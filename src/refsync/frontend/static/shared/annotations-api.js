// Annotations for one paper (highlights, notes, snips), shared by refsync and explore.
// An AnnotationSet holds the paper's items and talks to /api/annotations; the
// notes pane, the snip gallery and the in-PDF highlighter all use the same set,
// so a change in one shows up in the others.

export const COLORS = {
  yellow: "#facc15",
  green: "#4ade80",
  blue: "#60a5fa",
  pink: "#f472b6",
  purple: "#c084fc",
};

export class ApiError extends Error {}

export async function http(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let data = null;
  try {
    data = await res.json();
  } catch (_) {}
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new ApiError(msg || `Request failed (${res.status})`);
  }
  return data;
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export class AnnotationSet {
  /**
   * @param {object} opts { base = "/api/annotations", currentProject = {id,name}|null, notify(msg, type) }
   */
  constructor(opts = {}) {
    this.base = opts.base || "/api/annotations";
    this.currentProject = opts.currentProject || null;
    this.notify = opts.notify || ((m, t) => (t === "error" ? console.error(m) : console.log(m)));
    this.paperKey = null; // id new annotations are saved under
    this.aliases = []; // all ids this paper's annotations may be stored under
    this.items = [];
    this._listeners = new Set();
    this._projects = null;
    this._token = 0;
    // () => fingerprint of the PDF currently shown for this paper (null if none);
    // set by the Annotator. Decides which stored position of a highlight applies.
    this.fingerprintFn = null;
  }

  get fingerprint() {
    return this.fingerprintFn ? this.fingerprintFn() : null;
  }

  /**
   * Where an annotation sits in the PDF that's open now:
   *   { page, rects, top, state }   state: ok | pending (not looked for yet) | orphaned
   * The position it was made at is used on the same PDF (or when none is open);
   * on another version of the PDF, the position found there by its quote.
   */
  placement(a, fingerprint = this.fingerprint) {
    const original = { page: a.page, rects: a.rects || [], top: a.top || 0, state: "ok" };
    if (!a.page || !original.rects.length) return original;
    if (a.kind !== "highlight" || !a.quote || !fingerprint || !a.pdf_fingerprint) return original;
    if (a.pdf_fingerprint === fingerprint) return original;
    const found = a.anchors && a.anchors[fingerprint];
    if (!found) return { page: a.page, rects: [], top: a.top || 0, state: "pending" };
    if (found.state !== "ok") return { page: a.page, rects: [], top: a.top || 0, state: "orphaned" };
    return { page: found.page, rects: found.rects, top: found.top || 0, state: "ok" };
  }

  /** Tell listeners to redraw (e.g. a different version of the PDF was opened). */
  refresh() {
    this._changed();
  }

  onChange(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  _changed() {
    for (const fn of this._listeners) {
      try {
        fn(this.items);
      } catch (e) {
        console.error(e);
      }
    }
  }

  async setPaper(key, aliases = []) {
    this.paperKey = key;
    this.aliases = [...new Set([key, ...aliases].filter(Boolean))];
    this.items = [];
    this._changed();
    if (key) await this.reload();
  }

  async reload() {
    const token = ++this._token;
    if (!this.paperKey) return;
    try {
      const items = await http("GET", `${this.base}?paper_id=${encodeURIComponent(this.aliases.join(","))}`);
      if (token !== this._token) return; // another paper was opened meanwhile
      this.items = items;
      this._changed();
    } catch (e) {
      this.notify(e.message, "error");
    }
  }

  get(id) {
    return this.items.find((a) => a.id === id) || null;
  }

  _sort() {
    this.items.sort((a, b) => (a.page || 0) - (b.page || 0) || (a.top || 0) - (b.top || 0) || (a.created_at < b.created_at ? -1 : 1));
  }

  async create(data) {
    if (!this.paperKey) throw new Error("No paper open");
    const item = await http("POST", this.base, { paper_id: this.paperKey, projects: [], ...data });
    // the user may have moved on to another paper while this saved
    if (this.aliases.includes(item.paper_id)) {
      this.items.push(item);
      this._sort();
      this._changed();
    }
    return item;
  }

  async update(id, fields) {
    const item = await http("PATCH", `${this.base}/${id}`, fields);
    const i = this.items.findIndex((a) => a.id === id);
    if (i >= 0) {
      if (fields.starred) for (const a of this.items) if (a.id !== id) a.starred = false;
      this.items[i] = item;
      this._sort();
      this._changed();
    }
    return item;
  }

  /** Record where an annotation sits in another version of the PDF. */
  async setAnchor(id, anchor) {
    const item = await http("PUT", `${this.base}/${id}/anchor`, anchor);
    const i = this.items.findIndex((a) => a.id === id);
    if (i >= 0) {
      this.items[i] = item;
      this._changed();
    }
    return item;
  }

  async remove(id) {
    await http("DELETE", `${this.base}/${id}`);
    this.items = this.items.filter((a) => a.id !== id);
    this._changed();
  }

  star(id, on = true) {
    return this.update(id, { starred: on });
  }

  async projects() {
    if (!this._projects) {
      try {
        this._projects = await http("GET", `${this.base}/projects`);
      } catch (_) {
        this._projects = [];
      }
    }
    const list = [...this._projects];
    if (this.currentProject && !list.some((p) => p.id === this.currentProject.id)) list.unshift(this.currentProject);
    // the current project first, then any other project already used on this paper
    if (this.currentProject) {
      list.sort((a, b) => (a.id === this.currentProject.id ? -1 : b.id === this.currentProject.id ? 1 : 0));
    }
    for (const a of this.items) for (const p of a.projects) if (!list.some((x) => x.id === p.id)) list.push(p);
    return list;
  }

  imageUrl(name) {
    return `${this.base}/files/${encodeURIComponent(name)}`;
  }

  counts() {
    const c = { highlight: 0, note: 0, snip: 0 };
    for (const a of this.items) c[a.kind] = (c[a.kind] || 0) + 1;
    return c;
  }
}
