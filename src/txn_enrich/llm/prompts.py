"""Prompts and JSON schemas. Kept short on purpose: every token here is paid per call."""

from __future__ import annotations

import json

from ..models import CATEGORIES

_CATEGORY_LIST = ", ".join(CATEGORIES)

TRIAGE_SYSTEM = f"""You enrich card-transaction merchant signatures. You have NO web access.
For each item ("sig" = cleaned name, "raw" = original descriptor, optional "loc", "mcc", "cc"):
- Fill what you know with certainty; use null when unsure. Never invent websites or locations.
- merchant_name: the store name (outlet as it trades, e.g. "Starbucks HSR Layout").
- brand: the consumer brand/chain behind it (e.g. "Starbucks"); for an independent shop,
  its own business name. Never a payment processor (PayPal, Square, Stripe).
- category must be one of: {_CATEGORY_LIST}.
- merchant_type: online | physical | both | unknown.
- confidence (0-1): probability that merchant_name AND category are correct.
  >=0.8 only for merchants you clearly recognise.
- needs_search: true only if a web search would materially improve the answer.
- search_query: when needs_search, a short query (<=8 words) that best identifies the business; else null.
Return one result per item, echoing "i"."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "merchant_name": {"type": ["string", "null"]},
                    "brand": {"type": ["string", "null"]},
                    "category": {"type": ["string", "null"]},
                    "subcategory": {"type": ["string", "null"]},
                    "website": {"type": ["string", "null"]},
                    "merchant_type": {"type": ["string", "null"]},
                    "location": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "needs_search": {"type": "boolean"},
                    "search_query": {"type": ["string", "null"]},
                },
                "required": [
                    "i", "merchant_name", "brand", "category", "subcategory", "website",
                    "merchant_type", "location", "confidence", "needs_search", "search_query",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

SEARCH_SYSTEM = f"""You identify the business behind a card-transaction merchant signature.
Run ONE web search using the suggested query (refine only if clearly better), then answer.
Use null for anything the results do not support. category must be one of: {_CATEGORY_LIST}.
merchant_name: the store name (outlet as it trades). brand: the consumer brand/chain behind it
(an independent shop's own name; never a payment processor such as PayPal or Square).
merchant_type: online | physical | both | unknown. website: the official domain only.
confidence (0-1): probability that merchant_name AND category are correct."""

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "merchant_name": {"type": ["string", "null"]},
        "brand": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "subcategory": {"type": ["string", "null"]},
        "website": {"type": ["string", "null"]},
        "merchant_type": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": [
        "merchant_name", "brand", "category", "subcategory", "website", "merchant_type",
        "location", "confidence",
    ],
    "additionalProperties": False,
}


def compact_json(obj) -> str:
    """JSON without whitespace: fewer input tokens."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def triage_user_prompt(payloads: list[dict]) -> str:
    return compact_json({"items": payloads})


def search_user_prompt(
    key: str, raw: str, query: str, known: dict, missing: list[str], location_hint: str | None
) -> str:
    payload = {"sig": key, "raw": raw[:80], "query": query}
    if location_hint:
        payload["loc"] = location_hint
    if known:
        payload["known"] = known
    if missing:
        payload["need"] = missing
    return compact_json(payload)
