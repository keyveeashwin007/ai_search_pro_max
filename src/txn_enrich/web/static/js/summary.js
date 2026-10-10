// Summary cards shown above the table once a run finishes.
import { $, el, clear, money } from "./util.js";

export function showSummary(m, provider) {
  const stat = (value, label, good) => el("div", { class: "stat" + (good ? " good" : "") },
    el("b", { text: String(value) }), el("span", { text: label }));
  const cost = m.billed_complete ? money(m.billed_usd) : "≈ " + money(m.est_cost_usd);
  const notes = [`${m.llm_calls} model call(s) via ${provider}.`];
  if (m.errors) notes.push(`${m.errors} lookup error(s).`);
  if (m.budget_exhausted) notes.push(`${m.budget_exhausted} merchant(s) skipped: search budget used up.`);
  const box = clear($("summary"));
  box.className = "cards";
  box.append(
    stat(m.records, "transactions"),
    stat(m.unique_merchants, "unique merchants"),
    stat(m.web_searches, `web searches (naive: ${m.naive_searches})`, true),
    stat(m.searches_avoided, "searches avoided", true),
    stat(cost, m.billed_complete ? "billed cost" : "estimated cost"),
    stat((m.input_tokens + m.output_tokens).toLocaleString(), "tokens"),
    stat(m.cache_hits + m.rule_hits, "free (saved + known brands)"),
    el("p", { class: "muted small saving", text: notes.join(" ") }));
  box.hidden = false;
}
