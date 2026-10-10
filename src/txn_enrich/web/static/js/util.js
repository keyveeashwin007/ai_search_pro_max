// Small DOM helpers. Everything that comes from the model or the web is inserted with
// textContent (never innerHTML), so a malicious merchant name cannot inject markup.

export const $ = (id) => document.getElementById(id);

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

/** Only http(s) URLs become links; javascript: and data: URLs are dropped. */
export function safeUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

export function cleanUrl(url) {
  const u = safeUrl(url);
  if (!u) return null;
  for (const k of [...u.searchParams.keys()]) {
    if (k.toLowerCase().startsWith("utm_")) u.searchParams.delete(k);
  }
  return u;
}

export function domain(url) {
  const u = safeUrl(url);
  return u ? u.hostname.replace(/^www\./, "") : url;
}

/** One link per website, like the Streamlit panel. */
export function sourceLinks(urls) {
  const seen = new Map();
  for (const raw of urls || []) {
    const u = cleanUrl(raw);
    if (u && !seen.has(domain(raw))) seen.set(domain(raw), u.href);
  }
  return [...seen].map(([d, href]) =>
    el("a", { href, target: "_blank", rel: "noopener noreferrer", text: d }));
}

export function money(v) {
  if (v === null || v === undefined) return "–";
  return "$" + (v < 0.01 ? v.toFixed(4) : v.toFixed(3));
}

export function pct(v) {
  return Math.round((v || 0) * 100) + "%";
}

let toastTimer;
export function toast(message) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2600);
}

export const PATH_LABEL = {
  cache: "saved", rule: "known brand", llm: "model", "llm+search": "web search",
  budget_exhausted: "no search left", error: "error",
};
export const PATH_HELP = {
  cache: "Reused from an earlier run: free",
  rule: "Matched the known-merchant list: free",
  llm: "The model was confident: no web search",
  "llm+search": "Looked up on the web",
  budget_exhausted: "Wanted a search but the budget ran out; model answer kept",
  error: "The lookup failed; model answer kept",
};
