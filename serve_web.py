"""Start the web UI (separate from the Streamlit app).

Run:  .venv/bin/python serve_web.py            ->  http://localhost:8600
      .venv/bin/python serve_web.py --port 9000

Hosted (Render, Fly, Railway, ...): the platform sets PORT; use --host 0.0.0.0. That needs
TXN_ACCESS_PASSWORD (>= 8 characters), otherwise the server refuses to start. Other settings:
TXN_ALLOWED_ORIGINS (your Netlify URL), TXN_ALLOWED_HOSTS, TXN_MAX_ROWS, TXN_MAX_SEARCHES,
TXN_MAX_RUNNING. See DEPLOY.md.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from txn_enrich.config import load_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Web UI for txn-enrichment.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8600))
    parser.add_argument("--host", default=os.environ.get("HOST") or "127.0.0.1",
                        help="127.0.0.1 for local use; 0.0.0.0 when hosted (needs a password)")
    args = parser.parse_args()

    import uvicorn

    from txn_enrich.web.security import LOCAL_HOSTS, WebOptions, check_exposure
    from txn_enrich.web.server import create_app

    load_dotenv(ROOT / ".env")
    options = WebOptions.from_env()
    problem = check_exposure(args.host, options)
    if problem:
        sys.exit(problem)
    if args.host not in LOCAL_HOSTS and not options.allowed_hosts:
        # Hosted: the platform's public hostname is unknown here and the password protects it.
        options = replace(options, allowed_hosts=("*",))
    base = load_settings(ROOT / "config.yaml")
    base = base.with_overrides(  # same path handling as app.py
        cache_path=str(ROOT / base.cache_path),
        known_merchants_path=str(ROOT / base.known_merchants_path),
    )
    print(f"Web UI on http://{args.host}:{args.port}  (login required: {bool(options.password)})")
    uvicorn.run(create_app(base, options=options), host=args.host, port=args.port,
                log_level="warning", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()

