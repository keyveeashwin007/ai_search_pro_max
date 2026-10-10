"""Streamlit UI: type merchant signatures, enrich them, see token/search savings.

Run:  .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from txn_enrich.cache import EnrichmentCache  # noqa: E402
from txn_enrich.config import demo_cache_path, load_settings  # noqa: E402
from txn_enrich.activity import render_activity, search_progress  # noqa: E402
from txn_enrich.enricher import Enricher, ProgressEvent  # noqa: E402
from txn_enrich.inputs import load_transactions, parse_signatures  # noqa: E402
from txn_enrich.llm import FakeProvider, OpenAIProvider  # noqa: E402
from txn_enrich.llm.openai_provider import gateway_label, resolve_connection  # noqa: E402
from txn_enrich.models import Transaction  # noqa: E402

load_dotenv(ROOT / ".env")
BASE = load_settings(ROOT / "config.yaml")
BASE = BASE.with_overrides(
    cache_path=str(ROOT / BASE.cache_path),  # absolute paths are kept as-is by Path join
    known_merchants_path=str(ROOT / BASE.known_merchants_path),
)

EXAMPLES = """SQ *BLUE BOTTLE #123 OAKLAND CA
SQ *BLUE BOTTLE #77 BERKELEY CA
STARBUCKS STORE 12345 SAN FRANCISCO CA
UBER *EATS PENDING
AMZN Mktp US*2K4AB1C23
TST* JOES PIZZA 10/02 NEW YORK NY
KOPI KOPI COFFEE ROASTERS
PAYPAL *NETFLIX.COM 4029357733"""

st.set_page_config(page_title="Txn Enrichment", page_icon="🔎", layout="wide")


@st.cache_resource
def get_cache(path: str, ttl: int) -> EnrichmentCache:
    return EnrichmentCache(path, ttl)


def transactions_from_upload(upload) -> list[Transaction]:
    """CSV needs a `description` (or `raw`/`signature`/`merchant`) column; JSONL one object per line."""
    data = upload.getvalue().decode("utf-8", errors="replace")
    return load_transactions(data, upload.name)


# ------------------------------------------------------------------ sidebar
_key, _base_url = resolve_connection()
has_key = bool(_key)
GATEWAY = gateway_label(_base_url)
with st.sidebar:
    st.header("Settings")
    provider_name = st.radio(
        "Provider",
        ["OpenAI (model + web search)", "Offline demo (fake, no key)"],
        index=0 if has_key else 1,
        help="Offline demo makes no network calls; useful to see the pipeline without a key.",
    )
    use_openai = provider_name.startswith("OpenAI")
    if use_openai and not has_key:
        st.error(
            "No API key found. Copy .env.example to .env, add OPENAI_API_KEY "
            "(or TM_API_KEY for RouterPlus), then restart."
        )
    elif use_openai:
        st.caption(f"Endpoint: {GATEWAY}")
    model = st.text_input("Model", BASE.model, disabled=not use_openai)
    threshold = st.slider("Confidence threshold (skip search at/above)", 0.0, 1.0,
                          BASE.confidence_threshold, 0.05)
    budget = st.number_input("Max web searches per run", 0, 500, BASE.max_searches_per_run)
    batch = st.number_input("Triage batch size", 1, 50, BASE.batch_size)
    context = st.select_slider("Search context size", ["low", "medium", "high"],
                               BASE.search_context_size, disabled=not use_openai)

    # The offline demo uses its own cache file so fake results never leak into real runs.
    cache_file = BASE.cache_path if use_openai else demo_cache_path(BASE.cache_path)
    cache = get_cache(cache_file, BASE.cache_ttl_days)
    st.caption(f"Cache ({'real' if use_openai else 'demo'}): {len(cache)} merchants")
    if st.button("Clear cache"):
        st.toast(f"Cleared {cache.clear()} cached merchants")
    st.divider()
    upload = st.file_uploader("Bulk input (CSV / JSONL)", type=["csv", "jsonl", "json"])

settings = BASE.with_overrides(
    model=model, confidence_threshold=threshold, max_searches_per_run=int(budget),
    batch_size=int(batch), search_context_size=context,
)

# --------------------------------------------------------------------- main
st.title("🔎 Transaction Enrichment")
st.caption(
    "Type merchant signatures. Cache → known-merchant rules → batched LLM triage (no web) "
    "→ web search only for unresolved, de-duplicated merchants within the budget."
)

if "sig_text" not in st.session_state:
    st.session_state.sig_text = ""
c1, c2 = st.columns([4, 1])
with c2:
    if st.button("Load examples", use_container_width=True):
        st.session_state.sig_text = EXAMPLES
with c1:
    text = st.text_area("Merchant signatures (one per line)", key="sig_text", height=200,
                        placeholder="SQ *BLUE BOTTLE #123 OAKLAND CA")

with st.expander("Optional context for typed signatures (applied to every line)"):
    o1, o2, o3 = st.columns(3)
    amount_in = o1.text_input("Amount", "")
    mcc_in = o2.text_input("MCC", "", max_chars=4)
    country_in = o3.text_input("Country (ISO-2)", "", max_chars=2)

run = st.button("Enrich", type="primary", key="enrich", disabled=use_openai and not has_key)


def build_transactions() -> list[Transaction]:
    txns = parse_signatures(text)
    try:
        amount = float(amount_in) if amount_in.strip() else None
    except ValueError:
        amount = None
        st.warning("Amount ignored: not a number.")
    for t in txns:
        t.amount, t.mcc, t.country = amount, mcc_in.strip() or None, country_in.strip() or None
    if upload is not None:
        try:
            extra = transactions_from_upload(upload)
            typed_ids = {t.id for t in txns}
            for t in extra:  # keep ids unique when typed lines and file rows overlap
                if t.id in typed_ids:
                    t.id = f"file-{t.id}"
            txns += extra
        except Exception as exc:  # show a friendly error instead of a stack trace
            st.error(f"Could not read file: {exc}")
    return txns


PATH_HELP = {
    "cache": "served from cache (0 tokens, 0 searches)",
    "rule": "known-merchant rule (0 tokens, 0 searches)",
    "llm": "LLM triage, confident: no search",
    "llm+search": "LLM + one web search",
    "budget_exhausted": "needed search but budget ran out (triage answer kept)",
    "error": "provider error, see note",
}

if run:
    txns = build_transactions()
    if not txns:
        st.warning("Enter at least one signature (or upload a file).")
        st.stop()
    try:
        provider = (
            OpenAIProvider(settings.model, settings.search_context_size)
            if use_openai else FakeProvider()
        )
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    enricher = Enricher(provider, settings, cache=cache)
    # Live "search activity" panel: the enricher calls on_event (in this thread) at each step.
    live_events: list[ProgressEvent] = []
    with st.status(f"Enriching {len(txns)} signature(s)…", expanded=True) as status:
        progress_slot = st.empty()
        log_slot = st.empty()

        def on_event(event: ProgressEvent) -> None:
            live_events.append(event)
            done_n, total_n = search_progress(live_events)
            if event.stage == "search_start" or (total_n and event.stage.startswith("search_")):
                status.update(label=f"Web searching… {done_n} / {total_n} done")
                progress_slot.progress(done_n / total_n, text=f"Web searches: {done_n} / {total_n}")
            elif event.stage == "triage_start":
                status.update(label="Asking the model (no web)…")
            log_slot.markdown(render_activity(live_events))

        st.session_state.result = enricher.run(txns, on_event=on_event)
        st.session_state.provider_label = provider.name
        st.session_state.dropped = sorted(getattr(provider, "dropped_features", set()))
        m_live = st.session_state.result.metrics
        status.update(
            label=f"Done: {m_live.web_searches} web search(es), {m_live.llm_calls} model call(s)",
            state="error" if m_live.errors else "complete",
            expanded=False,
        )

result = st.session_state.get("result")
if result is not None:
    m = result.metrics
    st.subheader("Run stats")
    k = st.columns(6)
    k[0].metric("Records", m.records, help="input signatures")
    k[1].metric("Unique merchants", m.unique_merchants - m.fuzzy_merged,
                help=f"after normalization; {m.fuzzy_merged} typo variants merged")
    k[2].metric("Web searches", f"{m.web_searches} / {settings.max_searches_per_run}",
                delta=f"-{m.searches_avoided} vs 1 per record", delta_color="inverse")
    k[3].metric("Tokens in / out", f"{m.input_tokens:,} / {m.output_tokens:,}",
                help=f"{m.cached_input_tokens:,} input tokens served from OpenAI prompt cache")
    k[4].metric("LLM calls", m.llm_calls)
    if m.billed_complete:
        k[5].metric(
            "Billed cost", f"${m.billed_usd:.4f}",
            help=f"exact charge reported by the endpoint (usage.cost); "
                 f"config.yaml estimate was ${m.est_cost_usd:.4f}",
        )
    else:
        k[5].metric(
            "Est. cost", f"${m.est_cost_usd:.4f}",
            help="prices from config.yaml: tokens + $/search + search content tokens per search",
        )
    if st.session_state.get("dropped"):
        st.warning(
            "The API rejected these optional settings, so the run skipped them: "
            + ", ".join(st.session_state.dropped)
        )

    funnel = pd.DataFrame(
        [
            ("Cache hits", m.cache_hits),
            ("Known-merchant rules", m.rule_hits),
            ("LLM triage, no search", m.llm_resolved),
            ("Web searched", m.searches_planned),
            ("Budget exhausted", m.budget_exhausted),
            ("Errors", m.errors),
        ],
        columns=["Resolved by (unique merchants)", "Count"],
    )
    st.dataframe(funnel, hide_index=True, use_container_width=False)

    if result.events:
        searched = sum(1 for e in result.events if e.stage == "search_start")
        with st.expander(f"🔎 Search activity ({searched} merchant(s) web searched)",
                         expanded=searched > 0):
            st.markdown(render_activity(result.events))

    if st.session_state.get("provider_label") == "fake":
        st.info("Offline demo results: generated by the fake provider, not real data.")

    st.subheader("Results")
    df = pd.DataFrame(result.rows())
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "merchant_name": st.column_config.TextColumn(
                "store name", help="the store / outlet as it trades"),
            "brand": st.column_config.TextColumn(
                "brand", help="consumer brand or chain behind the store"),
            "confidence": st.column_config.ProgressColumn("confidence", min_value=0.0,
                                                          max_value=1.0, format="%.2f"),
            "website": st.column_config.TextColumn("website"),
            "sources": st.column_config.TextColumn("sources", width="medium"),
            "path": st.column_config.TextColumn(
                "path", help="; ".join(f"{k}: {v}" for k, v in PATH_HELP.items())),
        },
    )
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button("Download CSV", df.to_csv(index=False), "enriched.csv", "text/csv")
    d2.download_button(
        "Download JSON",
        json.dumps({"rows": result.rows(), "metrics": m.to_dict()}, indent=2),
        "enriched.json", "application/json",
    )

