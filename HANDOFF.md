# HANDOFF: txn-enrichment (paste this into a new chat to continue)

Last updated: 2026-10-10 (session 2). Status: **MVP built; 59/59 offline tests pass. Live-tested through RouterPlus (`.env` has a working `TM_API_KEY`; never print it).**

## 0e. Hosting: Netlify front end + password-gated API (session 2, latest) — NOT RUN
- Plan and steps: `DEPLOY.md`. Code: `web/security.py` (WebOptions from `TXN_*` env, `Auth` =
  shared password -> HMAC-signed expiring token, login rate limit, `check_exposure`), `server.py`
  (`/api/login`, `/api/health`, token on every other /api route via Bearer header or `?token=`,
  CORS for `TXN_ALLOWED_ORIGINS`, Host check relaxed by `TXN_ALLOWED_HOSTS`), `service.py`
  (`Busy`, `max_searches`, `max_running`). `serve_web.py` honours `HOST`/`PORT`, refuses a public
  bind without a password. Front end: `static/config.js` (`window.TXN_API`), `js/api.js`
  (base URL, token, login), login overlay in index.html. Deploy files: `netlify.toml`,
  `scripts/build_netlify.py` (validates `TXN_API_URL`, writes config.js + CSP `_headers`),
  `Dockerfile`, `requirements-web.txt`, `render.yaml`, `.dockerignore`. Tests: `tests/test_hosted.py`.
