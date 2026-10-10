# Deploying: Netlify front end + Python backend

**Status: written but never run.** The shell in the authoring session was unavailable, so neither
the tests nor a deploy have been tried. Run `.venv/bin/python -m pytest -q` first.

```
Browser ──► Netlify (static page)            https://<site>.netlify.app
   │
   └──────► Python API (Render/Fly/Railway)  https://<api>.onrender.com   ← holds your API key
```

Netlify cannot host the backend: runs are background threads, progress is a long-lived stream
(SSE) and the cache is an on-disk SQLite file. So the page lives on Netlify and calls the API.

## Why there is a password
Anyone who can reach the API can start live runs on **your** API key. So:
- the server **refuses to start** on a public address without `TXN_ACCESS_PASSWORD` (8+ chars);
- every `/api` route except `/api/health` and `/api/login` needs a login token;
- it caps rows, searches per run and simultaneous runs, and only allows your Netlify origin (CORS);
- 10 wrong passwords per minute locks logins for a minute.

One shared password, no user accounts. The login token lasts 12 hours and lives in the tab's
`sessionStorage`. The event stream and the CSV/JSON links carry it in the URL (browsers cannot
send headers there), so it can show up in the API's access logs. It expires, and it is not the
password, but use a password you do not reuse elsewhere.

## 1. Backend (Render example)
1. Push this folder to a Git repo (`.env` is git-ignored: never commit it).
2. Render > New > **Blueprint** > pick the repo (it reads `render.yaml`), or New > Web Service > Docker.
3. Set the environment variables:

| Variable | Value |
|---|---|
| `TXN_ACCESS_PASSWORD` | the shared password (required) |
| `TM_API_KEY` or `OPENAI_API_KEY` | your key (RouterPlus `tm_vk_...` is detected automatically) |
| `TXN_ALLOWED_ORIGINS` | your Netlify URL, no trailing slash. Comma-separate several |
| `TXN_MAX_ROWS` | rows per run (default 1000) |
| `TXN_MAX_SEARCHES` | ceiling for "max web searches" (default 500) |
| `TXN_MAX_RUNNING` | simultaneous runs (default 0 = unlimited) |
| `TXN_ALLOWED_HOSTS` | optional; the hosted server accepts any Host unless you list some |
| `TXN_CACHE_PATH` | set to `/data/cache.db` by the Dockerfile |

4. Check `https://<api>/api/health` returns `{"ok": true}`.

The cache is SQLite. Without a persistent disk (Render free plan) it is lost on every restart,
which only costs repeat lookups. `render.yaml` asks for a 1 GB disk, which needs a paid plan:
delete the `disk:` block to stay free. Keep **one instance**: the jobs and the login rate limit
live in memory, so scaling out breaks following a run.

The container runs as root so a platform-mounted disk is writable. If your host mounts volumes
for a normal user, add a `USER` line.

Other hosts: the same Docker image works on Fly and Railway. Without Docker use
`pip install -r requirements-web.txt` and the start command `python serve_web.py`
(the `PORT` variable is honoured; set `HOST=0.0.0.0`).

## 2. Front end (Netlify)
1. Netlify > Add new site > Import from Git > the same repo. `netlify.toml` supplies the build
   command (`python3 scripts/build_netlify.py`) and publish folder (`dist`).
2. Site configuration > Environment variables: `TXN_API_URL` = the API address, e.g.
   `https://txn-enrichment-api.onrender.com` (https only, no path).
3. Deploy. Then copy the Netlify URL into the backend's `TXN_ALLOWED_ORIGINS` and redeploy it.
4. CLI alternative: `TXN_API_URL=https://<api> python3 scripts/build_netlify.py`, then
   `npx netlify deploy --dir dist --prod`.

The build also writes a `_headers` file: a Content-Security-Policy that only lets the page talk
to itself and your API, `nosniff`, `no-referrer` and no framing.

## 3. Try it
Open the Netlify URL > sign in > "Offline demo" > Load examples > Enrich. Then Live mode with a
couple of rows. If sign-in says "Could not reach the server", check `TXN_API_URL`; if the run
fails right after sign-in, check `TXN_ALLOWED_ORIGINS` matches the Netlify URL exactly
(`https`, no trailing slash, no path).

## Local use is unchanged
`.venv/bin/python serve_web.py` still binds `127.0.0.1:8600` with no login.

## Not done / limits
- No user accounts, per-user quotas or audit log; one password for everyone who has it.
- Job results are kept in memory (last 20 jobs); a restart loses them.
- A Netlify "deploy preview" URL differs from the production URL: add it to `TXN_ALLOWED_ORIGINS`
  if you want to use previews.
