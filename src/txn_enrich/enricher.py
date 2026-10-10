"""Pipeline orchestrator.

normalize -> dedupe -> cache -> rules -> batched no-tools triage -> budgeted search -> cache
Each merchant (normalized + fuzzy-merged key) is processed at most once per run, no
matter how many transactions share it.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .cache import EnrichmentCache
from .config import Settings
from .inputs import parse_signatures  # noqa: F401  (re-exported for convenience)
from .llm.base import LLMProvider, SearchRequest, TriageItem
from .metrics import RunMetrics
from .models import EnrichedRecord, Enrichment, Transaction, TriageResult
from .normalize import normalize
from .planner import SearchCandidate, allocate_budget, cluster_keys
from .rules import KnownMerchants


@dataclass
class _Group:
    """All transactions that share one canonical merchant key."""

    key: str
    txns: list[Transaction] = field(default_factory=list)
    location_hint: Optional[str] = None

    @property
    def rep(self) -> Transaction:
        return self.txns[0]

    @property
    def total_amount(self) -> float:
        return sum(abs(t.amount or 0.0) for t in self.txns)


@dataclass
class _Resolution:
    enrichment: Enrichment
    confidence: float
    path: str
    sources: list[str] = field(default_factory=list)
    note: Optional[str] = None
    cacheable: bool = True


@dataclass
class ProgressEvent:
    """One step of a run, for live display (UI activity feed, logs).

    stage: start | local | triage_start | triage_done | plan | search_start | search_done |
           search_error | done
    """

    stage: str
    message: str
    key: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass
class RunResult:
    records: list[EnrichedRecord]
    metrics: RunMetrics
    events: list[ProgressEvent] = field(default_factory=list)

    def rows(self) -> list[dict]:
        return [r.to_row() for r in self.records]


class Enricher:
    def __init__(
        self,
        provider: LLMProvider,
        settings: Settings,
        cache: Optional[EnrichmentCache] = None,
        known: Optional[KnownMerchants] = None,
        max_workers: int = 4,
    ):
        self.provider = provider
        self.settings = settings
        self.cache = cache if cache is not None else EnrichmentCache(
            settings.cache_path, settings.cache_ttl_days
        )
        self.known = known if known is not None else KnownMerchants.from_yaml(
            settings.known_merchants_path
        )
        self.max_workers = max_workers

    # ------------------------------------------------------------------ public
    def run(
        self, transactions: Iterable[Transaction], on_event: Optional[ProgressCallback] = None
    ) -> RunResult:
        """Enrich transactions. `on_event` (optional) receives ProgressEvents as the run goes;
        it is always called from the calling thread, never from search worker threads."""
        self._events: list[ProgressEvent] = []
        self._on_event = on_event
        txns = [t for t in transactions if t.raw and t.raw.strip()]
        metrics = RunMetrics(records=len(txns))
        if not txns:
            return RunResult([], metrics)

        groups, txn_key = self._group(txns, metrics)
        self._emit("start", f"{len(txns)} signature(s) → {len(groups)} unique merchant(s)")
        resolved: dict[str, _Resolution] = {}

        pending = self._resolve_local(groups, resolved, metrics)
        self._emit(
            "local",
            f"cache: {metrics.cache_hits}, known rules: {metrics.rule_hits}, "
            f"left for the model: {len(pending)}",
        )
        candidates = self._triage(pending, groups, resolved, metrics)
        self._search(candidates, groups, resolved, metrics)

        for key, res in resolved.items():
            if res.cacheable:
                self.cache.set(key, self._to_cache(res))

        metrics.finalize_cost(self.settings)
        self._emit("done", f"done: {metrics.web_searches} web search(es), "
                           f"{metrics.llm_calls} model call(s)")
        records = [self._record(t, txn_key[t.id], resolved[txn_key[t.id]]) for t in txns]
        return RunResult(records, metrics, list(self._events))

    def _emit(self, stage: str, message: str, key: Optional[str] = None, **detail) -> None:
        event = ProgressEvent(stage, message, key, detail)
        self._events.append(event)
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # a display problem must never break the enrichment run
                pass

    # ------------------------------------------------------------- stage 1: dedupe
    def _group(self, txns: list[Transaction], metrics: RunMetrics):
        norm = {t.id: normalize(t.raw) for t in txns}
        counts: dict[str, int] = defaultdict(int)
        for t in txns:
            counts[norm[t.id].key] += 1
        metrics.unique_merchants = len(counts)

        canonical = cluster_keys(counts, self.settings.fuzzy_dedupe_threshold)
        metrics.fuzzy_merged = sum(1 for k, c in canonical.items() if k != c)

        groups: dict[str, _Group] = {}
        txn_key: dict[str, str] = {}
        for t in txns:
            n = norm[t.id]
            key = canonical[n.key]
            g = groups.setdefault(key, _Group(key=key))
            g.txns.append(t)
            if g.location_hint is None and n.location_hint:
                g.location_hint = n.location_hint
            txn_key[t.id] = key
        return groups, txn_key

    # ---------------------------------------------- stage 2: cache + rules (0 tokens)
    def _resolve_local(self, groups, resolved, metrics) -> list[str]:
        pending = []
        for key in groups:
            cached = self.cache.get(key)
            # Entries saved before the `brand` field existed are treated as misses (looked up
            # again once), so every result carries a brand.
            if cached is not None and "brand" in (cached.get("enrichment") or {}):
                resolved[key] = _Resolution(
                    enrichment=Enrichment.model_validate(cached.get("enrichment", {})),
                    confidence=float(cached.get("confidence", 0.0)),
                    path="cache",
                    sources=list(cached.get("sources", [])),
                    note=f"originally: {cached.get('path', '?')}",
                    cacheable=False,
                )
                metrics.cache_hits += 1
                continue
            rule = self.known.lookup(key)
            if rule is not None:
                resolved[key] = _Resolution(enrichment=rule, confidence=1.0, path="rule")
                metrics.rule_hits += 1
                continue
            pending.append(key)
        return pending

    # -------------------------------------- stage 3: batched triage (no web access)
    def _triage(self, pending, groups, resolved, metrics) -> list[tuple[str, TriageResult]]:
        metrics.sent_to_llm = len(pending)
        candidates: list[tuple[str, TriageResult]] = []
        threshold = self.settings.confidence_threshold
        size = max(1, self.settings.batch_size)

        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            items = []
            for i, key in enumerate(batch):
                rep = groups[key].rep
                items.append(
                    TriageItem(
                        idx=i,
                        key=key,
                        raw=rep.raw,
                        location_hint=groups[key].location_hint,
                        mcc=rep.mcc,
                        country=rep.country,
                        amount=rep.amount,
                    )
                )
            self._emit(
                "triage_start",
                f"asking the model about {len(batch)} merchant(s) (no web access)",
                keys=list(batch),
            )
            try:
                results, usage = self.provider.triage(items)
                metrics.add_usage(usage)
            except Exception as exc:  # keep going; these merchants can still be searched
                results = {}
                metrics.errors += 1
                err = f"triage failed: {exc}"[:200]
            else:
                err = None
            verdicts = []

            for i, key in enumerate(batch):
                tr = results.get(i) or TriageResult(
                    enrichment=Enrichment(), confidence=0.0, needs_search=True,
                    search_query=key,
                )
                # Confidence is the gate: a confident triage answer never triggers a search.
                if tr.confidence >= threshold:
                    resolved[key] = _Resolution(tr.enrichment, tr.confidence, "llm")
                    metrics.llm_resolved += 1
                else:
                    candidates.append((key, tr))
                    if err:
                        resolved[key] = _Resolution(
                            tr.enrichment, 0.0, "error", note=err, cacheable=False
                        )
                verdicts.append({
                    "key": key,
                    "name": tr.enrichment.merchant_name,
                    "brand": tr.enrichment.brand,
                    "confidence": tr.confidence,
                    "needs_search": tr.confidence < threshold,
                    "query": (tr.search_query or key).strip()[:120],
                })
            sure = sum(1 for v in verdicts if not v["needs_search"])
            self._emit(
                "triage_done",
                err or f"model is sure about {sure}, unsure about {len(verdicts) - sure} "
                       f"(threshold {threshold:.2f})",
                verdicts=verdicts, error=err,
            )
        metrics.search_candidates = len(candidates)
        return candidates

    # --------------------------------------------- stage 4: budgeted web search
    def _search(self, candidates, groups, resolved, metrics) -> None:
        if not candidates:
            return
        triage = dict(candidates)
        ranked = [
            SearchCandidate(
                key=key,
                confidence=tr.confidence,
                frequency=len(groups[key].txns),
                total_amount=groups[key].total_amount,
            )
            for key, tr in candidates
        ]
        to_search, skipped = allocate_budget(ranked, self.settings.max_searches_per_run)
        metrics.searches_planned = len(to_search)
        metrics.budget_exhausted = len(skipped)
        self._emit(
            "plan",
            f"{len(to_search)} merchant(s) will be web searched"
            + (f"; {len(skipped)} skipped (search budget used up)" if skipped else ""),
            searching=[c.key for c in to_search], skipped=[c.key for c in skipped],
        )

        for c in skipped:
            if c.key in resolved:  # already marked as triage error
                continue
            tr = triage[c.key]
            resolved[c.key] = _Resolution(
                tr.enrichment, tr.confidence, "budget_exhausted",
                note="search budget exhausted; triage answer kept", cacheable=False,
            )

        def work(key: str):
            tr = triage[key]
            group = groups[key]
            request = SearchRequest(
                key=key,
                raw=group.rep.raw,
                query=(tr.search_query or key).strip()[:120],
                known=tr.enrichment,
                location_hint=group.location_hint,
                country=group.rep.country,
            )
            return self.provider.search_enrich(request, self.settings.max_searches_per_merchant)

        keys = [c.key for c in to_search]
        if not keys:
            return
        for key in keys:
            query = (triage[key].search_query or key).strip()[:120]
            self._emit("search_start", f"searching the web for “{key}”", key,
                       query=query, raw=groups[key].rep.raw)
        # Events are emitted here, in the calling thread, as each search finishes.
        with ThreadPoolExecutor(max_workers=max(1, min(self.max_workers, len(keys)))) as pool:
            futures = {pool.submit(work, key): key for key in keys}
            for fut in as_completed(futures):
                key = futures[fut]
                tr = triage[key]
                try:
                    outcome, usage = fut.result()
                except Exception as exc:
                    metrics.errors += 1
                    note = f"search failed: {exc}"[:200]
                    resolved[key] = _Resolution(
                        tr.enrichment, tr.confidence, "error", note=note, cacheable=False,
                    )
                    self._emit("search_error", f"search failed for “{key}”", key, error=note)
                    continue
                metrics.add_usage(usage)
                merged = self._merge(tr.enrichment, outcome.enrichment)
                confidence = max(outcome.confidence, tr.confidence)
                resolved[key] = _Resolution(
                    merged, confidence, "llm+search", sources=outcome.sources,
                )
                self._emit(
                    "search_done",
                    f"found “{merged.merchant_name or key}” ({confidence:.0%} sure)",
                    key,
                    name=merged.merchant_name, brand=merged.brand, category=merged.category,
                    website=merged.website, confidence=confidence,
                    searches=usage.searches, queries=outcome.queries,
                    sources=outcome.sources,
                    tokens=usage.input_tokens + usage.output_tokens,
                    billed_usd=usage.billed_usd if usage.billed_calls else None,
                )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _merge(base: Enrichment, found: Enrichment) -> Enrichment:
        """Search evidence wins; triage fills any field search left empty."""
        data = base.model_dump()
        data.update({k: v for k, v in found.model_dump().items() if v is not None})
        return Enrichment.model_validate(data)

    @staticmethod
    def _to_cache(res: _Resolution) -> dict:
        return {
            "enrichment": res.enrichment.model_dump(),
            "confidence": res.confidence,
            "sources": res.sources,
            "path": res.path,
        }

    def _record(self, t: Transaction, key: str, res: _Resolution) -> EnrichedRecord:
        return EnrichedRecord(
            id=t.id,
            raw=t.raw,
            key=key,
            enrichment=res.enrichment,
            confidence=res.confidence,
            path=res.path,
            sources=res.sources,
            low_confidence=res.confidence < self.settings.confidence_threshold,
            note=res.note,
        )
