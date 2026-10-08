// PDF.js reader shared by refsync and refsync-explore.
// One viewer instance, reused as you move between papers. Text layer is on
// (for selection/highlights) and internal links (citation -> reference) work.
//
// Events (reader.on(name, fn)):
//   page(pageNumber, pagesCount)    current page changed
//   state(state, message)           loading | ready | error
//   document(pdfDocument)           a new document finished loading
//   pagerendered(pageNumber, div)   a page (canvas) was drawn; div is the .page element
//   textlayer(pageNumber, div)      a page's text layer is ready (for anchoring)

export const PDFJS = {
  lib: "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.min.mjs",
  worker: "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.worker.min.mjs",
  viewer: "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/web/pdf_viewer.mjs",
  css: "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/web/pdf_viewer.css",
};

let libPromise = null;

export function loadPdfjs() {
  if (!libPromise) {
    libPromise = (async () => {
      const pdfjsLib = await import(PDFJS.lib);
      // pdf_viewer.mjs reads globalThis.pdfjsLib when it's evaluated
      globalThis.pdfjsLib = pdfjsLib;
      pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS.worker;
      const viewer = await import(PDFJS.viewer);
      return { pdfjsLib, viewer };
    })();
  }
  return libPromise;
}

export class PdfReader {
  /**
   * @param {HTMLElement} container  absolutely positioned scroll container
   * @param {object} hooks  optional { onPage, onState } (same as the page/state events)
   */
  constructor(container, hooks = {}) {
    this.container = container;
    this.viewer = null;
    this.doc = null;
    this.fingerprint = null;
    this.docKey = null; // `key` of the document currently shown (set once it has loaded)
    this.loadingTask = null;
    this.currentKey = null;
    this.token = 0;
    this._listeners = {};
    if (hooks.onPage) this.on("page", hooks.onPage);
    if (hooks.onState) this.on("state", hooks.onState);
  }

  on(name, fn) {
    (this._listeners[name] = this._listeners[name] || []).push(fn);
    return () => {
      this._listeners[name] = this._listeners[name].filter((f) => f !== fn);
    };
  }

  _emit(name, ...args) {
    for (const fn of this._listeners[name] || []) {
      try {
        fn(...args);
      } catch (e) {
        console.error(`reader ${name} listener failed`, e);
      }
    }
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
    eventBus.on("pagechanging", (e) => this._emit("page", e.pageNumber, this.viewer.pagesCount));
    eventBus.on("pagerendered", (e) => this._emit("pagerendered", e.pageNumber, e.source.div));
    eventBus.on("textlayerrendered", (e) => this._emit("textlayer", e.pageNumber, e.source.div));
    this._onResize = () => {
      // Hidden (other tab/mode): nothing to fit, and pdf.js can't scroll it
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

  async open(url, { key, page = 1, top = null } = {}) {
    if (key && key === this.currentKey && this.doc) {
      if (top != null) this.scrollTo(page, top);
      return;
    }
    const token = ++this.token;
    this.currentKey = key;
    this._emit("state", "loading");
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
      this.docKey = key || null;
      this.fingerprint = (doc.fingerprints && doc.fingerprints[0]) || null;
      const startPage = Math.max(1, Math.min(page || 1, doc.numPages));
      const onInit = () => {
        if (!this.container.offsetParent) {
          // Opened while hidden: apply the fit and page once it's visible again
          this._pendingInit = onInit;
          return;
        }
        this._pendingInit = null;
        this.viewer.currentScaleValue = "page-width";
        if (top != null) this.scrollTo(startPage, top);
        else if (startPage > 1) this.viewer.currentPageNumber = startPage;
        this._emit("page", this.viewer.currentPageNumber, doc.numPages);
      };
      this.eventBus.on("pagesinit", onInit, { once: true });
      this.viewer.setDocument(doc);
      this.linkService.setDocument(doc, null);
      if (old) old.destroy();
      this._emit("document", doc);
      this._emit("state", "ready");
    } catch (e) {
      if (token !== this.token) return;
      this.currentKey = null;
      this._dropDocument(); // don't leave the previous paper showing
      this._emit("state", "error", e.message || String(e));
    }
  }

  _dropDocument() {
    if (this.viewer && this.doc) {
      this.viewer.setDocument(null);
      this.linkService.setDocument(null, null);
      this.doc.destroy();
      this.doc = null;
      this.docKey = null;
      this.fingerprint = null;
    }
  }

  clear() {
    this.token++;
    this.currentKey = null;
    this._dropDocument();
  }

  get pagesCount() {
    return this.doc ? this.doc.numPages : 0;
  }

  get currentPage() {
    return this.viewer && this.doc ? this.viewer.currentPageNumber : 1;
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

  /** Page's .page element (exists for every page once the document is set). */
  pageDiv(pageNumber) {
    if (!this.viewer || !this.doc) return null;
    const pv = this.viewer.getPageView(pageNumber - 1);
    return pv ? pv.div : null;
  }

  /** Scroll so that `top` (fraction of the page height) sits near the top of the view. */
  scrollTo(pageNumber, top = 0) {
    const div = this.pageDiv(pageNumber);
    if (!div || !this.container.offsetParent) {
      this.goTo(pageNumber);
      return;
    }
    this.container.scrollTop = Math.max(0, div.offsetTop + top * div.clientHeight - 60);
  }

  /**
   * Render a region of a page at high resolution and return a PNG data URL.
   * rect = [x0, y0, x1, y1] as fractions of the page.
   */
  async renderRegion(pageNumber, rect, maxWidthPx = 2400) {
    if (!this.doc) throw new Error("No document");
    const page = await this.doc.getPage(pageNumber);
    const base = page.getViewport({ scale: 1 });
    const [x0, y0, x1, y1] = rect;
    const regionW = Math.max(1, (x1 - x0) * base.width);
    // ~3x for crisp figures, capped so huge regions stay a sane size
    const scale = Math.min(4, Math.max(1.5, maxWidthPx / regionW));
    const vp = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(vp.width);
    canvas.height = Math.ceil(vp.height);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
    const sx = Math.floor(x0 * canvas.width);
    const sy = Math.floor(y0 * canvas.height);
    const sw = Math.max(1, Math.ceil((x1 - x0) * canvas.width));
    const sh = Math.max(1, Math.ceil((y1 - y0) * canvas.height));
    const out = document.createElement("canvas");
    out.width = sw;
    out.height = sh;
    const ctx = out.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, sw, sh);
    ctx.drawImage(canvas, sx, sy, sw, sh, 0, 0, sw, sh);
    return out.toDataURL("image/png");
  }

  /** Plain text of one page (for finding a highlight's quote in another PDF version). */
  async pageText(pageNumber, doc = this.doc) {
    if (!doc) return "";
    const page = await doc.getPage(pageNumber);
    const tc = await page.getTextContent();
    return tc.items.map((i) => i.str).join("");
  }
}
