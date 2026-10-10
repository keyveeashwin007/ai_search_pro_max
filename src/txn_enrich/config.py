"""Settings loaded from config.yaml with environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Optional

import yaml


@dataclass(frozen=True)
class Settings:
    # Model
    model: str = "gpt-4.1-mini"
    search_context_size: str = "low"  # low | medium | high -> how much web content the model reads

    # Token / search optimization
    confidence_threshold: float = 0.8
    batch_size: int = 20
    max_searches_per_run: int = 20
    max_searches_per_merchant: int = 1
    fuzzy_dedupe_threshold: int = 90  # rapidfuzz ratio (0-100) for merging near-identical merchants

    # Cache
    cache_path: str = "cache.db"
    cache_ttl_days: int = 30

    # Cost estimate (USD) - gpt-4.1-mini standard tier, OpenAI pricing page (checked 2026-10-10)
    price_input_per_1m: float = 0.40
    price_cached_input_per_1m: float = 0.10
    price_output_per_1m: float = 1.60
    price_per_search: float = 0.01  # $10 / 1k web_search calls
    # Extra search-content tokens per search to add to the estimate. 0: verified that the API's
    # usage.input_tokens already includes them (a real run matched the billed cost exactly).
    search_content_tokens_per_search: int = 0

    known_merchants_path: str = "data/known_merchants.yaml"

    def with_overrides(self, **kwargs) -> "Settings":
        valid = {f.name for f in fields(self)}
        return replace(self, **{k: v for k, v in kwargs.items() if k in valid and v is not None})


def load_settings(path: Optional[str | Path] = None) -> Settings:
    """Load settings from YAML (if present).

    Env overrides: OPENAI_MODEL -> model, TXN_CACHE_PATH -> cache_path (used by tests so
    they never touch the real cache).
    """
    data: dict = {}
    if path and Path(path).exists():
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    settings = Settings().with_overrides(**data)
    env_model = os.getenv("OPENAI_MODEL")
    if env_model:
        settings = settings.with_overrides(model=env_model)
    env_cache = os.getenv("TXN_CACHE_PATH")
    if env_cache:
        settings = settings.with_overrides(cache_path=env_cache)
    return settings


def demo_cache_path(cache_path: str) -> str:
    """Separate cache file for the offline fake provider so fake data never reaches real runs."""
    p = Path(cache_path)
    return str(p.with_name(f"{p.stem}.demo{p.suffix or '.db'}"))
