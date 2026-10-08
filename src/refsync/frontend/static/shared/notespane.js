// Notes pane: every highlight, note and snip on the current paper, in reading
// order, with filters (kind, project) and inline editing. Shared by both apps.

import { COLORS, escapeHtml } from "./annotations-api.js";

const KIND_LABEL = { highlight: "Highlight", note: "Note", snip: "Snip" };

export class NotesPane {
  /**
   * @param {HTMLElement} el
   * @param {AnnotationSet} set
   * @param {object} opts { onJump(item), currentPage(): number, onWrite: async () => {} }
   */
  constructor(el, set, opts = {}) {
    this.el = el;
    this.set = set;
    this.opts = opts;
    this.kind = "all";
    this.project = "";
    this.editing = null;
    this.projects = [];
    el.classList.add("rsa-pane");
    set.onChange(() => this.render());
    el.addEventListener("click", (e) => this._onClick(e));
    el.addEventListener("change", (e) => this._onChange(e));
    el.addEventListener("keydown", (e) => this._onKeydown(e));
    el.addEventListener("focusout", (e) => this._onBlur(e));
    // A click on a button can blur a note being edited, which saves it and
    // re-renders; re-rendering between mousedown and click would swallow the
    // click. So hold renders while a pointer is down in the pane.
    this._pointerDown = false;
    this._renderPending = false;
    el.addEventListener("pointerdown", () => (this._pointerDown = true));
    const release = () => {
      if (!this._pointerDown) return;
      this._pointerDown = false;
      if (this._renderPending) setTimeout(() => this._renderPending && this.render(), 0); // after the click
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    this.render();
  }

  async refreshProjects() {
    this.projects = await this.set.projects();
    this.render();
  }

  _filtered() {
    return this.set.items.filter((a) => {
      if (this.kind !== "all" && a.kind !== this.kind) return false;
      if (this.project === "__none__" && a.projects.length) return false;
      if (this.project && this.project !== "__none__" && !a.projects.some((p) => p.id === this.project)) return false;
      return true;
    });
  }

  render() {
    if (this._pointerDown) {
      this._renderPending = true;
      return;
    }
    this._renderPending = false;
    if (this.editing && this.el.contains(document.activeElement)) return; // don't clobber typing
    const items = this._filtered();
    const c = this.set.counts();
    const projects = new Map();
    for (const p of this.projects) projects.set(p.id, p.name);
    for (const a of this.set.items) for (const p of a.projects) projects.set(p.id, p.name);
    const head = `
      <div class="rsa-pane-head">
        <div class="rsa-pane-title">Notes &amp; highlights
          <span class="rsa-faint">${c.highlight} highlights · ${c.note} notes · ${c.snip} snips</span></div>
        <div class="rsa-row">
          <select class="rsa-select" data-filter="kind">
            ${["all", "highlight", "note", "snip"]
              .map((k) => `<option value="${k}"${k === this.kind ? " selected" : ""}>${k === "all" ? "Everything" : KIND_LABEL[k] + "s"}</option>`)
              .join("")}
          </select>
          <select class="rsa-select" data-filter="project">
            <option value="">All projects</option>
            ${[...projects]
              .map(([id, name]) => `<option value="${escapeHtml(id)}"${id === this.project ? " selected" : ""}>${escapeHtml(name || id)}</option>`)
              .join("")}
            <option value="__none__"${this.project === "__none__" ? " selected" : ""}>Not tagged</option>
          </select>
          <span class="rsa-spacer"></span>
          <button type="button" class="rsa-btn" data-act="new-note" title="A note on this page (no highlight)">+ Note</button>
        </div>
      </div>`;
    const empty = this.set.paperKey
      ? `<div class="rsa-empty">${
          this.set.items.length
            ? "Nothing matches these filters."
            : "Select text in the PDF to highlight it (h) or add a note (n). Use ✂ Snip to save a figure."
        }</div>`
      : `<div class="rsa-empty">Open a paper to see its notes.</div>`;
    const list = items.length ? items.map((a) => this._item(a)).join("") : empty;
    this.el.innerHTML = head + `<div class="rsa-list">${list}</div>`;
  }

  _item(a) {
    const color = COLORS[a.color] || (a.kind === "snip" ? "#94a3b8" : a.kind === "note" ? "#cbd5e1" : COLORS.yellow);
    const editing = this.editing === a.id;
    const pl = this.set.placement(a); // page in the PDF that's open, if it's another version
    const where = pl.page ? `p. ${pl.page}` : "whole paper";
    const tags = a.projects.map((p) => `<span class="rsa-chip on">${escapeHtml(p.name || p.id)}</span>`).join("");
    const orphan =
      pl.state === "orphaned"
        ? `<div class="rsa-warn">Couldn't find this passage in this version of the PDF.</div>`
        : "";
    const note = editing
      ? `<textarea class="rsa-note" data-edit="${a.id}" rows="3" placeholder="Add a note…">${escapeHtml(a.note || "")}</textarea>`
      : a.note
        ? `<div class="rsa-item-note" data-act="edit" title="Click to edit">${escapeHtml(a.note)}</div>`
        : a.kind !== "note"
          ? `<div class="rsa-item-note rsa-placeholder" data-act="edit">Add a note…</div>`
          : "";
    return `
      <div class="rsa-item${a.starred ? " rsa-starred" : ""}" data-id="${a.id}" style="--rsa-c:${color}">
        <div class="rsa-item-head">
          <span class="rsa-kind">${KIND_LABEL[a.kind]}</span>
          <button type="button" class="rsa-link" data-act="jump" ${a.page ? "" : "disabled"}>${where}</button>
          ${tags}
          <span class="rsa-spacer"></span>
          ${a.kind === "snip" ? `<button type="button" class="rsa-icon" data-act="star" title="${a.starred ? "This paper's cover" : "Make this the paper's cover"}">${a.starred ? "★" : "☆"}</button>` : ""}
          <button type="button" class="rsa-icon" data-act="tags" title="Tag with projects">#</button>
          <button type="button" class="rsa-icon" data-act="delete" title="Delete">✕</button>
        </div>
        ${a.kind === "snip" ? `<img class="rsa-item-img" data-act="jump" src="${this.set.imageUrl(a.image)}" alt="snip p. ${a.page}">` : ""}
        ${a.quote ? `<blockquote class="rsa-quote" data-act="jump">${escapeHtml(a.quote)}</blockquote>` : ""}
        ${orphan}
        ${note}
        ${this.tagging === a.id ? this._tagPicker(a) : ""}
      </div>`;
  }

  _tagPicker(a) {
    const on = new Set(a.projects.map((p) => p.id));
    if (!this.projects.length) return `<div class="rsa-faint rsa-pad">No explore projects yet.</div>`;
    return `<div class="rsa-projects">${this.projects
      .map(
        (p) =>
          `<button type="button" class="rsa-chip${on.has(p.id) ? " on" : ""}" data-tag="${escapeHtml(p.id)}">${on.has(p.id) ? "✓ " : "+ "}${escapeHtml(p.name || p.id)}</button>`,
      )
      .join("")}</div>`;
  }

  async _onClick(e) {
    const act = e.target.closest("[data-act]");
    const tagBtn = e.target.closest("[data-tag]");
    const itemEl = e.target.closest(".rsa-item");
    const a = itemEl ? this.set.get(itemEl.dataset.id) : null;
    try {
      if (tagBtn && a) {
        const id = tagBtn.dataset.tag;
        const has = a.projects.some((p) => p.id === id);
        const next = has
          ? a.projects.filter((p) => p.id !== id)
          : [...a.projects, this.projects.find((p) => p.id === id)].filter(Boolean);
        await this.set.update(a.id, { projects: next.map((p) => ({ id: p.id, name: p.name })) });
        return;
      }
      if (!act) return;
      const what = act.dataset.act;
      if (what === "new-note") return this._newNote();
      if (!a) return;
      if (what === "jump" && this.opts.onJump) this.opts.onJump(a);
      else if (what === "edit") {
        this.editing = a.id;
        this.render();
        const ta = this.el.querySelector(`[data-edit="${a.id}"]`);
        if (ta) ta.focus();
      } else if (what === "star") await this.set.star(a.id, !a.starred);
      else if (what === "tags") {
        if (!this.projects.length) this.projects = await this.set.projects();
        this.tagging = this.tagging === a.id ? null : a.id;
        this.render();
      } else if (what === "delete") {
        if ((a.note || a.kind !== "highlight") && !confirm(`Delete this ${KIND_LABEL[a.kind].toLowerCase()}?`)) return;
        await this.set.remove(a.id);
      }
    } catch (err) {
      this.set.notify(err.message, "error");
    }
  }

  _onChange(e) {
    const f = e.target.dataset.filter;
    if (f === "kind") this.kind = e.target.value;
    if (f === "project") this.project = e.target.value;
    if (f) this.render();
  }

  _onKeydown(e) {
    if (e.target.dataset.edit && e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      e.target.blur();
    } else if (e.target.dataset.edit && e.key === "Escape") {
      e.stopPropagation();
      e.target.blur();
    }
  }

  async _onBlur(e) {
    const id = e.target.dataset && e.target.dataset.edit;
    if (!id) return;
    const value = e.target.value.trim();
    this.editing = null;
    const a = this.set.get(id);
    try {
      if (id === "__new__") {
        if (value) {
          if (this.opts.onWrite) await this.opts.onWrite();
          const page = this.opts.currentPage ? this.opts.currentPage() : null;
          await this.set.create({ kind: "note", note: value, page });
        } else this.render();
      } else if (a && value !== (a.note || "")) {
        if (a.kind === "note" && !value) {
          if (confirm("Delete this note?")) await this.set.remove(id);
          else this.render();
        } else await this.set.update(id, { note: value });
      } else this.render();
    } catch (err) {
      this.set.notify(err.message, "error");
      this.render();
    }
  }

  _newNote() {
    if (!this.set.paperKey) return;
    this.editing = "__new__";
    const page = this.opts.currentPage ? this.opts.currentPage() : null;
    const list = this.el.querySelector(".rsa-list");
    const box = document.createElement("div");
    box.className = "rsa-item";
    box.innerHTML = `<div class="rsa-item-head"><span class="rsa-kind">New note</span><span class="rsa-faint">${page ? "p. " + page : ""}</span></div>
      <textarea class="rsa-note" data-edit="__new__" rows="3" placeholder="Write a note… (Ctrl+Enter to save)"></textarea>`;
    list.prepend(box);
    const empty = list.querySelector(".rsa-empty");
    if (empty) empty.remove();
    box.querySelector("textarea").focus();
  }
}
