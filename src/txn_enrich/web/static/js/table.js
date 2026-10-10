// Results table: sort, filter, group duplicates, click a row for details.
import { $, el, clear, safeUrl, pct, PATH_LABEL, PATH_HELP } from "./util.js";
import { openDrawer } from "./drawer.js";
import { withToken } from "./api.js";

const COLUMNS = [
  ["merchant_name", "Store"], ["brand", "Brand"], ["category", "Category"],
  ["website", "Website"], ["confidence", "Confidence"], ["path", "How"],
];
const view = { rows: [], events: [], sort: ["", 1], path: "", q: "", cat: "", low: false, group: false };
const pathClass = (p) => "p-" + p.replace("+", "-");

export function showTable(rows, events, jobId) {
  Object.assign(view, { rows, events, sort: ["", 1], path: "", q: "", cat: "", low: false });
  $("q").value = "";
  $("low").checked = false;
  const cats = [...new Set(rows.map((r) => r.category).filter(Boolean))].sort();
  clear($("cat")).append(el("option", { value: "", text: "All categories" }),
    cats.map((c) => el("option", { value: c, text: c })));
  const chips = clear($("path-chips"));
  for (const p of [...new Set(rows.map((r) => r.path))]) {
    const n = rows.filter((r) => r.path === p).length;
    chips.append(el("button", {
      class: "tag " + pathClass(p), type: "button", "aria-pressed": "false", "data-path": p,
      title: PATH_HELP[p] || "", text: `${PATH_LABEL[p] || p} · ${n}`,
      onclick: () => { view.path = view.path === p ? "" : p; syncChips(); draw(); },
    }));
  }
  $("dl-csv").href = withToken(`/api/jobs/${jobId}/download.csv`);
  $("dl-json").href = withToken(`/api/jobs/${jobId}/download.json`);
  $("table-wrap").hidden = false;
  draw();
}

function syncChips() {
  for (const b of $("path-chips").children) b.setAttribute("aria-pressed", String(b.dataset.path === view.path));
}

export function bindTableControls() {
  $("q").addEventListener("input", (e) => { view.q = e.target.value.toLowerCase(); draw(); });
  $("cat").addEventListener("change", (e) => { view.cat = e.target.value; draw(); });
  $("low").addEventListener("change", (e) => { view.low = e.target.checked; draw(); });
  $("group").addEventListener("change", (e) => { view.group = e.target.checked; draw(); });
}

function visibleRows() {
  const q = view.q;
  const matches = (r) => !q || [r.raw_signature, r.merchant_name, r.brand, r.category, r.website]
    .some((v) => (v || "").toLowerCase().includes(q));
  let rows = view.rows.filter((r) => (!view.path || r.path === view.path) &&
    (!view.cat || r.category === view.cat) && (!view.low || r.low_confidence) && matches(r));
  if (view.group) {
    const seen = new Map();
    for (const r of rows) {
      const g = seen.get(r.normalized);
      if (g) g.count += 1;
      else seen.set(r.normalized, { ...r, count: 1 });
    }
    rows = [...seen.values()];
  }
  const [col, dir] = view.sort;
  if (!col) return rows;
  return [...rows].sort((a, b) => {
    const x = a[col] ?? "", y = b[col] ?? "";
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
  });
}

function cell(r, col) {
  if (col === "merchant_name") {
    return el("td", {}, r.merchant_name || "–",
      r.count > 1 && el("span", { class: "count-badge", text: `×${r.count}` }),
      r.location && el("span", { class: "sub", text: r.location }));
  }
  if (col === "website") {
    const u = r.website && safeUrl(/^https?:/i.test(r.website) ? r.website : "https://" + r.website);
    return el("td", {}, u ? el("a", {
      href: u.href, target: "_blank", rel: "noopener noreferrer",
      onclick: (e) => e.stopPropagation(), text: r.website,
    }) : "–");
  }
  if (col === "confidence") {
    return el("td", {}, el("span", { class: "conf" + (r.low_confidence ? " low" : "") },
      el("i", {}, el("b", { style: `width:${Math.round(r.confidence * 100)}%` })), pct(r.confidence)));
  }
  if (col === "path") {
    return el("td", {}, el("span", { class: "tag " + pathClass(r.path),
      title: PATH_HELP[r.path] || "", text: PATH_LABEL[r.path] || r.path }));
  }
  return el("td", { text: r[col] || "–" });
}

function draw() {
  const rows = visibleRows();
  const head = el("tr", {}, el("th", { text: "Signature" }));
  for (const [col, label] of COLUMNS) {
    const dir = view.sort[0] === col ? (view.sort[1] > 0 ? "ascending" : "descending") : "none";
    const th = el("th", { text: label, tabindex: "0", "aria-sort": dir });
    const sort = () => { view.sort = [col, view.sort[0] === col ? -view.sort[1] : 1]; draw(); };
    th.addEventListener("click", sort);
    th.addEventListener("keydown", (e) => { if (e.key === "Enter") sort(); });
    head.append(th);
  }
  const body = el("tbody");
  for (const r of rows) {
    const tr = el("tr", { tabindex: "0" }, el("td", { class: "raw", text: r.raw_signature }),
      COLUMNS.map(([col]) => cell(r, col)));
    const open = () => openDrawer(r, view.events);
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    body.append(tr);
  }
  clear($("table")).append(el("thead", {}, head), body);
  $("table-note").textContent =
    `${rows.length} of ${view.rows.length} shown. Click a row for details and the search trail.`;
}
