"""Data models shared across the pipeline."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Shopping",
    "Travel",
    "Transportation",
    "Entertainment",
    "Subscriptions & Software",
    "Utilities & Telecom",
    "Health & Fitness",
    "Financial Services",
    "Education",
    "Home & Services",
    "Personal Care",
    "Government & Taxes",
    "Charity",
    "Other",
]

MERCHANT_TYPES = {"online", "physical", "both", "unknown"}

# Fields we try to fill for every merchant. Used to tell the model what is still missing.
ENRICHMENT_FIELDS = [
    "merchant_name",  # store name: the outlet as it trades (e.g. "Starbucks HSR Layout")
    "brand",  # consumer brand / chain behind the store (e.g. "Starbucks")
    "category",
    "subcategory",
    "website",
    "merchant_type",
    "location",
]


class Transaction(BaseModel):
    """One input row: a raw merchant signature plus optional context."""

    id: str
    raw: str
    amount: Optional[float] = None
    mcc: Optional[str] = None
    country: Optional[str] = None


class Enrichment(BaseModel):
    """The enriched merchant attributes."""

    model_config = ConfigDict(extra="ignore")

    merchant_name: Optional[str] = None  # store name
    brand: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    website: Optional[str] = None
    merchant_type: Optional[str] = None
    location: Optional[str] = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v or v.lower() in {"null", "none", "unknown", "n/a", "na"}:
                return None
        return v

    @field_validator("merchant_type")
    @classmethod
    def _merchant_type(cls, v):
        if v is None:
            return None
        v = v.lower()
        return v if v in MERCHANT_TYPES else "unknown"

    def missing_fields(self) -> list[str]:
        return [f for f in ENRICHMENT_FIELDS if getattr(self, f) is None]


class TriageResult(BaseModel):
    """Output of the no-tools triage pass for one merchant."""

    enrichment: Enrichment = Field(default_factory=Enrichment)
    confidence: float = 0.0
    needs_search: bool = True
    search_query: Optional[str] = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, v))


class EnrichedRecord(BaseModel):
    """Final output row for one input transaction."""

    id: str
    raw: str
    key: str
    enrichment: Enrichment
    confidence: float
    path: str  # cache | rule | llm | llm+search | budget_exhausted | error
    sources: list[str] = Field(default_factory=list)
    low_confidence: bool = False
    note: Optional[str] = None

    def to_row(self) -> dict:
        row = {"id": self.id, "raw_signature": self.raw, "normalized": self.key}
        row.update(self.enrichment.model_dump())
        row.update(
            {
                "confidence": round(self.confidence, 2),
                "path": self.path,
                "low_confidence": self.low_confidence,
                "sources": " ".join(self.sources),
                "note": self.note or "",
            }
        )
        return row
