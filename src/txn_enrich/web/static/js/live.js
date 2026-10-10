// Live progress: a stepper, a progress bar and one card per merchant being searched.
import { $, el, clear, sourceLinks, money, pct } from "./util.js";

const STEPS = [
  ["read", "Read input"],
  ["triage", "Ask the model"],
  ["plan", "Plan searches"],
  ["search", "Web search"],
  ["done", "Finish"],
];

export class LiveView {
  constructor() {
    this.reset();
  }

  reset() {
    this.state = Object.fromEntries(STEPS.map(([k]) => [k, "todo"]));
    this.notes = {};
    this.cards = new Map(); // merchant key -> card element
    this.started = 0;
    this.finished = 0;
    this.planned = 0;
    clear($("merchants"));
    $("live-msg").textContent = "Starting…";
    this.draw();
  }

  set(step, status, note) {
    this.state[step] = status;
    if (note !== undefined) this.notes[step] = note;
  }

  /** Apply one ProgressEvent ({stage, message, key, detail}). */
  handle(e) {
    const d = e.detail || {};
    $("live-msg").textContent = e.message;
    switch (e.stage) {
      case "start":
        this.set("read", "done", e.message);
        break;
      case "local":
        this.set("read", "done", e.message);
        this.set("triage", "active");
        break;
      case "triage_start":
        this.set("triage", "active", e.message);
        break;
      case "triage_done":
        this.set("triage", "done", e.message);
        for (const v of d.verdicts || []) if (v.needs_search) this.noteUnsure(v);
        break;
      case "plan":
        this.planned = (d.searching || []).length;
        this.set("triage", "done");
        this.set("plan", "done", e.message);
        this.set("search", this.planned ? "active" : "skip",
          this.planned ? `0 / ${this.planned}` : "none needed");
        for (const key of d.skipped || []) this.card(key, "error", "Skipped: search budget used up");
        break;
      case "search_start":
        this.started += 1;
        this.searching(e);
        break;
      case "search_done":
        this.finished += 1;
        this.found(e);
        break;
      case "search_error":
        this.finished += 1;
        this.failed(e);
        break;
      case "done":
        for (const [k] of STEPS) if (this.state[k] !== "skip") this.state[k] = "done";
        this.set("done", "done", e.message);
        break;
    }
    if (this.planned) this.notes.search = `${this.finished} / ${this.planned}`;
    if (this.planned && this.finished >= this.planned) this.set("search", "done");
    this.draw();
  }

  draw() {
    const list = clear($("stepper"));
    for (const [key, label] of STEPS) {
      list.append(el("li", { class: this.state[key] },
        label, el("small", { text: this.notes[key] || (this.state[key] === "todo" ? "" : "…") })));
    }
    const fill = $("bar-fill");
    if (this.planned) {
      fill.classList.remove("indeterminate");
      fill.style.width = `${(this.finished / this.planned) * 100}%`;
    } else {
      const done = STEPS.filter(([k]) => this.state[k] !== "todo" && this.state[k] !== "active").length;
      fill.classList.toggle("indeterminate", this.state.done !== "done");
      if (this.state.done === "done") fill.style.width = "100%";
      else if (!fill.classList.contains("indeterminate")) fill.style.width = `${(done / STEPS.length) * 100}%`;
    }
  }

  card(key, kind, text) {
    let card = this.cards.get(key);
    if (!card) {
      card = el("div", { class: "m-card" });
      this.cards.set(key, card);
      $("merchants").append(card);
    }
    card.className = "m-card " + kind;
    clear(card).append(el("div", { class: "m-head" }, el("code", { text: key }), text));
    return card;
  }

  noteUnsure(v) {
    this.card(v.key, "", `Only ${pct(v.confidence)} sure, so it will be searched`);
  }

  searching(e) {
    const card = this.card(e.key, "searching");
    clear(card).append(
      el("div", { class: "m-head" }, el("span", { class: "spin" }),
        el("span", { class: "m-title", text: "Searching the web" }), el("code", { text: e.key })),
      el("div", { class: "m-meta", text: `query: ${e.detail.query || e.key}` }));
  }

  found(e) {
    const d = e.detail;
    const card = this.card(e.key, "");
    const title = d.brand && d.brand.toLowerCase() !== (d.name || "").toLowerCase()
      ? `${d.name || "?"} (brand: ${d.brand})` : d.name || "?";
    const meta = el("div", { class: "m-meta" },
      d.category && el("span", { text: d.category }),
      el("span", { text: `${pct(d.confidence)} sure` }),
      el("span", { text: `${d.searches || 0} search${d.searches === 1 ? "" : "es"}` }),
      d.billed_usd != null && el("span", { text: money(d.billed_usd) }));
    const q = (d.queries || []).length &&
      el("div", { class: "m-meta", text: "searched for: " + d.queries.join(" · ") });
    const links = sourceLinks(d.sources);
    clear(card).append(
      el("div", { class: "m-head" }, "✅", el("span", { class: "m-title", text: title }),
        el("code", { text: e.key })),
      meta, q || null,
      links.length ? el("div", { class: "m-meta src" }, "sources: ", links.flatMap((a, i) => i ? [", ", a] : [a])) : null);
  }

  failed(e) {
    const card = this.card(e.key, "error");
    clear(card).append(el("div", { class: "m-head" }, "❌", el("code", { text: e.key })),
      el("div", { class: "m-meta", text: (e.detail || {}).error || "search failed" }));
  }
}
