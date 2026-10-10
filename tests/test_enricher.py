from pathlib import Path

import pytest

from txn_enrich.cache import EnrichmentCache
from txn_enrich.config import Settings
from txn_enrich.enricher import Enricher, parse_signatures
from txn_enrich.llm import FakeProvider
from txn_enrich.models import Transaction
from txn_enrich.rules import KnownMerchants

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def known():
    return KnownMerchants.from_yaml(ROOT / "data" / "known_merchants.yaml")


def make(provider, tmp_path, known=None, **overrides):
    settings = Settings().with_overrides(cache_path=str(tmp_path / "c.db"), **overrides)
    cache = EnrichmentCache(settings.cache_path, settings.cache_ttl_days)
    return Enricher(provider, settings, cache=cache, known=known or KnownMerchants([]))


def unknown_txns(n_rows=100, n_merchants=10):
    # Each merchant appears with different store numbers/cities -> same normalized key.
    names = [f"LOCALSHOP{chr(65 + i // 26)}{chr(65 + i % 26)} BAKERY" for i in range(n_merchants)]
    cities = ["OAKLAND CA", "AUSTIN TX", "DENVER CO", "BOSTON MA"]
    return [
        Transaction(id=str(i), raw=f"SQ *{names[i % n_merchants]} #{100 + i} {cities[i % 4]}")
        for i in range(n_rows)
    ]


def test_100_rows_10_merchants_at_most_10_searches(tmp_path):
    fake = FakeProvider()
    result = make(fake, tmp_path).run(unknown_txns(100, 10))
    m = result.metrics
    assert m.records == 100
    assert m.unique_merchants == 10
    assert fake.search_calls == 10 and m.web_searches == 10
    assert m.searches_avoided == 90
    assert fake.triage_calls == 1  # 10 merchants fit in one batch of 20
    assert len(result.records) == 100
    assert {r.path for r in result.records} == {"llm+search"}


def test_cost_estimate_includes_search_fees_and_cached_tokens():
    from txn_enrich.metrics import RunMetrics

    m = RunMetrics(input_tokens=1_000_000, cached_input_tokens=200_000,
                   output_tokens=100_000, web_searches=10)
    s = Settings().with_overrides(search_content_tokens_per_search=8000)
    m.finalize_cost(s)
    expected = (
        (800_000 + 10 * 8000) / 1e6 * 0.40  # uncached input + search content block
        + 200_000 / 1e6 * 0.10              # cached input
        + 100_000 / 1e6 * 1.60              # output
        + 10 * 0.01                         # per-search fee
    )
    assert m.est_cost_usd == pytest.approx(expected)


def test_second_run_is_fully_cached(tmp_path):
    fake = FakeProvider()
    enricher = make(fake, tmp_path)
    enricher.run(unknown_txns(30, 5))
    calls_before = (fake.triage_calls, fake.search_calls)
    result = enricher.run(unknown_txns(30, 5))
    assert (fake.triage_calls, fake.search_calls) == calls_before
    assert result.metrics.web_searches == 0 and result.metrics.llm_calls == 0
    assert result.metrics.cache_hits == 5
    assert {r.path for r in result.records} == {"cache"}


def test_rules_use_zero_llm_calls(tmp_path, known):
    fake = FakeProvider()
    txns = parse_signatures("STARBUCKS STORE 123 SEATTLE WA\nUBER *EATS PENDING\nNETFLIX.COM")
    result = make(fake, tmp_path, known=known).run(txns)
    assert fake.triage_calls == 0 and fake.search_calls == 0
    assert [r.enrichment.merchant_name for r in result.records] == [
        "Starbucks", "Uber Eats", "Netflix",
    ]
    assert result.metrics.rule_hits == 3


def test_confident_triage_skips_search(tmp_path):
    fake = FakeProvider(known={"blue bottle": {
        "merchant_name": "Blue Bottle Coffee", "category": "Food & Dining",
    }})
    result = make(fake, tmp_path).run(parse_signatures("SQ *BLUE BOTTLE #1 OAKLAND CA"))
    assert fake.search_calls == 0
    rec = result.records[0]
    assert rec.path == "llm" and rec.enrichment.merchant_name == "Blue Bottle Coffee"
    assert not rec.low_confidence


def test_search_budget_is_enforced(tmp_path):
    fake = FakeProvider()
    result = make(fake, tmp_path, max_searches_per_run=3).run(unknown_txns(20, 8))
    m = result.metrics
    assert fake.search_calls == 3 and m.web_searches == 3
    assert m.budget_exhausted == 5
    exhausted = [r for r in result.records if r.path == "budget_exhausted"]
    assert exhausted and all(r.low_confidence for r in exhausted)


def test_budget_exhausted_not_cached_so_later_run_can_search(tmp_path):
    fake = FakeProvider()
    enricher = make(fake, tmp_path, max_searches_per_run=0)
    enricher.run(unknown_txns(4, 2))
    assert fake.search_calls == 0
    enricher.settings = enricher.settings.with_overrides(max_searches_per_run=20)
    enricher.run(unknown_txns(4, 2))
    assert fake.search_calls == 2


def test_triage_batching(tmp_path):
    fake = FakeProvider()
    make(fake, tmp_path, batch_size=20).run(unknown_txns(45, 45))
    assert fake.triage_batch_sizes == [20, 20, 5]


def test_fuzzy_typo_merged_into_one_search(tmp_path):
    fake = FakeProvider()
    txns = parse_signatures("SQ *KOPI KOPI ROASTERS\nSQ *KOPI KOPI ROASTERS\nSQ *KOPI KOPI ROASTER")
    result = make(fake, tmp_path).run(txns)
    assert fake.search_calls == 1
    assert result.metrics.fuzzy_merged == 1
    assert len({r.key for r in result.records}) == 1


def test_search_failure_is_reported_not_raised(tmp_path):
    class Boom(FakeProvider):
        def search_enrich(self, request, max_searches=1):
            raise RuntimeError("network down")

    result = make(Boom(), tmp_path).run(parse_signatures("MYSTERY SHOP"))
    rec = result.records[0]
    assert rec.path == "error" and "network down" in rec.note
    assert result.metrics.errors == 1


def test_parse_signatures_skips_blanks_and_comments():
    txns = parse_signatures("# header\n\nSQ *A SHOP\n  \nB STORE\n")
    assert [t.raw for t in txns] == ["SQ *A SHOP", "B STORE"]
    assert [t.id for t in txns] == ["1", "2"]
