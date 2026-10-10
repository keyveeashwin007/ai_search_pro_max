// Side drawer with everything known about one merchant, plus what the pipeline did for it.
import { $, el, clear, sourceLinks, pct, PATH_LABEL } from "./util.js";

export function openDrawer(r, events) {
  $("drawer-title").textContent = r.merchant_name || r.raw_signature;
  const fields = [
    ["Signature", r.raw_signature], ["Brand", r.brand], ["Category", r.category],
    ["Subcategory", r.subcategory], ["Type", r.merchant_type], ["Location", r.location],
    ["Website", r.website], ["Confidence", pct(r.confidence)],
    ["How", PATH_LABEL[r.path] || r.path], ["Note", r.note],
  ].filter(([, v]) => v);
  const body = clear($("drawer-body"));
  body.append(el("dl", { class: "kv" },
    fields.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])));
  const links = sourceLinks((r.sources || "").split(" ").filter(Boolean));
  if (links.length) {
    body.append(el("h3", { text: "Sources" }),
      el("div", { class: "src" }, links.flatMap((a, i) => (i ? [el("br"), a] : [a]))));
  }
  const trail = events.filter((e) => e.key === r.normalized);
  if (trail.length) {
    body.append(el("h3", { text: "What happened" }),
      el("ol", { class: "timeline" }, trail.map((e) => el("li", { text: e.message }))));
  }
  $("drawer").hidden = false;
  $("scrim").hidden = false;
  $("drawer-close").focus();
}

export function closeDrawer() {
  $("drawer").hidden = true;
  $("scrim").hidden = true;
}
