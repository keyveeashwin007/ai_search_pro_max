from pathlib import Path

import pytest

from txn_enrich.inputs import load_transactions, parse_signatures

ROOT = Path(__file__).resolve().parents[1]


def test_sample_csv():
    txns = load_transactions((ROOT / "data" / "sample_transactions.csv").read_text(), "s.csv")
    assert len(txns) == 10
    assert txns[0].raw == "SQ *BLUE BOTTLE #123 OAKLAND CA"
    assert txns[0].amount == 6.5 and txns[0].mcc == "5814" and txns[0].country == "US"


def test_jsonl_and_json_array():
    jsonl = '{"id": "a", "raw": "SQ *X SHOP"}\n\n{"id": "b", "raw": "Y STORE", "amount": "3"}'
    txns = load_transactions(jsonl, "t.jsonl")
    assert [t.id for t in txns] == ["a", "b"] and txns[1].amount == 3.0
    arr = '[{"description": "Z CAFE"}]'
    assert load_transactions(arr, "t.json")[0].raw == "Z CAFE"


def test_missing_column_raises():
    with pytest.raises(ValueError):
        load_transactions("foo,bar\n1,2\n", "bad.csv")


def test_plain_text_and_comments():
    assert [t.raw for t in load_transactions("A SHOP\n# skip\nB SHOP", "x.txt")] == [
        "A SHOP", "B SHOP",
    ]
    assert parse_signatures("") == []
