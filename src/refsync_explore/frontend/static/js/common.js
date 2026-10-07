// Shared helpers for refsync-explore pages.

export const CDN = {
  alpine: "https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/module.esm.js",
  sortable: "https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/modular/sortable.core.esm.js",
  pdfjs: "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.min.mjs",
  pdfjsWorker: "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.worker.min.mjs",
  pdfjsViewer: "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/web/pdf_viewer.mjs",
};

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* empty body */
  }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    if (msg && typeof msg === "object") msg = msg.message || JSON.stringify(msg);
    throw new ApiError(msg || `Request failed (${res.status})`, res.status);
  }
  return data;
}

// --- toasts ---------------------------------------------------------------
export function toast(message, type = "info", ms = 3200) {
  let box = document.querySelector(".toasts");
  if (!box) {
    box = document.createElement("div");
    box.className = "toasts";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function toastError(e) {
  toast(e && e.message ? e.message : String(e), "error", 5000);
}

// --- theme (same idea as refsync's dark toggle; per-browser) -----------------
export function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set) return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("explore-theme", next);
  } catch (_) {
    /* storage unavailable */
  }
  return next;
}

// --- misc -------------------------------------------------------------------
export function timeAgo(iso) {
  if (!iso) return "";
  const t = new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toLocaleDateString();
}

export function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

// MathJax: titles/abstracts from ADS often contain $...$
let typesetTimer = null;
export function typesetSoon(root) {
  clearTimeout(typesetTimer);
  typesetTimer = setTimeout(() => {
    if (window.MathJax && window.MathJax.typesetPromise) {
      window.MathJax.typesetPromise(root ? [root] : undefined).catch(() => {});
    }
  }, 60);
}

export function isTyping(e) {
  const t = e.target;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

export function refsyncUrl(port, path = "/") {
  return `${location.protocol}//${location.hostname}:${port}${path}`;
}
