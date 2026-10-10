// Entry point: theme, run button, live event stream.
import { $, toast } from "./util.js";
import { state, store } from "./state.js";
import { LiveView } from "./live.js";
import { showSummary } from "./summary.js";
import { showTable, bindTableControls } from "./table.js";
import { closeDrawer } from "./drawer.js";
import { initInputs, bindInputs, requestBody, setProvider } from "./input.js";
import { api, login, setToken, withToken } from "./api.js";

const live = new LiveView();
let running = false;

// ------------------------------------------------------------------ theme
const applyTheme = (t) => { document.documentElement.dataset.theme = t; };
applyTheme(store.get("theme", matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  store.set("theme", next);
});

// ------------------------------------------------------------------ run
function formError(msg) {
  $("form-error").textContent = msg || "";
  $("form-error").hidden = !msg;
}

function setRunning(on) {
  running = on;
  $("run").disabled = on;
  $("run").firstChild.textContent = on ? "Working… " : "Enrich ";
}

async function run() {
  if (running) return;
  formError("");
  setRunning(true);
  let res, data;
  try {
    res = await api("/api/enrich", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody()),
    });
    data = await res.json();
  } catch {
    setRunning(false);
    return formError("Could not reach the server. Is it still running?");
  }
  if (res.status === 401) {  // login expired
    setRunning(false);
    setToken("");
    return showLogin("Your session expired. Sign in again.");
  }
  if (!res.ok) {
    setRunning(false);
    return formError(data.error || "Something went wrong.");
  }
  for (const id of ["empty", "summary", "table-wrap"]) $(id).hidden = true;
  $("live").hidden = false;
  live.reset();
  follow(data.job_id);
}

async function finish(jobId, events) {
  const job = await (await api(`/api/jobs/${jobId}`)).json();
  showSummary(job.result.metrics, job.result.provider);
  showTable(job.result.rows, events, jobId);
  for (const d of job.result.dropped) toast(`The gateway does not support "${d}"; ran without it.`);
  const fresh = await (await api("/api/config")).json();
  if (fresh.cache) state.cfg.cache = fresh.cache;
  setProvider(state.provider);
  $("summary").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function follow(jobId) {
  const events = [];
  const source = new EventSource(withToken(`/api/jobs/${jobId}/events`));
  source.addEventListener("progress", (m) => {
    const e = JSON.parse(m.data);
    events.push(e);
    live.handle(e);
  });
  source.addEventListener("finished", async (m) => {
    source.close();
    const { status, error } = JSON.parse(m.data);
    setRunning(false);
    if (status !== "done") {
      $("live-msg").textContent = "Run failed.";
      return formError(error || "The run failed.");
    }
    await finish(jobId, events);
  });
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) {
      setRunning(false);
      formError("Lost connection to the server.");
    }
  };
}

$("run").addEventListener("click", run);
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run();
  if (e.key === "Escape") closeDrawer();
});
$("drawer-close").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
// ------------------------------------------------------------------ login + start-up
let booted = false;

function showLogin(message = "") {
  $("login").hidden = false;
  $("login-error").textContent = message;
  $("login-error").hidden = !message;
  $("password").value = "";
  $("password").focus();
}

async function start() {
  let cfg;
  try {
    cfg = await (await api("/api/config")).json();
  } catch {
    $("status-pill").textContent = "server unreachable";
    return;
  }
  if (!cfg.authed) return showLogin();
  $("login").hidden = true;
  state.cfg = cfg;
  if (!booted) {
    booted = true;
    bindTableControls();
    bindInputs();
  }
  initInputs();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await login($("password").value);
  } catch (err) {
    return showLogin(err.message);
  }
  start();
});

start();
