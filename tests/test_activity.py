"""Live search-activity feed: enricher progress events + the Markdown renderer."""

from types import SimpleNamespace as NS

from txn_enrich.activity import domain, md, render_activity, search_progress
from txn_enrich.cache import EnrichmentCache
from txn_enrich.config import Settings
from txn_enrich.enricher import Enricher, ProgressEvent
from txn_enrich.llm import FakeProvider
from txn_enrich.llm.openai_provider import _search_queries
from txn_enrich.models import Transaction
from txn_enrich.rules import KnownMerchants


def make(provider, tmp_path, **overrides):
    s = Settings().with_overrides(cache_path=str(tmp_path / "c.db"), **overrides)
    return Enricher(provider, s, cache=EnrichmentCache(s.cache_path, s.cache_ttl_days),
                    known=KnownMerchants([]))


def txns(*raws):
    return [Transaction(id=str(i), raw=r) for i, r in enumerate(raws)]


def test_events_stream_in_order_and_are_kept_on_result(tmp_path):
    fake = FakeProvider(known={"blue bottle": {"merchant_name": "Blue Bottle Coffee"}})
    seen: list[ProgressEvent] = []
    result = make(fake, tmp_path).run(
        txns("SQ *BLUE BOTTLE #1 OAKLAND CA", "LOCALSHOPAA BAKERY", "LOCALSHOPBB BAKERY"),
        on_event=seen.append,
    )
    stages = [e.stage for e in seen]
    assert stages[:4] == ["start", "local", "triage_start", "triage_done"]
    assert stages[4] == "plan" and stages[-1] == "done"
    assert stages.count("search_start") == 2 and stages.count("search_done") == 2
    # every search starts before any finishes (all are shown as running first)
    assert max(i for i, s in enumerate(stages) if s == "search_start") < stages.index("search_done")
    assert result.events == seen

    done = {e.key: e.detail for e in seen if e.stage == "search_done"}
    assert set(done) == {"localshopaa bakery", "localshopbb bakery"}
    d = done["localshopaa bakery"]
    assert d["searches"] == 1 and d["queries"] == ["localshopaa bakery business"]
    assert d["sources"] == ["https://localshopaabakery.example.com/"]
    verdicts = next(e for e in seen if e.stage == "triage_done").detail["verdicts"]
    assert [v["needs_search"] for v in verdicts] == [False, True, True]
    assert search_progress(seen) == (2, 2)


def test_budget_skip_and_search_error_events(tmp_path):
    class Boom(FakeProvider):
        def search_enrich(self, request, max_searches=1):
            raise RuntimeError("upstream 503")

    seen = []
    make(Boom(), tmp_path, max_searches_per_run=1).run(
        txns("LOCALSHOPAA BAKERY", "LOCALSHOPBB BAKERY"), on_event=seen.append)
    plan = next(e for e in seen if e.stage == "plan")
    assert len(plan.detail["searching"]) == 1 and len(plan.detail["skipped"]) == 1
    err = next(e for e in seen if e.stage == "search_error")
    assert "upstream 503" in err.detail["error"]
    text = render_activity(seen)
    assert "skipped: search budget used up" in text and "❌" in text and "upstream 503" in text


def test_broken_callback_never_breaks_the_run(tmp_path):
    def explode(_event):
        raise ValueError("display bug")

    result = make(FakeProvider(), tmp_path).run(txns("LOCALSHOPAA BAKERY"), on_event=explode)
    assert result.records[0].path == "llm+search"


def test_no_events_when_nothing_to_do(tmp_path):
    assert make(FakeProvider(), tmp_path).run([]).events == []


def test_render_running_then_finished():
    start = ProgressEvent("search_start", "", "kopi kopi", {"query": "kopi kopi roasters"})
    running = render_activity([start])
    assert "⏳" in running and "kopi kopi roasters" in running

    done = ProgressEvent("search_done", "", "kopi kopi", {
        "name": "Kopi Kopi", "category": "Food & Dining", "confidence": 0.8, "searches": 1,
        "queries": ["kopi kopi coffee"], "sources": ["https://www.kopikopi.com/about"],
        "website": "kopikopi.com", "billed_usd": 0.0138,
    })
    text = render_activity([start, done])
    assert "⏳" not in text and "✅" in text
    assert "[kopikopi.com](https://www.kopikopi.com/about)" in text
    assert "80% sure" in text and "1 search " in text and "\\$0.0138" in text
    assert "kopi kopi coffee" in text  # the query actually run is shown, not the planned one


def test_markdown_escaping_and_domain():
    assert md("SQ *BLUE_BOTTLE $5 [x]") == "SQ \\*BLUE\\_BOTTLE \\$5 \\[x\\]"
    assert domain("https://www.example.com/a?b=1") == "example.com"
    assert domain("not a url") == "not a url"


def test_search_queries_from_openai_output():
    response = NS(output=[
        NS(type="web_search_call", action=NS(type="search", query="kopi kopi coffee",
                                             queries=["kopi kopi coffee", "kopi kopi roastery"])),
        NS(type="web_search_call", action=NS(type="open_page", url="https://x.com")),
        NS(type="message", content=[]),
    ])
    assert _search_queries(response) == ["kopi kopi coffee", "kopi kopi roastery"]
    assert _search_queries(NS(output=None)) == []
    # Seen live via RouterPlus: the "query" was the whole JSON prompt -> ignored.
    echoed = NS(output=[NS(type="web_search_call",
                           action=NS(type="search", query='{"sig":"x","raw":"PAYPAL *X"}'))])
    assert _search_queries(echoed) == []


def test_source_links_strip_tracking_and_dedupe_domains():
    from txn_enrich.activity import clean_url, source_links

    assert clean_url("https://a.com/p?utm_source=openai") == "https://a.com/p"
    assert clean_url("https://a.com/p?x=1&utm_source=openai") == "https://a.com/p?x=1"
    links = source_links([
        "https://www.paypal.com/a?utm_source=openai",
        "https://paypal.com/b?utm_source=openai",
        "https://legalclarity.org/x?utm_source=openai",
    ])
    assert links == "[paypal.com](https://www.paypal.com/a), [legalclarity.org](https://legalclarity.org/x)"
