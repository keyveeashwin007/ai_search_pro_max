"""Web UI server: starts a real uvicorn on a free port and talks to it with urllib."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from txn_enrich.config import Settings
from txn_enrich.web.server import create_app
from txn_enrich.web.service import rows_to_csv

SIGS = "SQ *BLUE BOTTLE #123 OAKLAND CA\nSQ *BLUE BOTTLE #77 BERKELEY CA\nKOPI KOPI COFFEE"


@pytest.fixture
def server(tmp_path, monkeypatch):
    for var in ("OPENAI_API_KEY", "TM_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    root = Path(__file__).resolve().parent.parent
    base = Settings(
        cache_path=str(tmp_path / "cache.db"),
        known_merchants_path=str(root / "data" / "known_merchants.yaml"),
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(base, max_rows=5), host="127.0.0.1",
                                        port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "test server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


def call(base, path, body=None, method=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"} if body is not None else {}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=hdrs,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def wait_done(base, job_id):
    for _ in range(200):
        _, text, _ = call(base, f"/api/jobs/{job_id}")
        job = json.loads(text)
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def start_demo(base, **extra):
    status, text, _ = call(base, "/api/enrich", {"provider": "demo", "text": SIGS, **extra})
    assert status == 200, text
    return json.loads(text)["job_id"]


def test_index_and_static_assets(server):
    status, text, _ = call(server, "/")
    assert status == 200 and "Txn Enrichment" in text
    for asset in ("/static/js/main.js", "/static/base.css", "/static/ui.css", "/static/table.css",
                  "/static/js/live.js", "/static/js/table.js", "/static/js/input.js"):
        assert call(server, asset)[0] == 200, asset


def test_config_never_exposes_the_key(server, monkeypatch):
    status, text, _ = call(server, "/api/config")
    cfg = json.loads(text)
    assert status == 200 and cfg["has_key"] is False and "examples" in cfg
    assert cfg["defaults"]["threshold"] == 0.8
    monkeypatch.setenv("TM_API_KEY", "tm_vk_" + "ab" * 24)
    _, text, _ = call(server, "/api/config")
    assert json.loads(text)["has_key"] is True
    assert "tm_vk_" not in text


def test_demo_run_end_to_end(server):
    job_id = start_demo(server)
    job = wait_done(server, job_id)
    assert job["status"] == "done"
    rows = job["result"]["rows"]
    assert len(rows) == 3 and all(r["brand"] for r in rows)
    assert job["result"]["metrics"]["records"] == 3

    _, csv_text, hdr = call(server, f"/api/jobs/{job_id}/download.csv")
    assert csv_text.splitlines()[0].startswith("id,raw_signature") and "brand" in csv_text
    assert "attachment" in hdr["Content-Disposition"]
    _, json_text, _ = call(server, f"/api/jobs/{job_id}/download.json")
    assert len(json.loads(json_text)["rows"]) == 3


def test_validation_errors(server):
    assert call(server, "/api/enrich", {"provider": "demo", "text": "  \n# comment"})[0] == 400
    big = "\n".join(f"MERCHANT {i}" for i in range(6))
    status, text, _ = call(server, "/api/enrich", {"provider": "demo", "text": big})
    assert status == 400 and "Too many rows" in json.loads(text)["error"]
    status, text, _ = call(server, "/api/enrich", {"provider": "openai", "text": SIGS})
    assert status == 400 and "No API key" in json.loads(text)["error"]
    req = urllib.request.Request(server + "/api/enrich", data=b"text=x", method="POST",
                                 headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 415
    assert call(server, "/api/jobs/nope")[0] == 404
    assert call(server, "/api/jobs/nope/download.csv")[0] == 404


def test_cross_site_and_foreign_host_are_refused(server):
    body = {"provider": "demo", "text": SIGS}
    status, _, _ = call(server, "/api/enrich", body, headers={"Origin": "http://evil.example"})
    assert status == 403
    status, _, _ = call(server, "/api/cache/clear?provider=demo", method="POST",
                        headers={"Origin": "http://evil.example"})
    assert status == 403
    status, _, _ = call(server, "/api/config", headers={"Host": "evil.example"})
    assert status == 400  # DNS-rebinding guard (TrustedHostMiddleware)


def test_file_upload_and_settings_are_clamped(server):
    csv_data = "id,description,amount\n1,SQ *BLUE BOTTLE #1,5\n2,KOPI KOPI,7\n"
    body = {"provider": "demo", "text": "STARBUCKS 1", "file": {"name": "x.csv", "data": csv_data},
            "settings": {"threshold": 7, "budget": -5, "batch": "abc", "context_size": "huge"}}
    _, text, _ = call(server, "/api/enrich", body)
    job = wait_done(server, json.loads(text)["job_id"])
    assert job["status"] == "done" and len(job["result"]["rows"]) == 3
    assert len({r["id"] for r in job["result"]["rows"]}) == 3  # typed id "1" vs file id "1"


def test_clear_cache(server):
    wait_done(server, start_demo(server))
    _, text, _ = call(server, "/api/config")
    assert json.loads(text)["cache"]["demo"] >= 1
    _, text, _ = call(server, "/api/cache/clear?provider=demo", method="POST")
    assert json.loads(text)["cleared"] >= 1
    _, text, _ = call(server, "/api/config")
    assert json.loads(text)["cache"]["demo"] == 0


def test_csv_export_neutralises_formulas():
    out = rows_to_csv([{"a": "=HYPERLINK(\"http://x\")", "b": "ok", "c": "-1+1", "d": 3}])
    lines = out.splitlines()
    assert lines[1].startswith("\"'=HYPERLINK") and ",ok," in lines[1]
    assert "'-1+1" in lines[1] and rows_to_csv([]) == ""



def test_event_stream_replays_progress_then_finishes(server):
    job_id = start_demo(server)
    wait_done(server, job_id)  # events are kept, so a late subscriber still gets all of them
    _, stream, hdr = call(server, f"/api/jobs/{job_id}/events")
    assert hdr["Content-Type"].startswith("text/event-stream")
    assert stream.index("event: progress") < stream.index("event: finished")
    first = [b for b in stream.split("\n\n") if "event: progress" in b][0]
    assert json.loads(first.split("data: ", 1)[1])["stage"] == "start"
    assert '"status": "done"' in stream

    # reconnecting with Last-Event-ID resumes after that event instead of replaying everything
    _, resumed, _ = call(server, f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "0"})
    assert "id: 0\n" not in resumed and "id: 1\n" in resumed