- **Nothing has been executed**: the shell tool returned nothing for the whole session. Not run:
  pytest (expected ~68 + the new hosted tests), the server, the login page in a browser, the
  Docker build, any real Netlify/Render deploy (needs the user's accounts).
- Guesses to verify first: Starlette CORS preflight returns 200 with `urllib` OPTIONS; the
  `test_server_caps_the_search_budget` assertion (FakeProvider search counts); `EventSource`
  cross-origin works with `?token=` (no cookies needed); the Netlify CSP does not block the
  inline `style=` attributes the JS sets (style-src allows 'unsafe-inline').
- Known weakness: token in the URL for SSE/downloads can land in access logs (expires in 12 h).

## 0d. Custom web UI on its own port (session 2)
- `serve_web.py` -> http://localhost:8600 (Streamlit stays on 8501). Starlette + uvicorn, no build
  step. Code: `src/txn_enrich/web/{service,server}.py`; front end: `web/static/` (index.html,
  base/ui/table.css, js/{main,input,live,summary,table,drawer,state,util}.js). Tests:
  `tests/test_web.py` (real uvicorn on a free port + urllib).
- **NOT YET RUN.** The terminal tool hung right after the files were written (a heredoc), so
  `pytest` was not re-run (expected ~68 tests with test_web.py), the server was not started and
  the page was never opened in a browser. First: run the tests, start `serve_web.py`, open the
  page, press "Try the examples" with the offline demo, then a live run.
- Editor edits over ~6,000 characters fail; avoid heredocs in the shell tool.

## 0c. Brand field (session 2)
- `Enrichment.brand` sits next to `merchant_name` (the store name), e.g. "Blue Bottle Coffee
  Oakland" with brand "Blue Bottle Coffee". It is in both strict JSON schemas and both prompts,
  in the CSV/JSON rows right after `merchant_name`, in the CLI output, and in the activity log
  ("Store (brand: X)", shown only when the brand differs from the store name).
- Rules: `brand` is optional in `known_merchants.yaml` and defaults to `merchant_name`
  (an explicit value is used for, e.g., Amazon Prime, whose brand is Amazon).
- Cache: entries saved before this change have no `brand` key, so they count as misses and are
  looked up again once (triage is cheap; a search only if the model is unsure).
- Live check (triage only): Blue Bottle → store "Blue Bottle Coffee Oakland" / brand "Blue Bottle
  Coffee"; Top Form Payyanur → brand "Top Form"; $0.000386 billed.

## 0b. Live search activity in the UI (session 2, latest)
- `Enricher.run(txns, on_event=cb)` emits `ProgressEvent(stage, message, key, detail)`:
  start, local, triage_start, triage_done (per-merchant verdicts), plan (searching/skipped),
  search_start (per merchant), search_done (name, confidence, searches, queries, sources,
  tokens, billed_usd) / search_error, done. The callback runs in the calling thread
  (`as_completed`, not in workers); exceptions in it are swallowed. `RunResult.events` keeps them.
- `src/txn_enrich/activity.py`: `render_activity(events)` → Markdown (⏳ → ✅/❌), plus
  `search_progress`, `md` escaping (also `$`), and source links cleaned of `utm_*`, one per domain.
- `app.py`: `st.status` panel with a progress bar updated live, then the
  "🔎 Search activity" expander under Run stats.
- `SearchOutcome.queries`: from `action.query`/`queries`. Seen live via RouterPlus, `action.query`
  can echo the whole JSON prompt, so JSON-like or >200-character values are ignored and the UI
  shows the planned triage query instead.

## 0a. RouterPlus (session 2)

The user wants to use **RouterPlus** (https://app.routerplus.com/docs/overview). From its docs:
- OpenAI-compatible base URL `https://api.routerplus.com/v1`; key `tm_vk_...`, sent as Bearer.
- `POST /v1/responses` passes OpenAI models through (`gpt-4.1-mini` and `gpt-4o-mini` are listed as
  supported), and hosted `web_search`, `include`, `max_tool_calls` and `tool_choice` all pass through.
  `store` is forced to false (we already send false). Web search costs $10 per 1k plus tokens.
- Each response's usage carries `usage.cost` (exact USD). `previous_response_id` is not supported
  (we don't use it). Claude models on `/v1/responses` drop `web_search`.

Code: `resolve_connection()` in `openai_provider.py` reads `OPENAI_API_KEY` or `TM_API_KEY`. A
`tm_vk_` key goes to RouterPlus automatically, and `OPENAI_BASE_URL` overrides that. `check()` now
uses `models.list()` because RouterPlus has no per-model route. `Usage.billed_usd` and
`RunMetrics.billed_usd` hold the exact cost; the UI shows "Billed cost" when every call reported one.
Not yet run live: the user still has to create a `.env` with `TM_API_KEY`.

## 0. Session 2 changes

- `OpenAIProvider.check()` + CLI `--check`: confirms the key works and the model exists (models.retrieve, 0 tokens).
- Search request: if the API returns 400 for an optional setting (`max_tool_calls`,
  `include`, `search_context_size`, `user_location`), the provider drops it, retries, and
  remembers it in `provider.dropped_features` (shown as a CLI note and a UI warning).
  Other errors are raised as before.
- Search count: only `web_search_call` items with `action.type == "search"` (or no type)
  count, since `open_page` / `find_in_page` aren't billed (per OpenAI docs).
- Cost estimate now matches OpenAI pricing (checked 2026-10-10): gpt-4.1-mini $0.40 in /
  $0.10 cached / $1.60 out per 1M, $10 per 1k searches. `search_content_tokens_per_search` is 0:
  a live run showed usage.input_tokens already includes the search content (see "Live runs").
- UI: the Enrich button has `key="enrich"`; the test now clicks it by key, not position.
- Docs confirm: `web_search` tool, `include=["web_search_call.action.sources"]`,
  `search_context_size`, `user_location`, and `tool_choice="required"` are all valid for the
  Responses API; `gpt-4.1-mini` is listed as supported (search context limited to 128k).

## 1. Goal (from the user)

Build an AI search tool that enriches card-transaction **merchant signatures** with GenAI while
**optimising tokens and the number of web-search calls**. The model decides what goes to web
search, and the pipeline keeps searches to a minimum.

User decisions:
- **Language:** Python. **Design:** provider-agnostic, but the MVP uses **OpenAI for both the
  model and web search** (the user's key; Responses API + built-in `web_search` tool).
- **Input:** manual typing in a **UI** (one signature per line), plus CSV/JSONL upload.
- **Defaults (agreed):** confidence threshold 0.8, batch size 20, max 20 searches per run,
  1 search per merchant, low search context.
- **Location:** `/Users/manaskinkar/.cline/data/workspaces/chat/txn-enrichment`
- **API key:** the user puts `OPENAI_API_KEY=...` in `txn-enrichment/.env` themselves (copy
  `.env.example`). Never paste the key into chat. **There is no `.env` yet.**

## 2. Environment

- macOS, Python 3.14.4, project venv at `txn-enrichment/.venv`.
- Installed in the venv: openai 3.28.0, streamlit 1.65.0, pydantic 2.14, pandas 3.0.6,
  rapidfuzz 3.14.6, pytest 9.1.1, python-dotenv, pyyaml (`requirements.txt`).
- The shell is zsh: `${!var}` fails, and an unmatched `*.db` glob prints "no matches found".

## 3. Pipeline (how it saves tokens and searches)

```
typed signatures → 1 normalize → 2 dedupe (exact + conservative fuzzy) → 3 SQLite cache
  → 4 known-merchant rules (YAML) → 5 batched LLM triage with NO tools (can't search)
  → confidence ≥ 0.8: done | else → 6 budget planner → 7 OpenAI web_search (max_tool_calls=1)
  → merge with triage → write to cache
```

- Each unique merchant is processed **once per run** and **cached across runs** (30-day TTL).
- Planner order: transactions sharing the merchant (desc), then total spend (desc), then
  confidence (asc). Merchants past the budget get path `budget_exhausted` (triage answer kept).
- `budget_exhausted` and `error` results are **not cached**, so a later run can retry them.
- The search call sends only: cleaned name, raw (≤80 chars), the model's own query, known fields,
  missing fields, location hint. Uses `tool_choice="required"`, `search_context_size="low"`,
  `user_location` when an ISO-2 country is given, strict JSON schema, `store=False`.
- Output paths: `cache | rule | llm | llm+search | budget_exhausted | error`.
- Output fields: merchant_name (store name; UI column "store name"), brand (consumer
  brand/chain behind the store, never a payment processor), category (fixed list of 16 in `models.py`), subcategory,
  website, merchant_type (online/physical/both/unknown), location, confidence, sources,
  low_confidence, note.

## 4. Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI: text area, Load examples, optional amount/MCC/country, Enrich button, run stats (records, unique merchants, searches vs budget, searches avoided, tokens, LLM calls, est. cost), table of where each merchant was resolved, results table, CSV/JSON download. Sidebar: provider (OpenAI / Offline demo), model, threshold, budget, batch size, search context, clear cache, file upload. |
| `src/txn_enrich/normalize.py` | No-LLM cleanup: processor prefixes (SQ*, TST*, PAYPAL*), store numbers, dates, phone numbers, reference codes, noise words; trailing "CITY ST" kept as `location_hint`. |
| `src/txn_enrich/planner.py` | `cluster_keys` fuzzy dedupe (same word count + same first word + each word ratio ≥ 90); `allocate_budget`. |
| `src/txn_enrich/enricher.py` | Orchestrator `Enricher.run()` → `RunResult(records, metrics)`. Searches run in a 4-worker thread pool. |
| `src/txn_enrich/llm/base.py` | Provider interface: `triage(items)`, `search_enrich(request, max_searches)`; `Usage`. |
| `src/txn_enrich/llm/openai_provider.py` | OpenAI Responses API implementation; counts `web_search_call` items; cited URLs first. |
| `src/txn_enrich/llm/prompts.py` | Short system prompts and strict JSON schemas; compact JSON inputs. |
| `src/txn_enrich/llm/fake.py` | Deterministic offline provider (tests and the UI's "Offline demo"). |
| `src/txn_enrich/cache.py` | SQLite cache with TTL (thread-safe). |
| `src/txn_enrich/rules.py` + `data/known_merchants.yaml` | ~19 big brands resolved with 0 tokens. |
| `src/txn_enrich/inputs.py` | `parse_signatures` (typed text), `load_transactions` (CSV/JSONL/JSON/text). |
| `src/txn_enrich/config.py` + `config.yaml` | Settings. Env overrides: `OPENAI_MODEL`, `TXN_CACHE_PATH`. `demo_cache_path()` gives fake runs their own `cache.demo.db`. |
| `src/txn_enrich/metrics.py` | Run counters and cost estimate (prices in config.yaml: gpt-4.1-mini, checked 2026-10-10). |
| `src/txn_enrich/cli.py` | `PYTHONPATH=src .venv/bin/python -m txn_enrich.cli [--fake] [--json] [-f file] "SIG" ...` |
| `src/txn_enrich/activity.py` | Renders progress events as the live "Search activity" log. |
| `tests/` | 59 offline tests (brand field, activity feed, normalize, rules, dedupe, budget, caching, batching, errors, OpenAI request shape via stub client, input loaders, headless UI via Streamlit AppTest). |
| `README.md` | User-facing docs. |

## 5. Commands

```bash
cd /Users/manaskinkar/.cline/data/workspaces/chat/txn-enrichment
cp .env.example .env              # user adds OPENAI_API_KEY
.venv/bin/streamlit run app.py    # UI at http://localhost:8501
.venv/bin/python -m pytest -q     # offline tests
PYTHONPATH=src .venv/bin/python -m txn_enrich.cli --fake -f data/sample_transactions.csv
```

## 6. Verified so far

- All 59 tests pass. The CLI with `--fake` on the sample CSV: 10 records → 8 unique merchants,
  5 resolved by rules, 3 searches (vs 10 if every record were searched).
- With no key, the CLI exits with a clear error (code 2) and the UI disables Enrich.
- The Streamlit server started headless and returned HTTP 200; no server is left running.
- Tests never write to the real `cache.db`. The project root currently has no `.db` files.

## 7. NOT verified / next steps

0. First run `PYTHONPATH=src .venv/bin/python -m txn_enrich.cli --check` (free).
1. **Real OpenAI smoke test** once `.env` exists:
   `PYTHONPATH=src .venv/bin/python -m txn_enrich.cli "SQ *BLUE BOTTLE #123 OAKLAND CA" "KOPI KOPI COFFEE ROASTERS"`
   Check that the triage JSON parses, the search returns `web_search_call` + sources, and the counts look right.
2. **Model:** OpenAI docs list `gpt-4.1-mini` as supporting web search, but it hasn't been
   checked against the user's account (`--check` does this). Rejected optional settings are now
   dropped automatically. A rejection of `tool_choice="required"` used together with json_schema
   `text.format` is **not** handled automatically; if it happens, fix `_create_search` in
   `openai_provider.py`.
3. **Cost check: done.** See "Live runs" below.

## Live runs (2026-10-10, RouterPlus, gpt-4.1-mini)
- `--check`: ok. A key one hex character short gives a plain 401 "missing or invalid API key";
  valid keys are `tm_vk_` + 48 hex characters.
- 2 well-known merchants (Blue Bottle, Kopi Kopi): 1 triage call, 0 searches, 403 in / 116 out,
  est $0.000347 vs billed $0.000346.
- `PAYPAL *JKLMSTUDIO 4029357733`: path `llm+search`, 1 search, 5 sources, no dropped features,
  9,176 in / 92 out, billed $0.013816. The old estimate (with +8,000 tokens per search) was
  $0.017018, so search content is already in usage; set to 0 and the estimate is now $0.013818.
- Note: that obscure merchant came back with confidence 0.9 and category Other, from loosely
  matching sources. The model may be overconfident on ambiguous searches; consider a lower
  confidence cap when the sources' domains don't match the merchant name.
4. Known limitations: US-centric normalization (a trailing `CO` is read as Colorado); conservative
   fuzzy merging.
5. Ideas: tool-calling mode with the budget enforced in the tool handler; more providers (implement
   `triage` + `search_enrich`); MCC-based rules; tracking savings over time.

## 8. Workspace rule

`/Users/manaskinkar/.cline/data/workspaces/chat` is a shared chat workspace (see its AGENTS.md).
Keep all work inside `txn-enrichment/` and don't put files at the top level.

