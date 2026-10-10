// Every call to the server goes through here: API base URL (the page may be hosted on another
// site, e.g. Netlify) and the login token (kept in sessionStorage, so closing the tab logs out).

const BASE = String(window.TXN_API || "").replace(/\/+$/, "");
const KEY = "txn.token";

export const getToken = () => { try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; } };
export const setToken = (t) => {
  try { t ? sessionStorage.setItem(KEY, t) : sessionStorage.removeItem(KEY); } catch { /* private mode */ }
};

export const url = (path) => BASE + path;

/** Same as fetch, plus the base URL and the Authorization header. */
export function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(url(path), { ...options, headers });
}

/** For EventSource and download links, which cannot send headers. */
export function withToken(path) {
  const token = getToken();
  return url(path) + (token ? (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token) : "");
}

/** Exchange the shared password for a token. Throws Error(message) when refused. */
export async function login(password) {
  let res, data;
  try {
    res = await fetch(url("/api/login"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    data = await res.json();
  } catch {
    throw new Error("Could not reach the server.");
  }
  if (!res.ok) throw new Error(data.error || "Login failed.");
  setToken(data.token);
}
