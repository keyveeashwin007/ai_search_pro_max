"""Brand name is returned alongside the store name (merchant_name) on every path."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from txn_enrich.activity import name_with_brand, render_activity
from txn_enrich.cache import EnrichmentCache
from txn_enrich.config import Settings
from txn_enrich.enricher import Enricher, ProgressEvent
from txn_enrich.llm import FakeProvider
from txn_enrich.llm.base import SearchRequest, TriageItem
from txn_enrich.llm.openai_provider import OpenAIProvider
from txn_enrich.llm.prompts import SEARCH_SCHEMA, TRIAGE_SCHEMA
from txn_enrich.models import Enrichment, Transaction
from txn_enrich.rules import KnownMerchants

ROOT = Path(__file__).resolve().parents[1]


def make(provider, tmp_path, known=None):
    s = Settings().with_overrides(cache_path=str(tmp_path / "c.db"))
    cache = EnrichmentCache(s.cache_path, s.cache_ttl_days)
    return Enricher(provider, s, cache=cache, known=known or KnownMerchants([])), cache


class _Responses:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _client(data, output=()):
    usage = NS(input_tokens=100, output_tokens=20, input_tokens_details=NS(cached_tokens=0))
    return NS(responses=_Responses(NS(output_text=json.dumps(data), output=list(output),
                                      usage=usage)))


def test_schemas_require_brand():
    triage_item = TRIAGE_SCHEMA["properties"]["results"]["items"]
    assert "brand" in triage_item["properties"] and "brand" in triage_item["required"]
    assert "brand" in SEARCH_SCHEMA["properties"] and "brand" in SEARCH_SCHEMA["required"]


def test_openai_triage_and_search_parse_brand():
    row = {"i": 0, "merchant_name": "Starbucks HSR Layout", "brand": "Starbucks",
           "category": "Food & Dining", "subcategory": None, "website": None,
           "merchant_type": "physical", "location": None, "confidence": 0.9,
           "needs_search": False, "search_query": None}
    p = OpenAIProvider(model="m", client=_client({"results": [row]}))
    results, _ = p.triage([TriageItem(idx=0, key="starbucks hsr", raw="STARBUCKS HSR")])
    assert results[0].enrichment.merchant_name == "Starbucks HSR Layout"
    assert results[0].enrichment.brand == "Starbucks"

    data = {k: v for k, v in row.items() if k not in ("i", "needs_search", "search_query")}
    data.update(merchant_name="JKLM Studio Bandra", brand="JKLM Studio")
    p = OpenAIProvider(model="m", client=_client(data))
    outcome, _ = p.search_enrich(SearchRequest(key="jklm", raw="PAYPAL *JKLM", query="jklm",
                                               known=Enrichment()))
    assert outcome.enrichment.brand == "JKLM Studio"


def test_rules_default_brand_to_merchant_name_or_use_explicit_brand():
    km = KnownMerchants.from_yaml(ROOT / "data" / "known_merchants.yaml")
    assert km.lookup("starbucks").brand == "Starbucks"
    assert km.lookup("amazon prime").brand == "Amazon"  # explicit brand in the YAML
    assert km.lookup("amazon prime").merchant_name == "Amazon Prime"


def test_brand_in_rows_for_every_path(tmp_path):
    known = KnownMerchants([{"match": ["starbucks"], "merchant_name": "Starbucks"}])
    fake = FakeProvider(known={"blue bottle": {"merchant_name": "Blue Bottle Coffee Oakland",
                                               "brand": "Blue Bottle Coffee"}})
    enricher, _ = make(fake, tmp_path, known)
    result = enricher.run([Transaction(id="1", raw="STARBUCKS #123"),
                           Transaction(id="2", raw="SQ *BLUE BOTTLE #1"),
                           Transaction(id="3", raw="LOCALSHOPAA BAKERY")])
    rows = {r["id"]: r for r in result.rows()}
    assert all("brand" in r for r in rows.values())
    assert rows["1"]["brand"] == "Starbucks"  # rule
    assert rows["2"]["brand"] == "Blue Bottle Coffee"  # triage
    assert rows["3"]["brand"] == "Localshopaa Bakery"  # search (fake)
    assert list(rows["1"]).index("brand") == list(rows["1"]).index("merchant_name") + 1

    again = enricher.run([Transaction(id="2", raw="SQ *BLUE BOTTLE #1")])
    assert again.records[0].path == "cache"
    assert again.records[0].enrichment.brand == "Blue Bottle Coffee"


def test_old_cache_entries_without_brand_are_looked_up_again(tmp_path):
    fake = FakeProvider(known={"blue bottle": {"merchant_name": "Blue Bottle Coffee",
                                               "brand": "Blue Bottle Coffee"}})
    enricher, cache = make(fake, tmp_path)
    cache.set("blue bottle", {"enrichment": {"merchant_name": "Blue Bottle Coffee"},
                              "confidence": 0.9, "sources": [], "path": "llm"})
    rec = enricher.run([Transaction(id="1", raw="SQ *BLUE BOTTLE #1")]).records[0]
    assert rec.path == "llm" and rec.enrichment.brand == "Blue Bottle Coffee"
    assert "brand" in cache.get("blue bottle")["enrichment"]  # refreshed entry


def test_activity_shows_brand_only_when_it_differs():
    assert name_with_brand("Starbucks", "Starbucks") == "Starbucks"
    assert name_with_brand("Starbucks HSR", "Starbucks") == "Starbucks HSR (brand: Starbucks)"
    assert name_with_brand(None, None) == "?"
    text = render_activity([
        ProgressEvent("search_start", "", "jklm", {"query": "jklm"}),
        ProgressEvent("search_done", "", "jklm", {"name": "JKLM Studio Bandra",
                                                   "brand": "JKLM Studio", "confidence": 0.8,
                                                   "searches": 1}),
    ])
    assert "JKLM Studio Bandra (brand: JKLM Studio)" in text
