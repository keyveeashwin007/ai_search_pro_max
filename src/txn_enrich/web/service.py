"""State behind the web UI's HTTP routes: settings, caches and background enrichment jobs.

A run is a background job. The browser starts it and then follows its ProgressEvents live.
"""

from __future__ import annotations

import csv
import io
import threading
import uuid
from collections import OrderedDict
from typing import Any, Callable, Optional

from ..cache import EnrichmentCache
from ..config import Settings, demo_cache_path
from ..enricher import Enricher, ProgressEvent
from ..inputs import load_transactions, parse_signatures
from ..llm import FakeProvider, OpenAIProvider
from ..llm.openai_provider import resolve_connection
from ..models import Transaction

CONTEXT_SIZES = ("low", "medium", "high")
MAX_JOBS = 20

# provider_factory(kind, settings) -> provider ; kind is "openai" or "demo"
ProviderFactory = Callable[[str, Settings], Any]


def default_provider_factory(kind: str, settings: Settings) -> Any:
    if kind == "openai":
        return OpenAIProvider(settings.model, settings.search_context_size)
    return FakeProvider()


class Job:
    def __init__(self, provider_kind: str, records: int):
        self.id = uuid.uuid4().hex[:12]
        self.provider_kind = provider_kind
        self.records = records
        self.events: list[dict] = []  # appended by the worker thread, read by the event stream
        self.status = "running"  # running | done | error (set AFTER the last event is appended)
        self.result: Optional[dict] = None
        self.error: Optional[str] = None


def event_dict(e: ProgressEvent) -> dict:
    return {"stage": e.stage, "message": e.message, "key": e.key, "detail": e.detail}


def _csv_safe(value: Any) -> Any:
    """Stop spreadsheet formula injection: web-sourced text starting with = + - @ is prefixed."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def rows_to_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_safe(v) for k, v in r.items()})
    return out.getvalue()


class Busy(Exception):
    """Too many runs in progress (TXN_MAX_RUNNING)."""


class Service:
    def __init__(self, base: Settings, provider_factory: Optional[ProviderFactory] = None,
                 max_rows: int = 1000, max_searches: int = 500, max_running: int = 0):
        self.base = base
        self.factory = provider_factory or default_provider_factory
        self.max_rows = max_rows
        self.max_searches = max_searches  # ceiling for the client's "max web searches"
        self.max_running = max_running  # 0 = unlimited
        self.jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._caches: dict[str, EnrichmentCache] = {}
        self._lock = threading.Lock()

    def cache_for(self, kind: str) -> EnrichmentCache:
        """Real and demo (fake) results live in separate cache files, as in the Streamlit app."""
        path = self.base.cache_path if kind == "openai" else demo_cache_path(self.base.cache_path)
        with self._lock:
            if path not in self._caches:
                self._caches[path] = EnrichmentCache(path, self.base.cache_ttl_days)
            return self._caches[path]

    def settings_from(self, raw: dict) -> Settings:
        """Per-run overrides from the browser, clamped to safe ranges."""

        def num(name, default, lo, hi, cast):
            try:
                return max(lo, min(hi, cast(raw.get(name, default))))
            except (TypeError, ValueError):
                return default

        base = self.base
        ctx = raw.get("context_size")
        return base.with_overrides(
            model=(str(raw.get("model") or base.model).strip()[:100] or base.model),
            confidence_threshold=num("threshold", base.confidence_threshold, 0.0, 1.0, float),
            max_searches_per_run=num("budget", base.max_searches_per_run, 0, self.max_searches, int),
            batch_size=num("batch", base.batch_size, 1, 50, int),
            search_context_size=ctx if ctx in CONTEXT_SIZES else base.search_context_size,
        )

    def transactions_from(self, body: dict) -> list[Transaction]:
        ctx = body.get("context") or {}
        amount_text = str(ctx.get("amount") or "").strip()
        try:
            amount = float(amount_text) if amount_text else None
        except ValueError:
            amount = None
        mcc = str(ctx.get("mcc") or "").strip()[:4] or None
        country = str(ctx.get("country") or "").strip()[:2] or None
        txns = parse_signatures(str(body.get("text") or ""))
        for t in txns:
            t.amount, t.mcc, t.country = amount, mcc, country
        f = body.get("file")
        if f:
            extra = load_transactions(str(f.get("data") or ""), str(f.get("name") or ""))
            typed = {t.id for t in txns}
            for t in extra:
                if t.id in typed:
                    t.id = f"file-{t.id}"
            txns += extra
        return txns

    def start(self, body: dict) -> Job:
        """Validate and start a background run. Raises ValueError with a user-readable message."""
        kind = "openai" if body.get("provider") == "openai" else "demo"
        if kind == "openai" and not resolve_connection()[0]:
            raise ValueError(
                "No API key on the server. Add OPENAI_API_KEY or TM_API_KEY to .env and "
                "restart, or switch to the offline demo."
            )
        txns = self.transactions_from(body)
        if not txns:
            raise ValueError("Add at least one merchant signature (or upload a file).")
        if len(txns) > self.max_rows:
            raise ValueError(f"Too many rows ({len(txns)}); the limit is {self.max_rows}.")
        settings = self.settings_from(body.get("settings") or {})
        provider = self.factory(kind, settings)
        enricher = Enricher(provider, settings, cache=self.cache_for(kind))
        job = Job(kind, len(txns))
        with self._lock:
            running = sum(1 for j in self.jobs.values() if j.status == "running")
            if self.max_running and running >= self.max_running:
                raise Busy("The server is busy with another run. Try again in a moment.")
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_JOBS:
                self.jobs.popitem(last=False)
        threading.Thread(target=self._run, args=(job, enricher, provider, txns),
                         daemon=True).start()
        return job

    @staticmethod
    def _run(job: Job, enricher: Enricher, provider: Any, txns: list[Transaction]) -> None:
        try:
            res = enricher.run(txns, on_event=lambda e: job.events.append(event_dict(e)))
            job.result = {
                "rows": res.rows(),
                "metrics": res.metrics.to_dict(),
                "dropped": sorted(getattr(provider, "dropped_features", set())),
                "provider": provider.name,
            }
            job.status = "done"
        except Exception as exc:  # reported to the browser, never a stack trace
            job.error = f"{type(exc).__name__}: {exc}"[:300]
            job.status = "error"

