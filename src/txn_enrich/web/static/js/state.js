// Shared UI state and a tiny localStorage wrapper (remembers settings between visits).
export const state = { cfg: null, provider: "demo", file: null };

export const store = {
  get(k, d) {
    try { return JSON.parse(localStorage.getItem("txn." + k)) ?? d; } catch { return d; }
  },
  set(k, v) {
    try { localStorage.setItem("txn." + k, JSON.stringify(v)); } catch { /* private mode */ }
  },
};
