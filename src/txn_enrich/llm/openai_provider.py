"""OpenAI adapter (Responses API): GPT for reasoning + built-in `web_search` tool for search."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from ..models import CATEGORIES, Enrichment, TriageResult
from .base import SearchOutcome, SearchRequest, TriageItem, Usage
from .prompts import (
    SEARCH_SCHEMA,
    SEARCH_SYSTEM,
    TRIAGE_SCHEMA,
    TRIAGE_SYSTEM,
    search_user_prompt,
    triage_user_prompt,
)

_CATEGORY_LOOKUP = {c.lower(): c for c in CATEGORIES}
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class MissingAPIKeyError(RuntimeError):
    pass


# RouterPlus (https://app.routerplus.com/docs): OpenAI-compatible gateway. Its Responses API
# passes OpenAI models (gpt-4.1-mini, gpt-4o-mini, ...) through, hosted `web_search` included,
# and adds the exact charge as `usage.cost`. Keys look like `tm_vk_...`.
ROUTERPLUS_BASE_URL = "https://api.routerplus.com/v1"
_KEY_ENV_VARS = ("OPENAI_API_KEY", "TM_API_KEY")


def resolve_connection(
    api_key: Optional[str] = None, base_url: Optional[str] = None
) -> tuple[Optional[str], Optional[str]]:
    """Pick the key and endpoint: OPENAI_API_KEY or TM_API_KEY; OPENAI_BASE_URL if set.

    A RouterPlus key (`tm_vk_...`) with no base URL goes to RouterPlus automatically.
    """
    key = api_key or next((os.getenv(v) for v in _KEY_ENV_VARS if os.getenv(v)), None)
    url = base_url or os.getenv("OPENAI_BASE_URL") or None
    if key and not url and key.startswith("tm_vk_"):
        url = ROUTERPLUS_BASE_URL
    return key, url


def gateway_label(base_url: Optional[str]) -> str:
    if not base_url:
        return "OpenAI"
    return "RouterPlus" if "routerplus" in base_url else base_url


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = _JSON_BLOCK.search(text or "")
        if not match:
            raise ValueError(f"Model did not return JSON: {(text or '')[:200]!r}")
        return json.loads(match.group(0))


def _canonical_category(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _CATEGORY_LOOKUP.get(value.strip().lower(), "Other")


def _usage(response: Any, searches: int = 0) -> Usage:
    u = getattr(response, "usage", None)
    cached = 0
    details = getattr(u, "input_tokens_details", None) if u is not None else None
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    # Gateways such as RouterPlus add the exact USD charge (search fees included) as usage.cost.
    cost = getattr(u, "cost", None) if u is not None else None
    if cost is None and u is not None and isinstance(getattr(u, "model_extra", None), dict):
        cost = u.model_extra.get("cost")
    try:
        cost = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost = None
    return Usage(
        calls=1,
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        cached_input_tokens=cached,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        searches=searches,
        billed_usd=cost or 0.0,
        billed_calls=1 if cost is not None else 0,
    )


def _enrichment_from(data: dict) -> Enrichment:
    e = Enrichment.model_validate(data)
    return e.model_copy(update={"category": _canonical_category(e.category)})


def _search_queries(response: Any) -> list[str]:
    """Query strings the model actually searched for (`action.query` / `action.queries`).

    Seen live via RouterPlus: `action.query` can echo the whole JSON prompt instead of a search
    phrase. Such values (JSON-looking or very long) are skipped; the UI then shows the planned query.
    """
    queries: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "web_search_call":
            continue
        action = getattr(item, "action", None)
        found = [getattr(action, "query", None)] + list(getattr(action, "queries", None) or [])
        for q in found:
            if not isinstance(q, str):
                continue
            q = q.strip()
            if q and len(q) <= 200 and q[0] not in "{[" and q not in queries:
                queries.append(q)
    return queries


def _collect_search_output(response: Any) -> tuple[int, list[str]]:
    """Count billed searches and gather source URLs (cited ones first).

    Only `search` actions incur the per-call fee; `open_page` / `find_in_page` (reasoning
    models) do not, so they are not counted. An item without an action type counts as a search.
    """
    searches = 0
    cited: list[str] = []
    seen: list[str] = []
    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "web_search_call":
            action = getattr(item, "action", None)
            if getattr(action, "type", None) in (None, "search"):
                searches += 1
            for src in getattr(action, "sources", None) or []:
                url = getattr(src, "url", None)
                if url and url not in seen:
                    seen.append(url)
        elif item_type == "message":
            for part in getattr(item, "content", None) or []:
                for ann in getattr(part, "annotations", None) or []:
                    url = getattr(ann, "url", None)
                    if url and url not in cited:
                        cited.append(url)
    sources = cited + [u for u in seen if u not in cited]
    return searches, sources


# Optional request features that some models/accounts may reject. On a 400 naming one of
# them, the provider drops it and retries (once per feature, remembered for later calls).
# `max_tool_calls` is a cost guard: without it the prompt still asks for one search and
# actual searches are still counted, so the metrics stay accurate.
_OPTIONAL_SEARCH_FEATURES = ("max_tool_calls", "include", "search_context_size", "user_location")


def _rejected_feature(exc: Exception, dropped: set[str]) -> Optional[str]:
    if getattr(exc, "status_code", None) != 400:
        return None
    # Prefer the structured `param` (e.g. "tools[0].search_context_size"); fall back to the message.
    text = getattr(exc, "param", None) or str(exc)
    for feature in _OPTIONAL_SEARCH_FEATURES:
        if feature not in dropped and feature in text:
            return feature
    return None


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4.1-mini",
        search_context_size: str = "low",
        api_key: Optional[str] = None,
        client: Any = None,
        base_url: Optional[str] = None,
    ):
        self.model = model
        self.search_context_size = search_context_size
        self.base_url = base_url
        if client is None:
            key, self.base_url = resolve_connection(api_key, base_url)
            if not key:
                raise MissingAPIKeyError(
                    "No API key: set OPENAI_API_KEY (or TM_API_KEY for RouterPlus) in the "
                    ".env file in the project folder."
                )
            from openai import OpenAI

            client = OpenAI(api_key=key, base_url=self.base_url)
        self.client = client
        self.gateway = gateway_label(self.base_url)
        self.dropped_features: set[str] = set()  # optional params the API rejected

    def check(self) -> str:
        """Preflight (no tokens): the key works and the model is in the endpoint's model list.

        Uses GET /models (list) because gateways such as RouterPlus have no per-model route.
        """
        ids = [getattr(m, "id", None) for m in self.client.models.list()]
        if self.model not in ids:
            close = [i for i in ids if i and ("gpt" in i or self.model.split("-")[0] in i)]
            raise ValueError(
                f"model {self.model!r} is not available on {self.gateway}. "
                f"Some available: {', '.join(sorted(close)[:10]) or 'none'}"
            )
        return self.model

    # ---- Stage 1: triage with NO tools attached -> the model cannot search here ----
    def triage(self, items: list[TriageItem]) -> tuple[dict[int, TriageResult], Usage]:
        response = self.client.responses.create(
            model=self.model,
            instructions=TRIAGE_SYSTEM,
            input=triage_user_prompt([it.to_payload() for it in items]),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "triage",
                    "schema": TRIAGE_SCHEMA,
                    "strict": True,
                }
            },
            max_output_tokens=300 + 150 * len(items),
            store=False,
        )
        data = _parse_json(response.output_text)
        results: dict[int, TriageResult] = {}
        for row in data.get("results", []):
            try:
                idx = int(row.get("i"))
            except (TypeError, ValueError):
                continue
            results[idx] = TriageResult(
                enrichment=_enrichment_from(row),
                confidence=row.get("confidence", 0.0),
                needs_search=bool(row.get("needs_search", True)),
                search_query=row.get("search_query"),
            )
        return results, _usage(response)

    # ---- Stage 2: budgeted web search for ONE deduplicated merchant ----
    def search_enrich(
        self, request: SearchRequest, max_searches: int = 1
    ) -> tuple[SearchOutcome, Usage]:
        known = {k: v for k, v in request.known.model_dump().items() if v is not None}
        while True:
            try:
                response = self._create_search(request, known, max_searches)
                break
            except Exception as exc:
                feature = _rejected_feature(exc, self.dropped_features)
                if feature is None:
                    raise
                self.dropped_features.add(feature)

        searches, sources = _collect_search_output(response)
        data = _parse_json(response.output_text)
        outcome = SearchOutcome(
            enrichment=_enrichment_from(data),
            confidence=TriageResult(confidence=data.get("confidence", 0.0)).confidence,
            sources=sources[:5],
            queries=_search_queries(response),
        )
        return outcome, _usage(response, searches=searches)

    def _create_search(self, request: SearchRequest, known: dict, max_searches: int) -> Any:
        dropped = self.dropped_features
        tool: dict[str, Any] = {"type": "web_search"}
        if "search_context_size" not in dropped:
            tool["search_context_size"] = self.search_context_size
        if (
            "user_location" not in dropped
            and request.country
            and len(request.country.strip()) == 2
        ):
            tool["user_location"] = {
                "type": "approximate",
                "country": request.country.strip().upper(),
            }
        extra: dict[str, Any] = {}
        if "max_tool_calls" not in dropped:
            extra["max_tool_calls"] = max(1, max_searches)  # hard cap on searches per merchant
        if "include" not in dropped:
            extra["include"] = ["web_search_call.action.sources"]

        return self.client.responses.create(
            model=self.model,
            instructions=SEARCH_SYSTEM,
            input=search_user_prompt(
                request.key,
                request.raw,
                request.query,
                known,
                request.known.missing_fields(),
                request.location_hint,
            ),
            tools=[tool],
            tool_choice="required",
            text={
                "format": {
                    "type": "json_schema",
                    "name": "merchant",
                    "schema": SEARCH_SCHEMA,
                    "strict": True,
                }
            },
            max_output_tokens=800,
            store=False,
            **extra,
        )
