"""Load transactions from typed text, CSV or JSONL (shared by the UI and the CLI)."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from .models import Transaction

SIGNATURE_COLUMNS = ("description", "raw", "signature", "merchant")


def parse_signatures(text: str, start: int = 1) -> list[Transaction]:
    """One signature per line; blank lines and '#' comments ignored."""
    txns = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            txns.append(Transaction(id=str(start + len(txns)), raw=line))
    return txns


def _opt(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _rows_to_transactions(rows: list[dict]) -> list[Transaction]:
    if not rows:
        return []
    columns: dict[str, str] = {}
    for row in rows:  # JSONL rows may have different keys; take the union
        for k in row.keys():
            if k and k.strip().lower() not in columns:
                columns[k.strip().lower()] = k
    col = next((columns[c] for c in SIGNATURE_COLUMNS if c in columns), None)
    if col is None:
        raise ValueError(
            "File needs a 'description', 'raw', 'signature' or 'merchant' column."
        )

    def get(row: dict, name: str) -> Optional[str]:
        key = columns.get(name)
        return _opt(row.get(key)) if key else None

    txns = []
    for i, row in enumerate(rows, start=1):
        raw = _opt(row.get(col))
        if not raw:
            continue
        amount = get(row, "amount")
        try:
            amount_f = float(amount) if amount is not None else None
        except ValueError:
            amount_f = None
        txns.append(
            Transaction(
                id=get(row, "id") or str(i),
                raw=raw,
                amount=amount_f,
                mcc=get(row, "mcc"),
                country=get(row, "country"),
            )
        )
    return txns


def load_transactions(data: str, filename: str = "") -> list[Transaction]:
    """CSV (.csv), JSONL/JSON (.jsonl/.json), otherwise one signature per line."""
    name = filename.lower()
    if name.endswith(".csv"):
        return _rows_to_transactions(list(csv.DictReader(io.StringIO(data))))
    if name.endswith((".jsonl", ".json")):
        stripped = data.strip()
        if stripped.startswith("["):
            rows = json.loads(stripped)
        else:
            rows = [json.loads(line) for line in data.splitlines() if line.strip()]
        return _rows_to_transactions(rows)
    return parse_signatures(data)
