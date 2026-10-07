// PDF.js-based reader. One viewer instance, reused as you hop between papers.
// The text layer is on (selection now; highlights in v1), and internal links
// (e.g. citations -> reference list) work through the link service.

import { CDN } from "./common.js";

let libPromise = null;

function loadPdfjs() {
  if (!libPromise) {
    libPromise = (async () => {
      const pdfjsLib = await import(CDN.pdfjs);
      // pdf_viewer.mjs reads globalThis.pdfjsLib when it's evaluated
      globalThis.pdfjsLib = pdfjsLib;
      pdfjsLib.GlobalWorkerOptions.workerSrc = CDN.pdfjsWorker;
      const viewer = await import(CDN.pdfjsViewer);
      return { pdfjsLib, viewer };
    })();
  }
  return libPromise;
}

export class PdfReader {
  /**
   * @param {HTMLElement} container  absolutely positioned scroll container
   * @param {object} hooks  { onPage(pageNumber, pagesCount), onState(state, message) }
   */
  constructor(container, hooks = {}) {
    this.container = container;
    this.hooks = hooks;
    this.viewer = null;
    this.doc = null;
    this.loadingTask = null;
    this.currentKey = null;
    this.token = 0;
  }

  async _ensureViewer() {
    if (this.viewer) return;
    const { viewer: V } = await loadPdfjs();
    const eventBus = new V.EventBus();
    const linkService = new V.PDFLinkService({ eventBus });
    const inner = document.createElement("div");
    inner.className = "pdfViewer";
    this.container.appendChild(inner);
    this.viewer = new V.PDFViewer({
      container: this.container,
      viewer: inner,
      eventBus,
      linkService,
      textLayerMode: 1,
      removePageBorders: true,
    });
    linkService.setViewer(this.viewer);
    this.linkService = linkService;
    this.eventBus = eventBus;
    eventBus.on("pagechanging", (e) => {
      if (this.hooks.onPage) this.hooks.onPage(e.pageNumber, this.viewer.pagesCount);
    });
    this._onResize = () => {
      // Hidden (results showing, other tab): nothing to fit, and pdf.js can't scroll it
      if (!this.container.offsetParent || !this.container.clientWidth) return;
      if (this._pendingInit) return this._pendingInit();
      if (this.doc && ["page-width", "page-fit", "auto"].includes(this.viewer.currentScaleValue)) {
        this.viewer.currentScaleValue = this.viewer.currentScaleValue;
      }
    };
    window.addEventListener("resize", this._onResize);
    if (window.ResizeObserver) {
      new ResizeObserver(() => this._onResize()).observe(this.container);
    }
  }

  async open(url, { key, page = 1 } = {}) {
    if (key && key === this.currentKey && this.doc) {
      return;
    }
    const token = ++this.token;
    this.currentKey = key;
    this._state("loading");
    try {
      await this._ensureViewer();
      const { pdfjsLib } = await loadPdfjs();
      if (this.loadingTask) {
        this.loadingTask.destroy().catch(() => {});
      }
      // Fetch ourselves so we get the server's error message on a 404
      const res = await fetch(url);
      if (!res.ok) {
        let msg = `Couldn't load the PDF (${res.status}).`;
        try {
          msg = (await res.json()).detail || msg;
        } catch (_) {}
        throw new Error(msg);
      }
      const data = new Uint8Array(await res.arrayBuffer());
      if (token !== this.token) return;
      this.loadingTask = pdfjsLib.getDocument({ data });
      const doc = await this.loadingTask.promise;
      if (token !== this.token) {
        doc.destroy();
        return;
      }
      const old = this.doc;
      this.doc = doc;
      const startPage = Math.max(1, Math.min(page || 1, doc.numPages));
      const onInit = () => {
        if (!this.container.offsetParent) {
          // Opened while hidden: apply the fit and page once it's visible again
          this._pendingInit = onInit;
          return;
        }
        this._pendingInit = null;
        this.viewer.currentScaleValue = "page-width";
        if (startPage > 1) this.viewer.currentPageNumber = startPage;
        if (this.hooks.onPage) this.hooks.onPage(this.viewer.currentPageNumber, doc.numPages);
      };
      this.eventBus.on("pagesinit", onInit, { once: true });
      this.viewer.setDocument(doc);
      this.linkService.setDocument(doc, null);
      if (old) old.destroy();
      this._state("ready");
    } catch (e) {
      if (token !== this.token) return;
      this.currentKey = null;
      this._dropDocument(); // don't leave the previous paper showing
      this._state("error", e.message || String(e));
    }
  }

  _dropDocument() {
    if (this.viewer && this.doc) {
      this.viewer.setDocument(null);
      this.linkService.setDocument(null, null);
      this.doc.destroy();
      this.doc = null;
    }
  }

  clear() {
    this.token++;
    this.currentKey = null;
    this._dropDocument();
  }

  zoom(delta) {
    if (!this.viewer || !this.doc) return;
    const s = this.viewer.currentScale * (delta > 0 ? 1.15 : 1 / 1.15);
    this.viewer.currentScale = Math.min(4, Math.max(0.3, s));
  }

  fitWidth() {
    if (this.viewer && this.doc) this.viewer.currentScaleValue = "page-width";
  }

  goTo(page) {
    if (this.viewer && this.doc) this.viewer.currentPageNumber = page;
  }

  _state(state, message) {
    if (this.hooks.onState) this.hooks.onState(state, message);
  }
}
