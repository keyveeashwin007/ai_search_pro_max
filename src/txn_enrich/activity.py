"""Turn enricher ProgressEvents into a readable activity log (Markdown) for the UI.

Pure functions, no Streamlit import, so they are easy to test. A search that is still running
is shown as ⏳; once it finishes, the same line turns into ✅ / ❌ with what was found.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .enricher import ProgressEvent

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+!|~$])")


def md(text: Optional[str]) -> str:
    """Escape user/web text so Markdown (and Streamlit's $...$ math) shows it literally."""
    return _MD_SPECIAL.sub(r"\\\1", str(text or ""))


def domain(url: str) -> str:
    host = urlparse(url).netloc or url
    return host[4:] if host.startswith("www.") else host


def clean_url(url: str) -> str:
    """Drop utm_* tracking parameters (the search tool adds utm_source=openai)."""
    parts = urlparse(url)
    if not parts.query:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_")]
    return urlunparse(parts._replace(query=urlencode(query)))


def source_links(urls: list[str]) -> str:
    """Markdown links, one per website (the first URL seen for each domain)."""
    by_domain: dict[str, str] = {}
    for u in urls:
        by_domain.setdefault(domain(u), clean_url(u))
    return ", ".join(f"[{md(d)}]({u.replace(')', '%29')})" for d, u in by_domain.items())


def name_with_brand(name: Optional[str], brand: Optional[str]) -> str:
    """'Store (brand: Brand)'; the brand is left out when it is missing or equals the store name."""
    text = md(name or "?")
    if brand and brand.strip().lower() != (name or "").strip().lower():
        text += f" (brand: {md(brand)})"
    return text


def _money(value: Optional[float]) -> str:
    return "" if value is None else f" · \\${value:.4f}"


def search_progress(events: Iterable[ProgressEvent]) -> tuple[int, int]:
    """(finished, total) web searches seen so far."""
    started = finished = 0
    for e in events:
        if e.stage == "search_start":
            started += 1
        elif e.stage in ("search_done", "search_error"):
            finished += 1
    return finished, started


def _search_line(start: ProgressEvent, end: Optional[ProgressEvent]) -> list[str]:
    key = md(start.key)
    planned = start.detail.get("query") or start.key
    if end is None:
        return [f"- ⏳ Searching the web for **{key}** … query: _{md(planned)}_"]
    if end.stage == "search_error":
        return [f"- ❌ **{key}**: {md(end.detail.get('error'))}"]

    d = end.detail
    n = d.get("searches", 0)
    head = f"- ✅ **{key}** → **{name_with_brand(d.get('name'), d.get('brand'))}**"
    if d.get("category"):
        head += f" · {md(d['category'])}"
    head += f" · {d.get('confidence', 0):.0%} sure · 🔎 {n} search{'es' if n != 1 else ''}"
    head += _money(d.get("billed_usd"))
    lines = [head]
    queries = d.get("queries") or []
    if queries:
        lines.append("    - searched for: " + ", ".join(f"_{md(q)}_" for q in queries))
    else:
        lines.append(f"    - query: _{md(planned)}_")
    if d.get("website"):
        lines.append(f"    - website: {md(d['website'])}")
    sources = d.get("sources") or []
    if sources:
        lines.append(f"    - sources: {source_links(sources)}")
    elif n == 0:
        lines.append("    - no web search was run (the model answered without one)")
    return lines


def render_activity(events: list[ProgressEvent]) -> str:
    """Markdown log of a run; call again with more events to refresh it live."""
    finished = {e.key: e for e in events if e.stage in ("search_done", "search_error")}
    lines: list[str] = []
    for e in events:
        if e.stage in ("start", "local"):
            lines.append(f"- {'📥' if e.stage == 'start' else '🗂️'} {md(e.message)}")
        elif e.stage == "triage_start":
            lines.append(f"- 🧠 {md(e.message)} …")
        elif e.stage == "triage_done":
            lines.append(f"- 🧠 {md(e.message)}")
            for v in e.detail.get("verdicts", []):
                if v["needs_search"]:
                    lines.append(
                        f"    - ❓ **{md(v['key'])}**: only {v['confidence']:.0%} sure "
                        f"→ needs a web search"
                    )
                else:
                    lines.append(
                        f"    - ✔️ **{md(v['key'])}** → "
                        f"{name_with_brand(v.get('name'), v.get('brand'))} "
                        f"({v['confidence']:.0%} sure, no search needed)"
                    )
        elif e.stage == "plan":
            lines.append(f"- 📋 {md(e.message)}")
            for key in e.detail.get("skipped", []):
                lines.append(f"    - ⏭️ **{md(key)}** skipped: search budget used up")
        elif e.stage == "search_start":
            lines.extend(_search_line(e, finished.get(e.key)))
        elif e.stage == "done":
            lines.append(f"- 🏁 {md(e.message)}")
    return "\n".join(lines)
