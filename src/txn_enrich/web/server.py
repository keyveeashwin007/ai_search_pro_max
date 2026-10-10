"""HTTP routes for the local web UI (Starlette). Run it with `serve_web.py` -> http://localhost:8600.

The browser starts a run with POST /api/enrich, then follows it live over Server-Sent Events
(GET /api/jobs/<id>/events). The API key stays on the server: the browser only learns whether
one exists.

Safety (it is a local tool, but it spends real money): the server binds to 127.0.0.1, rejects
unknown Host headers (DNS rebinding) and cross-site Origins, and only accepts JSON on POST.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config import Settings
from ..llm.openai_provider import gateway_label, resolve_connection
from .security import Auth, RateLimited, WebOptions
from .service import Busy, ProviderFactory, Service, rows_to_csv

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 5_000_000
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]

EXAMPLES = """SQ *BLUE BOTTLE #123 OAKLAND CA
SQ *BLUE BOTTLE #77 BERKELEY CA
STARBUCKS STORE 12345 SAN FRANCISCO CA
UBER *EATS PENDING
AMZN Mktp US*2K4AB1C23
TST* JOES PIZZA 10/02 NEW YORK NY
KOPI KOPI COFFEE ROASTERS
PAYPAL *NETFLIX.COM 4029357733"""


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(base: Settings, provider_factory: Optional[ProviderFactory] = None,
               max_rows: Optional[int] = None, options: Optional[WebOptions] = None) -> Starlette:
    options = options or WebOptions()
    service = Service(base, provider_factory, max_rows or options.max_rows,
                      options.max_searches, options.max_running)
    auth = Auth(options.password)

    def _same_origin(request: Request) -> bool:
        """Browsers send Origin on cross-site POSTs; accept this server or a listed origin."""
        origin = request.headers.get("origin")
        if not origin:
            return True  # curl and same-origin requests without an Origin header
        return (urlparse(origin).netloc == request.headers.get("host")
                or origin.rstrip("/") in options.allowed_origins)

    def _authed(request: Request) -> bool:
        """Token from `Authorization: Bearer` (fetch) or `?token=` (EventSource and downloads)."""
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        return auth.valid(token or request.query_params.get("token", ""))

    def _locked() -> JSONResponse:
        return _error("Login required.", 401)

    async def index(request: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True})  # for the hosting platform's health check

    async def login(request: Request) -> Response:
        if not _same_origin(request):
            return _error("Cross-site request refused.", 403)
        try:
            given = str((await request.json()).get("password") or "")
        except (ValueError, AttributeError):
            return _error("Send JSON with a password.")
        try:
            return JSONResponse({"token": auth.login(given)})
        except RateLimited:
            return _error("Too many wrong passwords. Wait a minute.", 429)
        except PermissionError:
            return _error("Wrong password.", 401)

    async def config(request: Request) -> Response:
        if not _authed(request):  # reveal nothing until logged in
            return JSONResponse({"auth_required": True, "authed": False})
        key, url = resolve_connection()
        return JSONResponse({
            "auth_required": auth.required, "authed": True,
            "has_key": bool(key),
            "gateway": gateway_label(url),
            "examples": EXAMPLES,
            "max_rows": service.max_rows,
            "defaults": {
                "model": base.model,
                "threshold": base.confidence_threshold,
                "budget": base.max_searches_per_run,
                "batch": base.batch_size,
                "context_size": base.search_context_size,
            },
            "cache": {"openai": len(service.cache_for("openai")),
                      "demo": len(service.cache_for("demo"))},
        })

    async def enrich(request: Request) -> Response:
        if not _same_origin(request):
            return _error("Cross-site request refused.", 403)
        if not _authed(request):
            return _locked()
        if "application/json" not in request.headers.get("content-type", ""):
            return _error("Send JSON (Content-Type: application/json).", 415)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return _error("Request too large (5 MB limit).", 413)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return _error("Request body must be a JSON object.")
        try:
            job = await asyncio.to_thread(service.start, body)
        except Busy as exc:
            return _error(str(exc), 429)
        except ValueError as exc:
            return _error(str(exc))
        except Exception as exc:  # e.g. an unreadable upload
            return _error(f"Could not start the run: {exc}"[:300])
        return JSONResponse({"job_id": job.id, "records": job.records})

    def _job(request: Request):
        return service.jobs.get(request.path_params["job_id"])

    async def job_status(request: Request) -> Response:
        if not _authed(request):
            return _locked()
        job = _job(request)
        if job is None:
            return _error("Unknown job.", 404)
        return JSONResponse({"status": job.status, "error": job.error, "result": job.result,
                             "events": len(job.events)})

    async def job_events(request: Request) -> Response:
        if not _authed(request):
            return _locked()
        job = _job(request)
        if job is None:
            return _error("Unknown job.", 404)
        try:  # EventSource resends the last id it saw when it reconnects
            sent = int(request.headers.get("last-event-id", "-1")) + 1
        except ValueError:
            sent = 0

        async def stream():
            nonlocal sent
            while True:
                status = job.status  # read BEFORE the events so none are missed
                while sent < len(job.events):
                    payload = json.dumps(job.events[sent])
                    yield f"id: {sent}\nevent: progress\ndata: {payload}\n\n"
                    sent += 1
                if status != "running":
                    done = json.dumps({"status": status, "error": job.error})
                    yield f"event: finished\ndata: {done}\n\n"
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.15)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def download(request: Request) -> Response:
        if not _authed(request):
            return _locked()
        job = _job(request)
        if job is None or job.result is None:
            return _error("No finished results for that job.", 404)
        if request.path_params["fmt"] == "csv":
            return Response(rows_to_csv(job.result["rows"]), media_type="text/csv", headers={
                "Content-Disposition": 'attachment; filename="enriched.csv"'})
        body = json.dumps({"rows": job.result["rows"], "metrics": job.result["metrics"]}, indent=2)
        return Response(body, media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="enriched.json"'})

    async def clear_cache(request: Request) -> Response:
        if not _same_origin(request):
            return _error("Cross-site request refused.", 403)
        if not _authed(request):
            return _locked()
        kind = "openai" if request.query_params.get("provider") == "openai" else "demo"
        return JSONResponse({"cleared": service.cache_for(kind).clear()})

    routes = [
        Route("/", index),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/health", health),
        Route("/api/config", config),
        Route("/api/enrich", enrich, methods=["POST"]),
        Route("/api/jobs/{job_id}", job_status),
        Route("/api/jobs/{job_id}/events", job_events),
        Route("/api/jobs/{job_id}/download.{fmt}", download),
        Route("/api/cache/clear", clear_cache, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
    ]
    hosts = ["*"] if "*" in options.allowed_hosts else [*ALLOWED_HOSTS, *options.allowed_hosts]
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=hosts)]
    if options.allowed_origins:  # front end served from another site (e.g. Netlify)
        middleware.append(Middleware(
            CORSMiddleware, allow_origins=list(options.allowed_origins),
            allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
            max_age=600))
    app = Starlette(routes=routes, middleware=middleware)
    app.state.service = service
    return app
