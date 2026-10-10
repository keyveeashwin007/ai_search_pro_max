"""Provider-agnostic interface: one cheap no-tools triage call, one budgeted search call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..models import Enrichment, TriageResult


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    billed_usd: float = 0.0  # exact charge when the endpoint reports it (RouterPlus usage.cost)
    billed_calls: int = 0  # how many calls reported a charge

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.searches += other.searches
        self.billed_usd += other.billed_usd
        self.billed_calls += other.billed_calls


@dataclass
class TriageItem:
    """Compact per-merchant payload sent to the model (only what it needs)."""

    idx: int
    key: str
    raw: str
    location_hint: Optional[str] = None
    mcc: Optional[str] = None
    country: Optional[str] = None
    amount: Optional[float] = None

    def to_payload(self) -> dict:
        payload = {"i": self.idx, "sig": self.key, "raw": self.raw[:80]}
        if self.location_hint:
            payload["loc"] = self.location_hint
        if self.mcc:
            payload["mcc"] = self.mcc
        if self.country:
            payload["cc"] = self.country
        return payload


@dataclass
class SearchRequest:
    key: str
    raw: str
    query: str
    known: Enrichment
    location_hint: Optional[str] = None
    country: Optional[str] = None


@dataclass
class SearchOutcome:
    enrichment: Enrichment
    confidence: float
    sources: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)  # search queries actually run, if reported


class LLMProvider(Protocol):
    name: str

    def triage(self, items: list[TriageItem]) -> tuple[dict[int, TriageResult], Usage]:
        """Classify merchants WITHOUT web access. Returns results keyed by TriageItem.idx."""
        ...

    def search_enrich(
        self, request: SearchRequest, max_searches: int
    ) -> tuple[SearchOutcome, Usage]:
        """Enrich one merchant using at most `max_searches` web searches."""
        ...
