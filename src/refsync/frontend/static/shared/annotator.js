// In-PDF annotation tools, shared by refsync and explore:
//  - draws highlights / snip outlines on the pages (positions are fractions of
//    the page, so they stay put at any zoom)
//  - select text -> popover: highlight in a color, add a note, copy
//  - click a highlight -> edit its color, note, project tags, or delete it
//  - snip mode: drag a box on a page -> high-res PNG of that region
//  - re-anchoring: if a highlight was made on a different version of the PDF,
//    find its quoted text there (using the text around it to pick the right
//    occurrence) and remember that position for this version only; the
//    original position is never changed
//  - everything is tied to the document actually shown: while another paper's
//    PDF is still on screen, nothing is drawn, selected or snipped

import { COLORS, escapeHtml } from "./annotations-api.js";

const NONSPACE = /\S/;

function clamp01(v) {
  return Math.min(1, Math.max(0, v));
}

/** Client rects -> merged [x0,y0,x1,y1] fractions of the page box. */
export function normalizeRects(clientRects, pageRect) {
  const rects = [];
  for (const r of clientRects) {
    if (r.width < 1 || r.height < 1) continue;
    // ignore rects outside this page (selection spilling onto the next page)
    if (r.bottom < pageRect.top || r.top > pageRect.bottom) continue;
    rects.push([
      clamp01((r.left - pageRect.left) / pageRect.width),
      clamp01((r.top - pageRect.top) / pageRect.height),
      clamp01((r.right - pageRect.left) / pageRect.width),
      clamp01((r.bottom - pageRect.top) / pageRect.height),
    ]);
  }
  rects.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const merged = [];
  for (const r of rects) {
    const last = merged[merged.length - 1];
    const sameLine = last && Math.abs(last[1] - r[1]) < 0.006 && Math.abs(last[3] - r[3]) < 0.006;
    if (sameLine && r[0] <= last[2] + 0.015) {
      last[0] = Math.min(last[0], r[0]);
      last[2] = Math.max(last[2], r[2]);
      last[1] = Math.min(last[1], r[1]);
      last[3] = Math.max(last[3], r[3]);
    } else if (!merged.some((m) => m[0] <= r[0] && m[1] <= r[1] && m[2] >= r[2] && m[3] >= r[3])) {
      merged.push([...r]);
    }
  }
  return merged.map((r) => r.map((v) => Math.round(v * 1e5) / 1e5));
}

function stripWs(s) {
  return (s || "").replace(/\s+/g, "");
}

/**
 * Best occurrence of `target` in `text` (both without whitespace), scored by
 * how much of `prefix` / `suffix` (the text around the original highlight)
 * surrounds it. Returns { index, length, score, max } or null.
 */
export function bestMatch(text, target, prefix = "", suffix = "") {
  if (!target || !text) return null;
  let hay = text;
  let needle = target;
  let pre = stripWs(prefix);
  let suf = stripWs(suffix);
  let idx = hay.indexOf(needle);
  if (idx < 0) {
    hay = text.toLowerCase();
    needle = target.toLowerCase();
    pre = pre.toLowerCase();
    suf = suf.toLowerCase();
    idx = hay.indexOf(needle);
  }
  let best = null;
  const max = pre.length + suf.length;
  while (idx >= 0) {
    let a = 0;
    while (a < pre.length && idx - 1 - a >= 0 && hay[idx - 1 - a] === pre[pre.length - 1 - a]) a++;
    let b = 0;
    const end = idx + needle.length;
    while (b < suf.length && end + b < hay.length && hay[end + b] === suf[b]) b++;
    if (!best || a + b > best.score) best = { index: idx, length: needle.length, score: a + b, max };
    if (best.score === max) break; // can't do better
    idx = hay.indexOf(needle, idx + 1);
  }
  return best;
}

