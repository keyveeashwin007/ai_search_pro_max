"""Deterministic offline provider for tests and keyless demos. Makes no network calls."""

from __future__ import annotations

import re
import threading

from ..models import Enrichment, TriageResult
from .base import SearchOutcome, SearchRequest, TriageItem, Usage


class FakeProvider:
    name = "fake"

    def __init__(self, known: dict[str, dict] | None = None, search_confidence: float = 0.9):
        """`known` maps normalized keys the fake model "already knows" -> enrichment dict."""
        self.known = known or {}
        self.search_confidence = search_confidence
        self.triage_calls = 0
        self.search_calls = 0
        self.searched_keys: list[str] = []
        self.triage_batch_sizes: list[int] = []
        self._lock = threading.Lock()

    def triage(self, items: list[TriageItem]) -> tuple[dict[int, TriageResult], Usage]:
        with self._lock:
            self.triage_calls += 1
            self.triage_batch_sizes.append(len(items))
        results: dict[int, TriageResult] = {}
        for it in items:
            if it.key in self.known:
                results[it.idx] = TriageResult(
                    enrichment=Enrichment.model_validate(self.known[it.key]),
                    confidence=0.95,
                    needs_search=False,
                )
            else:
                results[it.idx] = TriageResult(
                    enrichment=Enrichment(merchant_name=it.key.title(), brand=it.key.title()),
                    confidence=0.3,
                    needs_search=True,
                    search_query=f"{it.key} business",
                )
        usage = Usage(calls=1, input_tokens=400 + 25 * len(items), output_tokens=40 * len(items))
        return results, usage

    def search_enrich(
        self, request: SearchRequest, max_searches: int = 1
    ) -> tuple[SearchOutcome, Usage]:
        with self._lock:
            self.search_calls += 1
            self.searched_keys.append(request.key)
        slug = re.sub(r"[^a-z0-9]+", "", request.key.lower()) or "merchant"
        outcome = SearchOutcome(
            enrichment=Enrichment(
                merchant_name=request.key.title(),
                brand=request.key.title(),
                category="Other",
                website=f"{slug}.example.com",
                merchant_type="unknown",
                location=request.location_hint,
            ),
            confidence=self.search_confidence,
            sources=[f"https://{slug}.example.com/"],
            queries=[request.query],
        )
        return outcome, Usage(calls=1, input_tokens=900, output_tokens=80, searches=1)
