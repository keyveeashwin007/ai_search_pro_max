"""Deterministic (no-LLM) cleanup of raw merchant signatures.

The normalized key is the unit of caching and de-duplication, so many raw
variants ("SQ *BLUE BOTTLE #123 OAKLAND CA", "BLUE BOTTLE 0456 BERKELEY CA")
collapse to one key ("blue bottle") and cost at most one LLM/search round.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Payment facilitators whose prefix precedes the real merchant: "SQ *MERCHANT".
PROCESSOR_PREFIXES = {
    "sq", "tst", "sp", "pp", "paypal", "pypl", "ic", "cko", "sumup", "zettle",
    "izettle", "stripe", "clover", "pos", "bt", "ec",
}

US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in",
    "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv",
    "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn",
    "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}

# First word of common two-word city names ("SAN FRANCISCO", "NEW YORK").
CITY_PREFIXES = {
    "san", "los", "las", "new", "st", "saint", "fort", "ft", "el", "santa", "salt",
    "palo", "mountain", "redwood", "long", "west", "east", "north", "south", "kansas",
    "oklahoma", "jersey", "daly", "menlo", "walnut", "half", "colorado", "virginia",
}

# Bank / network noise words that never identify a merchant.
NOISE_WORDS = {
    "pos", "debit", "credit", "purchase", "checkcard", "chkcard", "card", "visa",
    "mastercard", "recurring", "pmt", "payment", "auth", "ach", "dbt", "crd", "txn",
    "transaction", "contactless", "intl", "international", "fee", "inc", "llc", "ltd",
    "corp", "co", "pending", "help", "bill", "billing", "online", "autopay",
}

_RE_DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b")
_RE_PHONE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
_RE_HASHNUM = re.compile(r"#\s*\w+")
_RE_STORE = re.compile(r"\b(store|str|no|loc|terminal|term)\s*\d+\b", re.I)
_RE_LONGNUM = re.compile(r"\b\w*\d{3,}\w*\b")  # store ids, refs, card fragments
# Mixed letter+digit reference codes like "2K4AB1C23" (>= 6 chars).
_RE_REFCODE = re.compile(r"\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{6,}\b", re.I)
_RE_WWW = re.compile(r"\bwww\.", re.I)
_RE_TLD = re.compile(r"\.(com|net|org|co|io|us|uk|ca|app)\b", re.I)
_RE_NON_ALNUM = re.compile(r"[^a-z0-9&' ]+")
_RE_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class NormalizedSignature:
    key: str
    location_hint: Optional[str] = None


def _split_processor(text: str) -> str:
    """Handle '*' separators.

    'SQ *MERCHANT'      -> 'MERCHANT'        (payment-processor prefix dropped)
    'UBER *EATS'        -> 'UBER EATS'       (both sides are the merchant)
    'AMZN MKTP US*2K4X' -> 'AMZN MKTP US 2K4X' (reference code removed later)
    """
    if "*" not in text:
        return text
    left, right = text.split("*", 1)
    left_clean = left.strip().lower()
    if left_clean in PROCESSOR_PREFIXES and right.strip():
        return right.replace("*", " ")
    return f"{left} {right.replace('*', ' ')}"


def normalize(raw: str) -> NormalizedSignature:
    text = (raw or "").strip()
    if not text:
        return NormalizedSignature(key="")

    text = _split_processor(text)
    text = _RE_WWW.sub("", text)
    text = _RE_TLD.sub("", text)
    text = _RE_DATE.sub(" ", text)
    text = _RE_PHONE.sub(" ", text)
    text = _RE_HASHNUM.sub(" ", text)
    text = _RE_STORE.sub(" ", text)
    text = _RE_LONGNUM.sub(" ", text)
    text = _RE_REFCODE.sub(" ", text)
    text = _RE_NON_ALNUM.sub(" ", text.lower())
    tokens = [t for t in _RE_SPACES.split(text) if t]
    # Drop trailing noise first, but keep possible state codes (e.g. "co" = Colorado).
    while tokens and tokens[-1] in NOISE_WORDS and tokens[-1] not in US_STATES:
        tokens.pop()

    # Strip trailing "CITY ST" location (kept as a hint for the model).
    location_hint = None
    if len(tokens) >= 2 and tokens[-1] in US_STATES:
        state = tokens.pop()
        city_tokens: list[str] = []
        if len(tokens) >= 2:
            city_tokens.insert(0, tokens.pop())
            if len(tokens) >= 2 and tokens[-1] in CITY_PREFIXES:
                city_tokens.insert(0, tokens.pop())
        location_hint = " ".join(city_tokens + [state]).upper()

    tokens = [t for t in tokens if t not in NOISE_WORDS]
    key = " ".join(tokens).strip(" '&")
    if not key:  # never return an empty key; fall back to a simple lowercase form
        key = _RE_SPACES.sub(" ", _RE_NON_ALNUM.sub(" ", (raw or "").lower())).strip()
    return NormalizedSignature(key=key, location_hint=location_hint)
