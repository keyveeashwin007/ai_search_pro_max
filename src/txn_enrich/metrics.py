"""Per-run counters that make token and search savings visible."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import Settings
from .llm.base import Usage


@dataclass
class RunMetrics:
    records: int = 0
    unique_merchants: int = 0  # after normalization (exact)
    fuzzy_merged: int = 0  # extra keys folded into another merchant
    cache_hits: int = 0  # merchants served from cache
    rule_hits: int = 0  # merchants served from known-merchant rules
    sent_to_llm: int = 0  # merchants triaged by the model
    llm_resolved: int = 0  # triaged confidently -> no search needed
    search_candidates: int = 0
    searches_planned: int = 0
    budget_exhausted: int = 0
    errors: int = 0
    llm_calls: int = 0
    web_searches: int = 0  # actual searches reported by the provider
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    est_cost_usd: float = 0.0
    billed_usd: float = 0.0  # exact, from the endpoint (RouterPlus usage.cost)
    billed_calls: int = 0

    @property
    def billed_complete(self) -> bool:
        """True when every LLM call reported its exact charge."""
        return self.llm_calls > 0 and self.billed_calls == self.llm_calls

    @property
    def naive_searches(self) -> int:
        """What a search-every-record approach would have spent."""
        return self.records

    @property
    def searches_avoided(self) -> int:
        return max(0, self.naive_searches - self.web_searches)

    def add_usage(self, usage: Usage) -> None:
        self.llm_calls += usage.calls
        self.web_searches += usage.searches
        self.input_tokens += usage.input_tokens
        self.cached_input_tokens += usage.cached_input_tokens
        self.output_tokens += usage.output_tokens
        self.billed_usd = round(self.billed_usd + usage.billed_usd, 8)
        self.billed_calls += usage.billed_calls

    def finalize_cost(self, settings: Settings) -> None:
        """Estimate: uncached + cached input, output, per-search fee, search content tokens."""
        cached = min(self.cached_input_tokens, self.input_tokens)
        search_content = self.web_searches * settings.search_content_tokens_per_search
        self.est_cost_usd = round(
            (self.input_tokens - cached + search_content) / 1_000_000 * settings.price_input_per_1m
            + cached / 1_000_000 * settings.price_cached_input_per_1m
            + self.output_tokens / 1_000_000 * settings.price_output_per_1m
            + self.web_searches * settings.price_per_search,
            6,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["naive_searches"] = self.naive_searches
        d["searches_avoided"] = self.searches_avoided
        d["billed_complete"] = self.billed_complete
        return d
