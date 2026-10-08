// Snip gallery: a paper's figure snips (cover first) followed by its highlighted
// passages as text snips. Compact strip that expands; click an image for a
// larger view. Shared by refsync (paper + reader pages) and explore.

import { COLORS, escapeHtml } from "./annotations-api.js";

export class SnipGallery {
  /**
   * @param {HTMLElement} el
   * @param {AnnotationSet} set
   * @param {object} opts { onJump(item), expanded = false, maxCollapsed = 6, title }
   */
  constructor(el, set, opts = {}) {
    this.el = el;
    this.set = set;
    this.opts = opts;
    this.expanded = !!opts.expanded;
    el.classList.add("rsa-gallery");
    set.onChange(() => this.render());
    el.addEventListener("click", (e) => this._onClick(e));
    this.render();
  }

  _parts() {
    const snips = this.set.items.filter((a) => a.kind === "snip");
    snips.sort((a, b) => (b.starred ? 1 : 0) - (a.starred ? 1 : 0) || (a.page || 0) - (b.page || 0));
    const quotes = this.set.items.filter((a) => a.kind === "highlight" && a.quote);
    return { snips, quotes };
  }

  render() {
    const { snips, quotes } = this._parts();
    if (!snips.length && !quotes.length) {
      this.el.innerHTML = this.set.paperKey
        ? `<div class="rsa-gallery-empty">No snips yet. Use <b>✂ Snip</b> on a figure, or highlight a passage.</div>`
        : "";
      this.el.classList.toggle("rsa-gallery-hidden", !this.set.paperKey);
      return;
    }
    this.el.classList.remove("rsa-gallery-hidden");
    const max = this.opts.maxCollapsed || 6;
    const shownSnips = this.expanded ? snips : snips.slice(0, max);
    const shownQuotes = this.expanded ? quotes : quotes.slice(0, 3);
    const hidden = snips.length + quotes.length - shownSnips.length - shownQuotes.length;
    this.el.innerHTML = `
      <div class="rsa-gallery-head">
        <span class="rsa-pane-title">${escapeHtml(this.opts.title || "Snips")}</span>
        <span class="rsa-faint">${snips.length} figure${snips.length === 1 ? "" : "s"} · ${quotes.length} highlight${quotes.length === 1 ? "" : "s"}</span>
        <span class="rsa-spacer"></span>
        ${snips.length > max || quotes.length > 3
          ? `<button type="button" class="rsa-link" data-act="toggle">${this.expanded ? "Show less" : `Show all (${hidden} more)`}</button>`
          : ""}
      </div>
      <div class="rsa-thumbs">
        ${shownSnips
          .map(
            (a) => `
          <figure class="rsa-thumb${a.starred ? " rsa-starred" : ""}" data-id="${a.id}">
            <img src="${this.set.imageUrl(a.image)}" alt="snip p. ${a.page}" data-act="open" loading="lazy">
            <button type="button" class="rsa-star" data-act="star" title="${a.starred ? "Cover" : "Make cover"}">${a.starred ? "★" : "☆"}</button>
            <figcaption>${a.note ? escapeHtml(a.note) : `p. ${a.page}`}</figcaption>
          </figure>`,
          )
          .join("")}
      </div>
      ${shownQuotes.length ? `<div class="rsa-quotes">${shownQuotes
        .map(
          (a) => `<button type="button" class="rsa-quote-snip" data-id="${a.id}" data-act="jump" style="--rsa-c:${COLORS[a.color] || COLORS.yellow}">
              <span class="rsa-faint">p. ${this.set.placement(a).page}</span> ${escapeHtml(a.quote)}</button>`,
        )
        .join("")}</div>` : ""}`;
  }

  async _onClick(e) {
    const act = e.target.closest("[data-act]");
    if (!act) return;
    const holder = e.target.closest("[data-id]");
    const a = holder ? this.set.get(holder.dataset.id) : null;
    try {
      if (act.dataset.act === "toggle") {
        this.expanded = !this.expanded;
        this.render();
      } else if (act.dataset.act === "star" && a) {
        await this.set.star(a.id, !a.starred);
      } else if (act.dataset.act === "jump" && a && this.opts.onJump) {
        this.opts.onJump(a);
      } else if (act.dataset.act === "open" && a) {
        this._lightbox(a);
      }
    } catch (err) {
      this.set.notify(err.message, "error");
    }
  }

  _lightbox(a) {
    const bg = document.createElement("div");
    bg.className = "rsa-lightbox";
    bg.innerHTML = `
      <div class="rsa-lightbox-card">
        <img src="${this.set.imageUrl(a.image)}" alt="snip">
        <div class="rsa-row">
          <span class="rsa-faint">p. ${a.page}</span>
          <span>${a.note ? escapeHtml(a.note) : ""}</span>
          <span class="rsa-spacer"></span>
          <button type="button" class="rsa-btn" data-lb="star">${a.starred ? "★ Cover" : "☆ Make cover"}</button>
          ${this.opts.onJump ? `<button type="button" class="rsa-btn" data-lb="jump">Go to page ${a.page}</button>` : ""}
          <button type="button" class="rsa-btn rsa-danger" data-lb="delete">Delete</button>
          <button type="button" class="rsa-btn rsa-primary" data-lb="close">Close</button>
        </div>
      </div>`;
    const close = () => {
      bg.remove();
      window.removeEventListener("keydown", onKey, true);
    };
    const onKey = (ev) => {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        close();
      }
    };
    bg.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-lb]");
      if (ev.target === bg) return close();
      if (!b) return;
      try {
        if (b.dataset.lb === "close") close();
        else if (b.dataset.lb === "jump") {
          close();
          this.opts.onJump(a);
        } else if (b.dataset.lb === "star") {
          a = await this.set.star(a.id, !a.starred);
          b.textContent = a.starred ? "★ Cover" : "☆ Make cover";
        } else if (b.dataset.lb === "delete") {
          if (!confirm("Delete this snip?")) return;
          close();
          await this.set.remove(a.id);
        }
      } catch (err) {
        this.set.notify(err.message, "error");
      }
    });
    window.addEventListener("keydown", onKey, true);
    document.body.appendChild(bg);
  }
}
