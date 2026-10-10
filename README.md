# txn-enrichment

GenAI enrichment of card-transaction merchant signatures that **minimises tokens and
web-search calls**. You type signatures (or upload a file) into a web UI. The model decides
what is worth searching, and the pipeline makes sure each unique merchant costs at most
one search, ever.

Each result has the **store name** (`merchant_name`, the outlet as it trades, e.g. "Blue Bottle
Coffee Oakland") and the **brand** (`brand`, the consumer brand or chain behind it, e.g. "Blue
Bottle Coffee"; never a payment processor such as PayPal or Square). It also has category,
subcategory, website, online/physical type, location, confidence and sources.

## How it saves tokens and searches

```
typed signatures ─► 1 normalize ─► 2 dedupe ─► 3 cache ─► 4 known-merchant rules
                                                               │ (0 tokens, 0 searches)
                    5 batched LLM triage, NO tools attached ◄──┘
                      confidence ≥ 0.8 → done (no search)
                      else ─► 6 budget planner ─► 7 web search (max 1 per merchant) ─► cache
```

| Step | What it does | Saves |
|---|---|---|
| Normalize | `SQ *BLUE BOTTLE #123 OAKLAND CA` → `blue bottle` (+ location hint) | many raw variants become 1 key |
| Dedupe | exact key + conservative typo merge (`blue botle` → `blue bottle`) | duplicate calls |
| Cache | SQLite, 30-day TTL, keyed by merchant | repeat merchants cost 0 |
| Rules | `data/known_merchants.yaml` (Amazon, Uber, Starbucks, …) | 0 tokens for big brands |
| Triage | 20 merchants per call, compact JSON, structured output, **no tools**, so the model *cannot* search | prompt overhead spread over the batch |
| Planner | searches only low-confidence merchants; ranked by #transactions, then spend; hard cap per run | bounded search spend |
| Search | OpenAI `web_search` tool with `max_tool_calls=1`, `search_context_size=low`, model gets only `sig` + its own query + missing fields | 1 search, small context |

The UI shows records vs unique merchants, searches used vs budget (and searches avoided
compared with one search per record), tokens in/out, LLM calls and estimated cost.

## Setup

```bash
cd /Users/manaskinkar/.cline/data/workspaces/chat/txn-enrichment
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # already done
cp .env.example .env      # then put TM_API_KEY (RouterPlus) or OPENAI_API_KEY in .env
```

### Using RouterPlus (recommended setup here)

[RouterPlus](https://app.routerplus.com/docs/overview) is an OpenAI-compatible gateway. Its
`POST /v1/responses` passes OpenAI models straight through to OpenAI, including the hosted
`web_search` tool, at OpenAI's prices ($10 per 1k searches plus tokens).

1. Sign up at https://app.routerplus.com, add credits in Billing (no free credit), and create a key
   (`tm_vk_...`) under Console > API keys.
2. Put it in `.env` as `TM_API_KEY=tm_vk_...`. A `tm_vk_` key is sent to
   `https://api.routerplus.com/v1` automatically (`OPENAI_BASE_URL` overrides this).
3. Run `PYTHONPATH=src .venv/bin/python -m txn_enrich.cli --check`. It lists models (no cost) and
   confirms `gpt-4.1-mini` is available.

RouterPlus reports the exact charge for every call as `usage.cost`. The CLI prints it as
`billed=$...`, and the UI shows **Billed cost** in place of the estimate.
Don't use Claude models for search: on `/v1/responses` RouterPlus drops `web_search` for
non-OpenAI models.

## Run the UI

```bash
.venv/bin/streamlit run app.py
```

Opens http://localhost:8501. Type one signature per line (or click **Load examples**),
optionally set amount/MCC/country, and click **Enrich**. Sidebar: model, confidence
threshold, search budget, batch size, search context size, clear cache, CSV/JSONL upload
(column `description`, optional `id, amount, mcc, country`; see
`data/sample_transactions.csv`). Without a key, choose **Offline demo** to see the
pipeline with fake data.

**Search activity:** while a run is going, a live panel shows each step: cache and rule hits,
the model's no-web triage verdict for each merchant (how sure it is, and whether it needs a
search), then every web search. Each search shows ⏳ while it runs and ✅/❌ when it finishes,
with the name found, confidence, number of searches, cost, the query, and the source websites.
A progress bar counts the finished searches. After the run, the same log stays on the page
under **🔎 Search activity**. The UI gets this from `Enricher.run(txns, on_event=...)`
(`ProgressEvent`), and `src/txn_enrich/activity.py` renders it.

## Web UI (second front end, separate port)

A custom HTML/JS interface on its own server, so it can run next to Streamlit:

```bash
.venv/bin/python serve_web.py          # http://localhost:8600   (Streamlit stays on 8501)
.venv/bin/python serve_web.py --port 9000
```

It uses the same pipeline, config, cache and `.env`. Features: live stepper and progress bar with
one card per merchant being searched (query, sources, cost), summary cards (searches avoided,
billed cost, tokens), a sortable table with text / category / low-confidence / "how" filters and
duplicate grouping, a detail drawer per row with sources and the pipeline trail, CSV / JSON
download, file drop, light / dark theme, remembered settings, and Cmd/Ctrl+Enter to run.

How it works: `POST /api/enrich` starts a background job; the browser follows it over
Server-Sent Events (`/api/jobs/<id>/events`). No build step and no CDN: plain files under
`src/txn_enrich/web/static/`. Safety: binds to 127.0.0.1, rejects foreign Host / Origin headers,
never sends the API key to the browser, escapes all model/web text, and neutralises spreadsheet
formulas in the CSV export. Tests: `tests/test_web.py`.

**Hosted (Netlify front end + Python API on Render/Fly/Railway):** see [DEPLOY.md](DEPLOY.md).
The API needs `TXN_ACCESS_PASSWORD` (it refuses to start on a public address without one) and
`TXN_ALLOWED_ORIGINS` (your Netlify URL); Netlify needs `TXN_API_URL`. Tests: `tests/test_hosted.py`.

## CLI

```bash
PYTHONPATH=src .venv/bin/python -m txn_enrich.cli "SQ *BLUE BOTTLE #123 OAKLAND CA" "UBER *EATS"
PYTHONPATH=src .venv/bin/python -m txn_enrich.cli -f sigs.txt --json
PYTHONPATH=src .venv/bin/python -m txn_enrich.cli --fake "MYSTERY SHOP"   # offline
PYTHONPATH=src .venv/bin/python -m txn_enrich.cli --check   # key + model check, 0 tokens
```

If the API rejects an optional search setting for your model (`max_tool_calls`, `include`,
`search_context_size`, `user_location`), the provider drops it, retries once and tells you
(CLI note / UI warning). `tool_choice="required"` and the strict JSON output are always sent.

## Tests (offline, no key needed)

```bash
.venv/bin/python -m pytest -q
```

Covers normalization, rules, fuzzy dedupe, budget allocation, "100 rows / 10 merchants →
≤10 searches", "second run → 0 searches", batching, failure handling, and the OpenAI
request shape (no tools in triage; `max_tool_calls` cap in search) via a stub client.

## Configuration

`config.yaml` holds the agreed defaults: threshold 0.8, batch 20, 20 searches/run,
1 search/merchant, low search context. `OPENAI_MODEL` in `.env` overrides the model.
Cost estimates use the prices in `config.yaml` (gpt-4.1-mini, checked 2026-10-10):
uncached and cached input, output, and $10 per 1k searches. The search content (~8k tokens) is
already included in the reported input tokens, so `search_content_tokens_per_search` is 0
(verified on a live RouterPlus run: estimate $0.013818 vs billed $0.013816). Only `search`
actions count as searches.
Update the prices if you change the model.

## Layout

```
app.py                       Streamlit UI
config.yaml                  settings
data/known_merchants.yaml    zero-cost merchant rules (extend freely)
src/txn_enrich/
  normalize.py  cache.py  rules.py  planner.py  enricher.py  metrics.py  models.py  cli.py
  activity.py               progress events -> live "Search activity" log (Markdown)
  llm/base.py               provider interface (triage + search_enrich)
  llm/openai_provider.py    OpenAI Responses API + web_search
  llm/prompts.py            short prompts + strict JSON schemas
  llm/fake.py               offline provider for tests/demo
tests/
```

To add another provider (Anthropic, Gemini, …), implement `triage()` and `search_enrich()`
from `llm/base.py`.

## Known limitations (MVP)

- Normalization rules are US-centric (state codes). A trailing `CO` is read as Colorado.
- Fuzzy merging is deliberately conservative (same word count and first word).
- Results that ended in `budget_exhausted` / `error` are not cached, so a later run can retry them.
- Cost figures are estimates.
