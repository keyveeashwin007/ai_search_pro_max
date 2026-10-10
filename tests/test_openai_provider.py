"""OpenAI adapter tested against a stub client: verifies request shape and parsing offline."""

import json
from types import SimpleNamespace as NS

import pytest

from txn_enrich.llm import MissingAPIKeyError, OpenAIProvider
from txn_enrich.llm.base import SearchRequest, TriageItem
from txn_enrich.models import Enrichment


class StubResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def stub_client(response):
    return NS(responses=StubResponses(response))


def usage(i=100, o=20, cached=0):
    return NS(input_tokens=i, output_tokens=o, input_tokens_details=NS(cached_tokens=cached))


def test_triage_sends_no_tools_and_parses():
    payload = {"results": [{
        "i": 0, "merchant_name": "Blue Bottle Coffee", "category": "food & dining",
        "subcategory": "Coffee Shop", "website": None, "merchant_type": "physical",
        "location": None, "confidence": 0.9, "needs_search": False, "search_query": None,
    }]}
    client = stub_client(NS(output_text=json.dumps(payload), output=[], usage=usage()))
    p = OpenAIProvider(model="m", client=client)
    results, u = p.triage([TriageItem(idx=0, key="blue bottle", raw="SQ *BLUE BOTTLE")])

    call = client.responses.calls[0]
    assert "tools" not in call  # triage can never search
    assert call["text"]["format"]["type"] == "json_schema"
    assert results[0].enrichment.category == "Food & Dining"  # canonicalised
    assert results[0].confidence == 0.9
    assert u.input_tokens == 100 and u.searches == 0


def test_search_caps_tool_calls_and_counts_searches():
    data = {
        "merchant_name": "Kopi Kopi", "category": "Made Up Category", "subcategory": None,
        "website": "kopikopi.com", "merchant_type": "physical", "location": "Austin, TX",
        "confidence": 0.85,
    }
    output = [
        NS(type="web_search_call", action=NS(sources=[NS(url="https://a.com"), NS(url="https://b.com")])),
        NS(type="message", content=[NS(annotations=[NS(url="https://b.com")])]),
    ]
    client = stub_client(NS(output_text=json.dumps(data), output=output, usage=usage(800, 60)))
    p = OpenAIProvider(model="m", client=client)
    req = SearchRequest(key="kopi kopi", raw="SQ *KOPI KOPI", query="kopi kopi austin",
                        known=Enrichment(merchant_name="Kopi Kopi"), country="us")
    outcome, u = p.search_enrich(req, max_searches=1)

    call = client.responses.calls[0]
    assert call["max_tool_calls"] == 1
    assert call["tool_choice"] == "required"
    assert call["tools"][0]["type"] == "web_search"
    assert call["tools"][0]["user_location"]["country"] == "US"
    assert u.searches == 1
    assert outcome.sources[0] == "https://b.com"  # cited source first
    assert outcome.enrichment.category == "Other"  # unknown category -> Other
    assert outcome.confidence == 0.85


class FakeBadRequest(Exception):
    """Mimics openai.BadRequestError: status_code 400 and a `param` field."""

    status_code = 400

    def __init__(self, msg, param=None):
        super().__init__(msg)
        self.param = param


class RejectingResponses(StubResponses):
    """Rejects the listed params (as a real API might) until they are removed."""

    def __init__(self, response, rejected):
        super().__init__(response)
        self.rejected = rejected

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tool = (kwargs.get("tools") or [{}])[0]
        for name in self.rejected:
            if name in kwargs:
                raise FakeBadRequest(f"Unsupported parameter: '{name}'.", param=name)
            if name in tool:
                raise FakeBadRequest("Unsupported parameter.", param=f"tools[0].{name}")
        return self.response


def search_response():
    data = {"merchant_name": "X", "category": "Other", "subcategory": None, "website": None,
            "merchant_type": "unknown", "location": None, "confidence": 0.7}
    output = [
        NS(type="web_search_call", action=NS(type="search", sources=[])),
        NS(type="web_search_call", action=NS(type="open_page", sources=None)),  # not billed
    ]
    return NS(output_text=json.dumps(data), output=output, usage=usage(500, 40))


def test_search_drops_rejected_optional_params_and_remembers():
    responses = RejectingResponses(search_response(), ["max_tool_calls", "search_context_size"])
    p = OpenAIProvider(model="m", client=NS(responses=responses))
    req = SearchRequest(key="x", raw="X", query="x", known=Enrichment())

    outcome, u = p.search_enrich(req, max_searches=1)
    assert p.dropped_features == {"max_tool_calls", "search_context_size"}
    assert u.searches == 1  # open_page action is not a billed search
    last = responses.calls[-1]
    assert "max_tool_calls" not in last and "search_context_size" not in last["tools"][0]
    assert last["tool_choice"] == "required" and "include" in last

    n = len(responses.calls)
    p.search_enrich(req, max_searches=1)
    assert len(responses.calls) == n + 1  # remembered: no retries on later calls


def test_search_non_optional_error_is_raised():
    responses = RejectingResponses(search_response(), ["model"])  # e.g. bad model
    p = OpenAIProvider(model="m", client=NS(responses=responses))
    with pytest.raises(FakeBadRequest):
        p.search_enrich(SearchRequest(key="x", raw="X", query="x", known=Enrichment()), 1)
    assert len(responses.calls) == 1


def test_check_lists_models():
    client = NS(models=NS(list=lambda: [NS(id="gpt-4o-mini"), NS(id="gpt-4.1-mini")]))
    assert OpenAIProvider(model="gpt-4.1-mini", client=client).check() == "gpt-4.1-mini"
    with pytest.raises(ValueError, match="gpt-4o-mini"):
        OpenAIProvider(model="gpt-nope", client=client).check()


def test_routerplus_usage_cost_is_recorded():
    u = usage(800, 60)
    u.cost = 0.0123  # RouterPlus adds the exact charge here
    data = {"merchant_name": "X", "category": "Other", "subcategory": None, "website": None,
            "merchant_type": "unknown", "location": None, "confidence": 0.7}
    client = stub_client(NS(output_text=json.dumps(data), output=[], usage=u))
    _, got = OpenAIProvider(model="m", client=client).search_enrich(
        SearchRequest(key="x", raw="X", query="x", known=Enrichment()), 1)
    assert got.billed_usd == pytest.approx(0.0123) and got.billed_calls == 1

    _, plain = OpenAIProvider(model="m", client=stub_client(
        NS(output_text=json.dumps(data), output=[], usage=usage()))).search_enrich(
        SearchRequest(key="x", raw="X", query="x", known=Enrichment()), 1)
    assert plain.billed_calls == 0  # direct OpenAI: no cost field


@pytest.mark.parametrize(
    "env, expected_url",
    [
        ({"TM_API_KEY": "tm_vk_abc"}, "https://api.routerplus.com/v1"),
        ({"OPENAI_API_KEY": "tm_vk_abc"}, "https://api.routerplus.com/v1"),
        ({"OPENAI_API_KEY": "sk-abc"}, None),
        ({"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "https://x.example/v1"}, "https://x.example/v1"),
    ],
)
def test_resolve_connection(monkeypatch, env, expected_url):
    from txn_enrich.llm.openai_provider import resolve_connection

    for v in ("OPENAI_API_KEY", "TM_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    key, url = resolve_connection()
    assert key and url == expected_url


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TM_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        OpenAIProvider()
