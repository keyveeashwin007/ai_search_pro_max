"""Known-merchant lookup: resolves well-known merchants with zero LLM tokens."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from .models import Enrichment


class KnownMerchants:
    def __init__(self, entries: list[dict] | None = None):
        self._patterns: list[tuple[str, Enrichment]] = []
        for entry in entries or []:
            enrichment = Enrichment.model_validate(entry)
            if enrichment.brand is None:  # big brands: the brand is the merchant name
                enrichment = enrichment.model_copy(update={"brand": enrichment.merchant_name})
            for pattern in entry.get("match", []):
                self._patterns.append((pattern.lower().strip(), enrichment))
        # Longest pattern first so "uber eats" wins over "uber".
        self._patterns.sort(key=lambda p: len(p[0]), reverse=True)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "KnownMerchants":
        p = Path(path)
        if not p.exists():
            return cls([])
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data.get("merchants", []))

    def lookup(self, key: str) -> Optional[Enrichment]:
        """Match when the normalized key equals a pattern or starts with it as whole words."""
        for pattern, enrichment in self._patterns:
            if key == pattern or key.startswith(pattern + " "):
                return enrichment
        return None

    def __len__(self) -> int:
        return len(self._patterns)