/** Find `quote` in a text layer (ignoring whitespace); returns a DOM Range or null. */
export function findQuoteRange(textLayerDiv, quote, prefix = "", suffix = "") {
  const target = stripWs(quote);
  if (!target || !textLayerDiv) return null;
  const walker = document.createTreeWalker(textLayerDiv, NodeFilter.SHOW_TEXT);
  const map = []; // [node, offset] per non-space char
  let text = "";
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const s = n.nodeValue;
    for (let i = 0; i < s.length; i++) {
      if (NONSPACE.test(s[i])) {
        text += s[i];
        map.push([n, i]);
      }
    }
  }
  const m = bestMatch(text, target, prefix, suffix);
  if (!m) return null;
  const [sn, so] = map[m.index];
  const [en, eo] = map[m.index + m.length - 1];
  const range = document.createRange();
  range.setStart(sn, so);
  range.setEnd(en, eo + 1);
  return range;
}

/** Text just before and after a selection, within its page's text layer. */
function contextOfRange(range, textLayer, n = 40) {
  const flat = (t) => t.replace(/\s+/g, " ");
  let prefix = null;
  let suffix = null;
  if (!textLayer) return { prefix, suffix };
  try {
    if (textLayer.contains(range.startContainer)) {
      const r = document.createRange();
      r.setStart(textLayer, 0);
      r.setEnd(range.startContainer, range.startOffset);
      prefix = flat(r.toString()).slice(-n);
    }
    if (textLayer.contains(range.endContainer)) {
      const r = document.createRange();
      r.setStart(range.endContainer, range.endOffset);
      r.setEnd(textLayer, textLayer.childNodes.length);
      suffix = flat(r.toString()).slice(0, n);
    }
  } catch (_) {}
  return { prefix, suffix };
}

export class Annotator {
  /**
   * @param {PdfReader} reader
   * @param {AnnotationSet} set
   * @param {object} opts { host: element popovers are positioned in (position:relative),
   *                        onWrite: async () => {} called before the first write (e.g. "saving PDF to library") }
   */
  constructor(reader, set, opts = {}) {
    this.reader = reader;
    this.set = set;
    this.host = opts.host || reader.container.parentElement;
    this.onWrite = opts.onWrite || null;
    this.snipMode = false;
    this.pending = null; // current text selection, ready to become a highlight
    this.activeId = null;
    this._reanchored = new Set(); // annotation ids already looked for in the open PDF
    this._reanchorWait = new Map(); // annotation id -> { page, doc }: found, waiting for that page's text layer
    this._jumpAfter = null; // annotation to scroll to once it has been located
    this._snipListeners = new Set();
    this.popover = null;

    // positions depend on which PDF is open, and only if it's this paper's
    set.fingerprintFn = () => (this.docMatches() ? reader.fingerprint : null);
    set.onChange(() => {
      this.redrawAll();
      this._scheduleReanchor();
    });
    reader.on("pagerendered", (n, div) => this.drawPage(n, div));
    reader.on("textlayer", (n, div) => {
      this.drawPage(n, div);
      this._reanchorOnPage(n, div);
    });
    reader.on("document", () => {
      this._reanchored.clear();
      this._reanchorWait.clear();
      this.pending = null;
      this.closePopover();
      set.refresh(); // redraw everything (and re-anchor) against the new document
    });

    const c = reader.container;
    c.addEventListener("mouseup", (e) => this._onMouseUp(e));
    c.addEventListener("click", (e) => this._onClick(e));
    c.addEventListener("mousedown", (e) => this._onMouseDown(e));
    c.addEventListener("scroll", () => this._repositionPopover());
    window.addEventListener("keydown", (e) => this._onKey(e), true);
  }

  onSnipMode(fn) {
    this._snipListeners.add(fn);
  }

  /** Is the PDF on screen the one for the paper whose annotations we hold? */
  docMatches() {
    const r = this.reader;
    return !!(r.doc && r.docKey && this.set.paperKey && this.set.aliases.includes(r.docKey));
  }

