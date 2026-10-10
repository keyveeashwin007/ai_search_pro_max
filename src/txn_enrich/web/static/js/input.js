// Left panel: provider switch, signatures box, file drop, settings.
import { $, toast } from "./util.js";
import { state, store } from "./state.js";
import { api } from "./api.js";

export function setProvider(p) {
  const { cfg } = state;
  state.provider = p;
  for (const b of $("provider-seg").children) b.setAttribute("aria-selected", String(b.dataset.v === p));
  const pill = $("status-pill");
  if (p === "openai") {
    pill.textContent = `${cfg.gateway} · ${cfg.has_key ? "key found" : "no key"}`;
    pill.className = "pill" + (cfg.has_key ? " ok" : "");
    $("provider-note").textContent = cfg.has_key
      ? "Uses the model plus web search. Real calls, real (small) cost."
      : "No API key on the server. Add one to .env and restart, or use the offline demo.";
  } else {
    pill.textContent = "offline demo";
    pill.className = "pill demo";
    $("provider-note").textContent = "No network, no cost. Fake answers just to show the flow.";
  }
  $("cache-note").textContent = `${cfg.cache[p]} saved merchants`;
  store.set("provider", p);
}

function syncThreshold() {
  $("threshold-out").textContent = Number($("threshold").value).toFixed(2);
}

function updateCount() {
  const n = $("sigs").value.split("\n").filter((l) => l.trim() && !l.trim().startsWith("#")).length;
  $("count").textContent = n ? `${n} line${n === 1 ? "" : "s"}` : "";
  store.set("text", $("sigs").value);
}

function setFile(f) {
  state.file = f;
  $("file-chip").hidden = !f;
  $("file-name").textContent = f ? f.name : "";
  $("drop-text").hidden = !!f;
}

async function takeFile(f) {
  if (!f) return;
  if (f.size > 4_000_000) return toast("That file is over 4 MB.");
  setFile({ name: f.name, data: await f.text() });
}

/** Fill the form from the server config + remembered values. */
export function initInputs() {
  const d = state.cfg.defaults, saved = store.get("settings", {});
  $("model").value = saved.model || d.model;
  $("threshold").value = saved.threshold ?? d.threshold;
  $("budget").value = saved.budget ?? d.budget;
  $("batch").value = saved.batch ?? d.batch;
  $("context").value = saved.context_size || d.context_size;
  syncThreshold();
  $("sigs").value = store.get("text", "");
  updateCount();
  setProvider(store.get("provider", state.cfg.has_key ? "openai" : "demo"));
}

export function bindInputs() {
  $("threshold").addEventListener("input", syncThreshold);
  for (const b of $("provider-seg").children) b.addEventListener("click", () => setProvider(b.dataset.v));
  $("sigs").addEventListener("input", updateCount);
  const examples = () => { $("sigs").value = state.cfg.examples; updateCount(); $("sigs").focus(); };
  $("examples").addEventListener("click", examples);
  $("examples2").addEventListener("click", examples);
  $("clear-text").addEventListener("click", () => { $("sigs").value = ""; updateCount(); });

  const drop = $("drop");
  $("browse").addEventListener("click", (e) => { e.preventDefault(); $("file").click(); });
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter") $("file").click(); });
  $("file").addEventListener("change", (e) => { takeFile(e.target.files[0]); e.target.value = ""; });
  $("file-remove").addEventListener("click", () => setFile(null));
  for (const ev of ["dragenter", "dragover"]) {
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  }
  for (const ev of ["dragleave", "drop"]) {
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
  }
  drop.addEventListener("drop", (e) => takeFile(e.dataTransfer.files[0]));

  $("clear-cache").addEventListener("click", async () => {
    let cleared;
    try {
      const res = await api(`/api/cache/clear?provider=${state.provider}`, { method: "POST" });
      ({ cleared } = await res.json());
    } catch {
      return toast("Could not reach the server.");
    }
    if (cleared === undefined) return toast("Could not clear the saved results.");
    state.cfg.cache[state.provider] = 0;
    setProvider(state.provider);
    toast(`Cleared ${cleared} saved merchants`);
  });
}

/** The JSON body for POST /api/enrich; also remembers the settings. */
export function requestBody() {
  const settings = {
    model: $("model").value, threshold: Number($("threshold").value),
    budget: Number($("budget").value), batch: Number($("batch").value),
    context_size: $("context").value,
  };
  store.set("settings", settings);
  return {
    provider: state.provider, text: $("sigs").value, file: state.file, settings,
    context: { amount: $("amount").value, mcc: $("mcc").value, country: $("country").value },
  };
}
