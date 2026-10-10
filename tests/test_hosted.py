"""Hosted mode: password login, CORS for a Netlify front end, limits, and the Netlify build."""

import importlib.util
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
from txn_enrich.llm import FakeProvider
from txn_enrich.web.security import Auth, RateLimited, WebOptions, check_exposure
from txn_enrich.web.server import create_app
from txn_enrich.web.service import Busy, Service

ROOT = Path(__file__).resolve().parent.parent
SIGS = "SQ *BLUE BOTTLE #123 OAKLAND CA\nKOPI KOPI COFFEE"
SITE = "https://my-site.netlify.app"
PASSWORD = "s3cret-pass"


def base_settings(tmp_path):
    return Settings(cache_path=str(tmp_path / "cache.db"),
                    known_merchants_path=str(ROOT / "data" / "known_merchants.yaml"))


@pytest.fixture
def locked(tmp_path):
    options = WebOptions(password=PASSWORD, allowed_origins=(SITE,), max_searches=3)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(base_settings(tmp_path), options=options),
                                        host="127.0.0.1", port=port, log_level="error"))
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


def login_token(base):
    status, text, _ = call(base, "/api/login", {"password": PASSWORD})
    assert status == 200, text
    return json.loads(text)["token"]


def run_job(base, token, body):
    auth = {"Authorization": f"Bearer {token}"}
    status, text, _ = call(base, "/api/enrich", body, headers=auth)
    assert status == 200, text
    job_id = json.loads(text)["job_id"]
    for _ in range(200):
        job = json.loads(call(base, f"/api/jobs/{job_id}", headers=auth)[1])
        if job["status"] != "running":
            return job_id, job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---------------------------------------------------------------- unit: Auth / options
def test_token_roundtrip_expiry_and_tampering():
    now = [1000.0]
    auth = Auth(PASSWORD, clock=lambda: now[0])
    token = auth.issue(ttl=60)
    assert auth.valid(token)
    assert not auth.valid("") and not auth.valid("abc") and not auth.valid("x.y")
    expires, _, sig = token.partition(".")
    assert not auth.valid(f"{int(expires) + 999}.{sig}")  # longer expiry breaks the signature
    assert not Auth("another-password").valid(token)  # signed with a different password
    now[0] += 61
    assert not auth.valid(token)  # expired


def test_login_rate_limit_and_no_password_mode():
    now = [0.0]
    auth = Auth(PASSWORD, clock=lambda: now[0])
    for _ in range(10):
        with pytest.raises(PermissionError):
            auth.login("nope")
    with pytest.raises(RateLimited):
        auth.login(PASSWORD)  # even the right password is held back while locked out
    now[0] += 61
    assert auth.valid(auth.login(PASSWORD))
    assert Auth("").valid("") and not Auth("").required


def test_exposure_guard():
    assert check_exposure("127.0.0.1", WebOptions()) is None
    assert check_exposure("localhost", WebOptions()) is None
    assert "TXN_ACCESS_PASSWORD" in check_exposure("0.0.0.0", WebOptions())
    assert check_exposure("0.0.0.0", WebOptions(password="short")) is not None
    assert check_exposure("0.0.0.0", WebOptions(password="long-enough-pw")) is None


def test_options_from_env():
    o = WebOptions.from_env({
        "TXN_ACCESS_PASSWORD": "pw", "TXN_ALLOWED_ORIGINS": f"{SITE}/, https://b.example",
        "TXN_MAX_ROWS": "50", "TXN_MAX_SEARCHES": "junk", "TXN_MAX_RUNNING": "2"})
    assert o.allowed_origins == (SITE, "https://b.example")
    assert (o.max_rows, o.max_searches, o.max_running) == (50, 500, 2)
    assert WebOptions.from_env({}) == WebOptions()


# ---------------------------------------------------------------- HTTP: login required
def test_everything_is_locked_until_login(locked):
    status, text, _ = call(locked, "/api/config")
    assert status == 200 and json.loads(text) == {"auth_required": True, "authed": False}
    assert call(locked, "/api/enrich", {"provider": "demo", "text": SIGS})[0] == 401
    assert call(locked, "/api/jobs/abc")[0] == 401
    assert call(locked, "/api/jobs/abc/events")[0] == 401
    assert call(locked, "/api/jobs/abc/download.csv")[0] == 401
    assert call(locked, "/api/cache/clear?provider=demo", method="POST")[0] == 401
    assert call(locked, "/api/health")[0] == 200  # platform health check needs no login