  // ------------------------------------------------------------------ drawing
  redrawAll() {
    if (!this.reader.doc) return;
    for (let n = 1; n <= this.reader.pagesCount; n++) {
      const div = this.reader.pageDiv(n);
      if (div && div.querySelector(".canvasWrapper")) this.drawPage(n, div);
    }
  }

  drawPage(pageNumber, pageDiv) {
    if (!pageDiv) return;
    let layer = pageDiv.querySelector(":scope > .rsa-layer");
    if (!layer) {
      layer = document.createElement("div");
      layer.className = "rsa-layer";
      // below the text layer, so text stays selectable through highlights
      const textLayer = pageDiv.querySelector(":scope > .textLayer");
      pageDiv.insertBefore(layer, textLayer || null);
    }
    const html = [];
    const items = this.docMatches() ? this.set.items : []; // another paper's PDF: draw nothing
    for (const a of items) {
      const pl = this.set.placement(a);
      if (pl.state !== "ok" || pl.page !== pageNumber || !pl.rects.length) continue;
      const color = COLORS[a.color] || COLORS.yellow;
      for (const [x0, y0, x1, y1] of pl.rects) {
        const style = `left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%;--rsa-c:${color}`;
        const cls = `rsa-mark rsa-${a.kind}${a.id === this.activeId ? " rsa-active" : ""}${a.note ? " rsa-has-note" : ""}`;
        html.push(`<div class="${cls}" data-id="${a.id}" style="${style}"></div>`);
      }
    }
    layer.innerHTML = html.join("");
  }

  flash(id) {
    this.activeId = id;
    this.redrawAll();
    clearTimeout(this._flashT);
    this._flashT = setTimeout(() => {
      if (this.activeId === id && !this.popover) {
        this.activeId = null;
        this.redrawAll();
      }
    }, 1600);
  }

  jumpTo(item) {
    if (!item || !item.page) return;
    const pl = this.set.placement(item);
    if (pl.state === "pending") {
      // still being located in this version of the PDF: go to where it was
      // found (or used to be) and finish the jump once it's placed
      const w = this._reanchorWait.get(item.id);
      this._jumpAfter = item.id;
      this.reader.scrollTo(w && w.doc === this.reader.doc ? w.page : item.page, 0);
      return;
    }
    if (pl.state === "orphaned") {
      this.set.notify(`This passage wasn't found in this version of the PDF (it was on p. ${item.page}).`);
    }
    this.reader.scrollTo(pl.page, pl.top || 0);
    this.flash(item.id);
  }

  // ------------------------------------------------------------------ geometry
  _pageAt(target) {
    const div = target && target.closest ? target.closest(".page") : null;
    if (!div || !this.reader.container.contains(div)) return null;
    return { div, number: Number(div.dataset.pageNumber) };
  }

