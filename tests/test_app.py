"""Headless UI test with Streamlit AppTest using the offline fake provider."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_ui_enrich_offline(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TM_API_KEY", raising=False)
    monkeypatch.setenv("TXN_CACHE_PATH", str(tmp_path / "ui.db"))  # never touch real cache
    monkeypatch.chdir(tmp_path)
    real_db = Path(APP).parent / "cache.db"  # may hold genuine results from real runs
    real_before = real_db.read_bytes() if real_db.exists() else None
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception

    at.sidebar.radio[0].set_value("Offline demo (fake, no key)").run()
    at.text_area(key="sig_text").set_value(
        "SQ *BLUE BOTTLE #1 OAKLAND CA\nSQ *BLUE BOTTLE #2 BERKELEY CA\nSTARBUCKS STORE 9 SEATTLE WA"
    ).run()
    at.button(key="enrich").click().run()
    assert not at.exception

    result = at.session_state["result"]
    assert result.metrics.records == 3
    assert result.metrics.web_searches <= 1  # 2 blue bottle rows -> 1 merchant; starbucks = rule
    paths = {r.raw: r.path for r in result.records}
    assert paths["STARBUCKS STORE 9 SEATTLE WA"] in {"rule", "cache"}
    assert len(at.dataframe) >= 2  # stats funnel + results table

    # Search activity panel: kept after the run, shows the searched merchant and its source.
    assert any(e.label.startswith("🔎 Search activity") for e in at.expander)
    activity = "\n".join(m.value for m in at.markdown)
    assert "✅ **blue bottle**" in activity and "example.com" in activity
    assert at.status and at.status[0].state == "complete"

    # Fake data went to the temp demo cache only; the project's real cache is untouched.
    assert (tmp_path / "ui.demo.db").exists()
    assert (real_db.read_bytes() if real_db.exists() else None) == real_before


def test_demo_cache_path():
    from txn_enrich.config import demo_cache_path

    assert demo_cache_path("/x/cache.db") == "/x/cache.demo.db"
