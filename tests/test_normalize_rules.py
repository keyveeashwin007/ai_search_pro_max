from pathlib import Path

import pytest

from txn_enrich.normalize import normalize
from txn_enrich.planner import SearchCandidate, allocate_budget, cluster_keys
from txn_enrich.rules import KnownMerchants

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "raw, key, loc",
    [
        ("SQ *BLUE BOTTLE #123 OAKLAND CA", "blue bottle", "OAKLAND CA"),
        ("TST* JOES PIZZA 10/02 NEW YORK NY", "joes pizza", "NEW YORK NY"),
        ("STARBUCKS STORE 12345 SAN FRANCISCO CA", "starbucks", "SAN FRANCISCO CA"),
        ("AMZN Mktp US*2K4AB1C23", "amzn mktp us", None),
        ("UBER *EATS PENDING", "uber eats", None),
        ("PAYPAL *NETFLIX.COM 4029357733", "netflix", None),
        ("POS DEBIT WHOLEFDS MKT 10234 AUSTIN TX", "wholefds mkt", "AUSTIN TX"),
        ("KOPI KOPI COFFEE ROASTERS", "kopi kopi coffee roasters", None),
        ("SQ *MILE HIGH BAGELS #9 DENVER CO", "mile high bagels", "DENVER CO"),
        ("ACME WIDGETS INC", "acme widgets", None),
    ],
)
def test_normalize(raw, key, loc):
    n = normalize(raw)
    assert n.key == key
    assert n.location_hint == loc


def test_store_variants_share_one_key():
    keys = {normalize(s).key for s in [
        "SQ *BLUE BOTTLE #123 OAKLAND CA",
        "SQ *BLUE BOTTLE #77 BERKELEY CA",
        "BLUE BOTTLE 0456 SAN JOSE CA",
    ]}
    assert keys == {"blue bottle"}


def test_normalize_never_empty():
    assert normalize("12345").key
    assert normalize("").key == ""


def test_known_merchants_lookup():
    km = KnownMerchants.from_yaml(ROOT / "data" / "known_merchants.yaml")
    assert km.lookup("uber eats").merchant_name == "Uber Eats"  # longest pattern wins
    assert km.lookup("uber trip").merchant_name == "Uber"
    assert km.lookup("amzn mktp us").merchant_name == "Amazon"
    assert km.lookup("starbucksish") is None  # whole-word match only
    assert km.lookup("blue bottle") is None


def test_cluster_merges_typos_but_not_distinct_merchants():
    canon = cluster_keys({"blue bottle": 5, "blue botle": 1, "uber": 3, "uber eats": 2}, 90)
    assert canon["blue botle"] == "blue bottle"
    assert canon["uber eats"] == "uber eats"
    assert canon["uber"] == "uber"


def test_cluster_keeps_one_letter_different_merchants_apart():
    canon = cluster_keys({"localshopa bakery": 2, "localshopb bakery": 2, "joes pizza": 1,
                          "joes plaza": 1}, 90)
    assert len(set(canon.values())) == 4


def test_cluster_threshold_100_disables_fuzzy():
    canon = cluster_keys({"blue bottle": 5, "blue botle": 1}, 100)
    assert canon["blue botle"] == "blue botle"


def test_budget_prioritises_frequent_then_spend():
    cands = [
        SearchCandidate("rare", 0.1, frequency=1, total_amount=5),
        SearchCandidate("common", 0.5, frequency=10, total_amount=50),
        SearchCandidate("big", 0.5, frequency=1, total_amount=900),
    ]
    picked, skipped = allocate_budget(cands, 2)
    assert [c.key for c in picked] == ["common", "big"]
    assert [c.key for c in skipped] == ["rare"]