  _pointOnPage(e, div) {
    const r = div.getBoundingClientRect();
    return [clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height)];
  }

  // ------------------------------------------------------------------ selection
  _onMouseUp(e) {
    if (this.snipMode) return;
    setTimeout(() => this._captureSelection(e), 0);
  }

  _captureSelection(e) {
    if (!this.docMatches()) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const startEl = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    const page = this._pageAt(startEl);
    if (!page) return;
    const quote = sel.toString().replace(/\s+/g, " ").trim();
    if (!quote) return;
    const rects = normalizeRects(range.getClientRects(), page.div.getBoundingClientRect());
    if (!rects.length) return;
    const textLayer = page.div.querySelector(".textLayer");
    this.pending = {
      page: page.number,
      rects,
      quote,
      ...contextOfRange(range, textLayer),
      doc: this.reader.doc,
      fingerprint: this.reader.fingerprint,
    };
    this._openSelectionPopover(e);
  }

  async highlight(color = "yellow", withNote = false) {
    const p = this.pending;
    if (!p) return null;
    this.closePopover();
    window.getSelection().removeAllRanges();
    if (p.doc !== this.reader.doc || !this.docMatches()) {
      this.pending = null; // the paper changed under the selection
      return null;
    }
    try {
      if (this.onWrite) await this.onWrite();
      const item = await this.set.create({
        kind: "highlight",
        color,
        page: p.page,
        rects: p.rects,
        quote: p.quote,
        prefix: p.prefix,
        suffix: p.suffix,
        pdf_fingerprint: p.fingerprint,
      });
      this.pending = null;
      if (withNote) this.edit(item, { focusNote: true });
      return item;
    } catch (e) {
      this.set.notify(e.message, "error");
      return null;
    }
  }

  // ------------------------------------------------------------------ clicking a highlight
  _onClick(e) {
    if (this.snipMode) return;
    if (e.target.closest(".rsa-pop")) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return; // that was a text selection, not a click
    const page = this._pageAt(e.target);
    if (!page) return;
    const [x, y] = this._pointOnPage(e, page.div);
    const items = this.docMatches() ? [...this.set.items] : [];
    const hit = items.reverse().find((a) => {
      const pl = this.set.placement(a);
      return (
        pl.state === "ok" &&
        pl.page === page.number &&
        pl.rects.some(([x0, y0, x1, y1]) => x >= x0 && x <= x1 && y >= y0 - 0.002 && y <= y1 + 0.002)
      );
    });
    if (hit) {
      e.preventDefault();
      this.edit(hit, { at: e });
    } else if (this.popover) {
      this.closePopover();
    }
  }

  // ------------------------------------------------------------------ popovers
  _place(pop, clientX, clientY) {
    const host = this.host.getBoundingClientRect();
    const w = pop.offsetWidth || 260;
    let left = clientX - host.left - w / 2;
    left = Math.max(8, Math.min(left, host.width - w - 8));
    let top = clientY - host.top + 10;
    if (top + pop.offsetHeight > host.height - 8) top = Math.max(8, clientY - host.top - pop.offsetHeight - 14);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }

  _repositionPopover() {
    if (!this.popover || !this._popAnchor) return;
    const a = this._popAnchor();
    if (a) this._place(this.popover, a.x, a.y);
  }

  _anchorFor(item) {
    return () => {
      const pl = this.set.placement(item);
      const div = this.reader.pageDiv(pl.page);
      if (!div || pl.state !== "ok" || !pl.rects.length) return null;
      const r = div.getBoundingClientRect();
      const last = pl.rects[pl.rects.length - 1];
      return { x: r.left + ((last[0] + last[2]) / 2) * r.width, y: r.top + last[3] * r.height };
    };
  }

  closePopover() {
    if (this.popover) this.popover.remove();
    this.popover = null;
    this._popAnchor = null;
    if (this.activeId) {
      this.activeId = null;
      this.redrawAll();
    }
  }

  _swatches(current) {
    return Object.entries(COLORS)
      .map(
        ([name, hex]) =>
          `<button type="button" class="rsa-swatch${name === current ? " on" : ""}" data-color="${name}" title="${name}" style="--rsa-c:${hex}"></button>`,
      )
      .join("");
  }

  _openSelectionPopover(e) {
    this.closePopover();
    const pop = document.createElement("div");
    pop.className = "rsa-pop rsa-pop-sel";
    pop.innerHTML = `
      <div class="rsa-row">${this._swatches(null)}
        <button type="button" class="rsa-btn" data-act="note" title="Highlight and add a note (n)">✎ Note</button>
        <button type="button" class="rsa-btn" data-act="copy" title="Copy text">Copy</button>
      </div>
      <div class="rsa-hint">h = highlight · n = note · Esc</div>`;
    pop.addEventListener("mousedown", (ev) => ev.preventDefault()); // keep the selection
    pop.addEventListener("click", async (ev) => {
      const sw = ev.target.closest("[data-color]");
      const act = ev.target.closest("[data-act]");
      if (sw) await this.highlight(sw.dataset.color);
      else if (act && act.dataset.act === "note") await this.highlight("yellow", true);
      else if (act && act.dataset.act === "copy") {
        try {
          await navigator.clipboard.writeText(this.pending ? this.pending.quote : "");
          this.set.notify("Copied", "ok");
        } catch (_) {}
        this.closePopover();
      }
    });
    this.host.appendChild(pop);
    this.popover = pop;
    const sel = window.getSelection();
    const rects = sel.rangeCount ? sel.getRangeAt(0).getClientRects() : [];
    const last = rects.length ? rects[rects.length - 1] : null;
    const at = last ? { x: last.left + last.width / 2, y: last.bottom } : { x: e.clientX, y: e.clientY };
    this._place(pop, at.x, at.y);
  }

  async edit(item, { at = null, focusNote = false } = {}) {
    this.closePopover();
    this.activeId = item.id;
    this.redrawAll();
    const projects = await this.set.projects();
    const tagged = new Set(item.projects.map((p) => p.id));
    const pop = document.createElement("div");
    pop.className = "rsa-pop rsa-pop-edit";
    const snip = item.kind === "snip";
    pop.innerHTML = `
      ${snip ? `<img class="rsa-pop-img" src="${this.set.imageUrl(item.image)}" alt="snip">` : ""}
      ${!snip ? `<div class="rsa-row">${this._swatches(item.color)}</div>` : ""}
      ${item.quote ? `<blockquote class="rsa-quote">${escapeHtml(item.quote)}</blockquote>` : ""}
      <textarea class="rsa-note" rows="3" placeholder="${snip ? "Caption or note…" : "Add a note…"}  (Ctrl+Enter to save)">${escapeHtml(item.note || "")}</textarea>
      ${
        projects.length
          ? `<div class="rsa-projects">${projects
              .map(
                (p) =>
                  `<button type="button" class="rsa-chip${tagged.has(p.id) ? " on" : ""}" data-project="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name || "")}">${tagged.has(p.id) ? "✓ " : "+ "}${escapeHtml(p.name || p.id)}</button>`,
              )
              .join("")}</div>`
          : ""
      }
      <div class="rsa-row rsa-actions">
        ${snip ? `<button type="button" class="rsa-btn" data-act="star">${item.starred ? "★ Cover" : "☆ Make cover"}</button>` : ""}
        <span class="rsa-spacer"></span>
        <button type="button" class="rsa-btn rsa-danger" data-act="delete">Delete</button>
        <button type="button" class="rsa-btn rsa-primary" data-act="save">Done</button>
      </div>`;
    const note = pop.querySelector(".rsa-note");
    const save = async () => {
      const value = note.value.trim();
      if (value !== (item.note || "")) {
        try {
          item = await this.set.update(item.id, { note: value });
        } catch (e) {
          this.set.notify(e.message, "error");
        }
      }
    };
    note.addEventListener("keydown", async (ev) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        await save();
        this.closePopover();
      } else if (ev.key === "Escape") {
        ev.stopPropagation();
        await save();
        this.closePopover();
      }
    });
    pop.addEventListener("click", async (ev) => {
      const sw = ev.target.closest("[data-color]");
      const chip = ev.target.closest("[data-project]");
      const act = ev.target.closest("[data-act]");
      try {
        if (sw) {
          item = await this.set.update(item.id, { color: sw.dataset.color });
          pop.querySelectorAll(".rsa-swatch").forEach((b) => b.classList.toggle("on", b === sw));
        } else if (chip) {
          const id = chip.dataset.project;
          if (tagged.has(id)) tagged.delete(id);
          else tagged.add(id);
          const list = projects.filter((p) => tagged.has(p.id)).map((p) => ({ id: p.id, name: p.name }));
          item = await this.set.update(item.id, { projects: list });
          chip.classList.toggle("on", tagged.has(id));
          chip.textContent = `${tagged.has(id) ? "✓ " : "+ "}${chip.dataset.name || id}`;
        } else if (act) {
          if (act.dataset.act === "save") {
            await save();
            this.closePopover();
          } else if (act.dataset.act === "delete") {
            if ((item.note || item.kind !== "highlight") && !confirm("Delete this " + (snip ? "snip" : "note") + "?")) return;
            this.closePopover();
            await this.set.remove(item.id);
          } else if (act.dataset.act === "star") {
            item = await this.set.star(item.id, !item.starred);
            act.textContent = item.starred ? "★ Cover" : "☆ Make cover";
          }
        }
      } catch (e) {
        this.set.notify(e.message, "error");
      }
    });
    note.addEventListener("blur", (ev) => {
      if (!pop.contains(ev.relatedTarget)) save();
    });
    this.host.appendChild(pop);
    this.popover = pop;
    this._popAnchor = at ? null : this._anchorFor(item);
    const anchor = at ? { x: at.clientX, y: at.clientY } : this._popAnchor() || { x: 200, y: 120 };
    this._place(pop, anchor.x, anchor.y);
    if (focusNote) note.focus();
  }

  // ------------------------------------------------------------------ snips
  setSnipMode(on) {
    this.snipMode = !!on;
    this.reader.container.classList.toggle("rsa-snip-mode", this.snipMode);
    if (this.snipMode) this.closePopover();
    for (const fn of this._snipListeners) fn(this.snipMode);
  }

  _onMouseDown(e) {
    if (!this.snipMode || e.button !== 0 || !this.docMatches()) return;
    const page = this._pageAt(e.target);
    if (!page) return;
    e.preventDefault();
    const layer = page.div.querySelector(":scope > .rsa-layer") || (this.drawPage(page.number, page.div), page.div.querySelector(":scope > .rsa-layer"));
    const box = document.createElement("div");
    box.className = "rsa-snip-box";
    layer.appendChild(box);
    const start = this._pointOnPage(e, page.div);
    let end = start;
    const draw = () => {
      const [x0, x1] = [Math.min(start[0], end[0]), Math.max(start[0], end[0])];
      const [y0, y1] = [Math.min(start[1], end[1]), Math.max(start[1], end[1])];
      Object.assign(box.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
      return [x0, y0, x1, y1];
    };
    const move = (ev) => {
      end = this._pointOnPage(ev, page.div);
      draw();
    };
    const up = async (ev) => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      end = this._pointOnPage(ev, page.div);
      const rect = draw();
      box.remove();
      if (rect[2] - rect[0] < 0.02 || rect[3] - rect[1] < 0.01) return; // a click, not a box
      this.setSnipMode(false);
      await this.snip(page.number, rect);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  async snip(pageNumber, rect) {
    const doc = this.reader.doc;
    const fingerprint = this.reader.fingerprint;
    try {
      if (this.onWrite) await this.onWrite();
      if (doc !== this.reader.doc || !this.docMatches()) return null; // paper changed meanwhile
      const image = await this.reader.renderRegion(pageNumber, rect);
      if (doc !== this.reader.doc) return null;
      const first = !this.set.items.some((a) => a.kind === "snip");
      const item = await this.set.create({
        kind: "snip",
        page: pageNumber,
        rects: [rect],
        image_data_url: image,
        pdf_fingerprint: fingerprint,
        starred: first, // the first snip of a paper becomes its cover
      });
      this.set.notify(first ? "Snip saved, and set as this paper's cover" : "Snip saved", "ok");
      return item;
    } catch (e) {
      this.set.notify(e.message || String(e), "error");
      return null;
    }
  }

  // ------------------------------------------------------------------ keys
  _onKey(e) {
    const t = e.target;
    const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
    if (e.key === "Escape") {
      if (this.snipMode) {
        this.setSnipMode(false);
        e.stopPropagation();
      } else if (this.popover && !typing) {
        this.closePopover();
        e.stopPropagation();
      }
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    const sel = window.getSelection();
    const hasSel = this.pending && sel && !sel.isCollapsed;
    if (hasSel && (e.key === "h" || e.key === "n")) {
      e.preventDefault();
      e.stopPropagation(); // don't let the app treat it as a shortcut
      this.highlight("yellow", e.key === "n");
    }
  }

  // ------------------------------------------------------------------ re-anchoring
  // A highlight made on another version of this paper's PDF is looked up by
  // its quote (prefix/suffix pick between repeats) and its position in *this*
  // PDF is stored per fingerprint. The original position is left alone, so the
  // highlight stays right in the app whose PDF it was made on.
  _scheduleReanchor() {
    if (!this.docMatches()) return;
    const doc = this.reader.doc;
    const fp = this.reader.fingerprint;
    if (!fp) return;
    const todo = this.set.items.filter(
      (a) => !this._reanchored.has(a.id) && this.set.placement(a, fp).state === "pending",
    );
    if (!todo.length) return;
    for (const a of todo) this._reanchored.add(a.id); // claim them before any await
    (async () => {
      const n = doc.numPages;
      const texts = {};
      const pageText = async (p) => {
        if (!(p in texts)) texts[p] = stripWs(await this.reader.pageText(p, doc));
        return texts[p];
      };
      for (const a of todo) {
        if (this.reader.doc !== doc) return; // another PDF was opened; it starts over
        const target = stripWs(a.quote);
        // nearest pages first, starting with the one it was on
        const pages = Array.from({ length: n }, (_, i) => i + 1).sort(
          (x, y) => Math.abs(x - a.page) - Math.abs(y - a.page) || x - y,
        );
        let best = null;
        try {
          for (const p of pages) {
            const m = bestMatch(await pageText(p), target, a.prefix, a.suffix);
            if (m && (!best || m.score > best.score)) best = { ...m, page: p };
            if (best && best.score === best.max) break;
          }
        } catch (_) {
          return; // document went away mid-search
        }
        if (!best) {
          try {
            await this.set.setAnchor(a.id, { fingerprint: fp, state: "orphaned" });
          } catch (_) {}
          continue;
        }
        if (this.reader.doc !== doc) return;
        this._reanchorWait.set(a.id, { page: best.page, doc });
        const div = this.reader.pageDiv(best.page);
        const tl = div && div.querySelector(".textLayer");
        if (tl && tl.childNodes.length) this._reanchorOnPage(best.page, div);
      }
    })();
  }

  async _reanchorOnPage(pageNumber, pageDiv) {
    const doc = this.reader.doc;
    const fp = this.reader.fingerprint;
    const waiting = [...this._reanchorWait.entries()].filter(([, w]) => w.page === pageNumber && w.doc === doc);
    if (!waiting.length || !this.docMatches()) return;
    const textLayer = pageDiv.querySelector(".textLayer");
    for (const [id] of waiting) {
      this._reanchorWait.delete(id);
      const a = this.set.get(id);
      if (!a || this.reader.doc !== doc) continue;
      const range = findQuoteRange(textLayer, a.quote, a.prefix, a.suffix);
      const rects = range ? normalizeRects(range.getClientRects(), pageDiv.getBoundingClientRect()) : [];
      try {
        if (rects.length) await this.set.setAnchor(id, { fingerprint: fp, state: "ok", page: pageNumber, rects });
        else await this.set.setAnchor(id, { fingerprint: fp, state: "orphaned" });
      } catch (_) {}
      if (this._jumpAfter === id) {
        this._jumpAfter = null;
        if (this.reader.doc === doc) this.jumpTo(this.set.get(id));
      }
    }
  }
}