def test_wrong_password_then_right_password(locked):
    assert call(locked, "/api/login", {"password": "wrong"})[0] == 401
    assert call(locked, "/api/login", {})[0] == 401
    token = login_token(locked)
    _, text, _ = call(locked, "/api/config", headers={"Authorization": f"Bearer {token}"})
    cfg = json.loads(text)
    assert cfg["authed"] is True and cfg["auth_required"] is True and "examples" in cfg
    _, text, _ = call(locked, "/api/config", headers={"Authorization": "Bearer bad"})
    assert json.loads(text)["authed"] is False


def test_run_with_header_token_and_query_token_for_stream_and_download(locked):
    token = login_token(locked)
    job_id, job = run_job(locked, token, {"provider": "demo", "text": SIGS})
    assert job["status"] == "done"
    # EventSource and download links cannot send headers, so they pass the token in the URL
    _, stream, _ = call(locked, f"/api/jobs/{job_id}/events?token={token}")
    assert "event: finished" in stream
    status, csv_text, _ = call(locked, f"/api/jobs/{job_id}/download.csv?token={token}")
    assert status == 200 and csv_text.startswith("id,raw_signature")


def test_server_caps_the_search_budget(locked):
    token = login_token(locked)
    names = "\n".join(f"LOCALSHOP{c} BAKERY" for c in "ABCDEFGH")
    _, job = run_job(locked, token, {"provider": "demo", "text": names,
                                     "settings": {"budget": 400}})
    m = job["result"]["metrics"]
    assert m["web_searches"] <= 3  # server max_searches=3 wins over the client's 400


# ---------------------------------------------------------------- CORS (Netlify -> API)
def test_cors_allows_only_the_listed_origin(locked):
    _, _, hdr = call(locked, "/api/health", headers={"Origin": SITE})
    assert hdr.get("access-control-allow-origin") == SITE
    _, _, hdr = call(locked, "/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in hdr}
    status, _, hdr = call(locked, "/api/enrich", method="OPTIONS", headers={
        "Origin": SITE, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert status == 200 and hdr.get("access-control-allow-origin") == SITE


def test_cross_origin_post_only_from_the_listed_site(locked):
    token = login_token(locked)
    auth = {"Authorization": f"Bearer {token}"}
    body = {"provider": "demo", "text": SIGS}
    assert call(locked, "/api/enrich", body, headers={**auth, "Origin": SITE})[0] == 200
    assert call(locked, "/api/enrich", body, headers={**auth, "Origin": "https://evil.example"})[0] == 403
    assert call(locked, "/api/login", {"password": PASSWORD},
                headers={"Origin": "https://evil.example"})[0] == 403


def test_busy_limit(tmp_path):
    release = threading.Event()

    class Slow(FakeProvider):
        def triage(self, items):
            release.wait(10)
            return super().triage(items)

    service = Service(base_settings(tmp_path), lambda kind, settings: Slow(), max_running=1)
    first = service.start({"provider": "demo", "text": "ZZZ UNKNOWN SHOP"})
    with pytest.raises(Busy):
        service.start({"provider": "demo", "text": "OTHER UNKNOWN SHOP"})
    release.set()
    for _ in range(200):
        if first.status != "running":
            break
        time.sleep(0.05)
    assert first.status == "done"
    service.start({"provider": "demo", "text": "OTHER UNKNOWN SHOP"})  # a slot is free again


# ---------------------------------------------------------------- Netlify build
def load_build():
    spec = importlib.util.spec_from_file_location(
        "build_netlify", ROOT / "scripts" / "build_netlify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clean_api_url():
    b = load_build()
    assert b.clean_api_url("https://api.example.com/") == "https://api.example.com"
    assert b.clean_api_url(" https://api.example.com:8443 ") == "https://api.example.com:8443"
    assert b.clean_api_url("http://localhost:8600") == "http://localhost:8600"
    for bad in ("", "api.example.com", "http://api.example.com", "https://api.example.com/v1",
                "https://user:pw@api.example.com", "javascript:alert(1)", "https://a.com?x=1"):
        with pytest.raises(ValueError):
            b.clean_api_url(bad)


def test_netlify_build_output(tmp_path):
    b = load_build()
    out = tmp_path / "dist"
    b.build(out, "https://api.example.com")
    assert (out / "index.html").exists() and (out / "static" / "js" / "main.js").exists()
    assert 'window.TXN_API = "https://api.example.com";' in (out / "static" / "config.js").read_text()
    headers = (out / "_headers").read_text()
    assert "connect-src 'self' https://api.example.com" in headers
    assert "frame-ancestors 'none'" in headers and "nosniff" in headers
    assert "script-src 'self'" in headers
    # the repo copy keeps pointing at the same server (local use)
    assert 'window.TXN_API = "";' in (ROOT / "src/txn_enrich/web/static/config.js").read_text()
