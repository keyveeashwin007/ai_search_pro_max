"""Build the Netlify site: the static front end plus the address of the API server.

Netlify runs this (see netlify.toml). Set TXN_API_URL in the Netlify UI to the https address of
the Python backend, e.g. https://txn-enrichment.onrender.com. Output goes to dist/:

    dist/index.html, dist/static/...   the front end
    dist/static/config.js              window.TXN_API = "<your api url>"
    dist/_headers                      security headers; the CSP only allows that API origin

Usage: TXN_API_URL=https://api.example.com python scripts/build_netlify.py [--out dist]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "txn_enrich" / "web" / "static"


def clean_api_url(raw: str) -> str:
    """The API origin (scheme://host[:port]). http is allowed only for localhost."""
    parsed = urlparse((raw or "").strip())
    local = parsed.hostname in ("localhost", "127.0.0.1")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("TXN_API_URL must look like https://your-api.example.com")
    if parsed.scheme == "http" and not local:
        raise ValueError("TXN_API_URL must be https (the password and results travel over it)")
    if parsed.username or parsed.password or parsed.path.strip("/") or parsed.query or parsed.fragment:
        raise ValueError("TXN_API_URL must be only scheme://host[:port], with no path or login")
    return f"{parsed.scheme}://{parsed.netloc}"


def headers_file(api: str) -> str:
    csp = (
        f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data:; connect-src 'self' {api}; frame-ancestors 'none'; "
        f"base-uri 'none'; form-action 'self'"
    )
    return (
        "/*\n"
        f"  Content-Security-Policy: {csp}\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: no-referrer\n"
        "  X-Frame-Options: DENY\n"
        "/static/*\n"
        "  Cache-Control: public, max-age=300\n"
    )


def build(out: Path, api_url: str) -> None:
    api = clean_api_url(api_url)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out / "static")
    shutil.copy(STATIC / "index.html", out / "index.html")
    (out / "static" / "config.js").write_text(
        f"// Written by scripts/build_netlify.py\nwindow.TXN_API = {json.dumps(api)};\n")
    (out / "_headers").write_text(headers_file(api))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "dist"))
    args = parser.parse_args()
    try:
        build(Path(args.out), os.environ.get("TXN_API_URL", ""))
    except ValueError as exc:
        sys.exit(f"build_netlify: {exc}")
    print(f"Built {args.out} for API {os.environ['TXN_API_URL']}")


if __name__ == "__main__":
    main()
