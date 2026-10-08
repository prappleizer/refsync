// refsync-explore workspace: triage board + ADS search + reader.

import {
  CDN,
  api,
  debounce,
  isTyping,
  refsyncUrl,
  timeAgo,
  toast,
  toastError,
  toggleTheme,
  typesetSoon,
} from "./common.js";
import { PdfReader } from "/shared/pdfview.js";
import { AnnotationSet } from "/shared/annotations-api.js";
import { Annotator } from "/shared/annotator.js";
import { NotesPane } from "/shared/notespane.js";
import { SnipGallery } from "/shared/gallery.js";

const [{ default: Alpine }, { default: Sortable }] = await Promise.all([
  import(CDN.alpine),
  import(CDN.sortable),
]);

const COLS = ["staging", "probables", "rejected"];
const SORT_NAMES = { relevance: "relevance", date: "newest", citations: "most cited", oldest: "oldest" };

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch (_) {
    /* storage unavailable: fine */
  }
  return null;
}

Alpine.data("workspace", (project, refsyncPort) => {
  // Non-reactive objects live here, outside Alpine's proxies
  // (pdf.js and Sortable don't work through Proxy wrappers).
  let reader = null;
  const lists = {};
  let resultsSortable = null;
  const savers = {};
  let tagQueue = Promise.resolve();
  let openTicket = 0;
  let annSet = null; // highlights / notes / snips of the open paper (shared with refsync)
  let annotator = null;
  let notesPane = null;
  const pid = project.id;

  return {
    project,
    status: null,
    adsKey: "",
    rate: null,

    // board
    cards: {},
    order: { staging: [], probables: [], rejected: [] },
    tags: [],
    sortMode: { staging: "manual", probables: "manual", rejected: "manual" },
    filter: "",
    selected: null,
    editingOneLiner: null,
    showRejected: false,

    // notes & snips
    readingMode: false,
    galleryOpen: true,
    snipMode: false,
    annCounts: { total: 0, snip: 0 },
    annSummary: {}, // paper id -> {highlight, note, snip, cover}

    // pane
    mode: "empty", // empty | search | paper
    paper: null,
    tab: "pdf",
    pdfState: "idle",
    pdfError: "",
    pageInfo: "",
    tagDraft: "",

    // search
    query: "",
    sort: "relevance",
    searching: false,
    results: null,
    checked: {},
    expanded: {},
    cursor: 0,
    history: [],
    historyOpen: false,
    recOpts: { mode: "all", min_year: null, include_triaged: false },
    addRef: "",
    addRefOpen: false,

    // dialogs
    helpOpen: false,
    promoteOpen: false,
    promotePick: {},
    promoteShelf: "",
    promoteTags: true,
    promoteNotes: true,
    promoting: false,

    // ------------------------------------------------------------ setup
    async init() {
      reader = new PdfReader(this.$refs.viewer, {
        onPage: (n, total) => this.onPage(n, total),
        onState: (state, msg) => this.onPdfState(state, msg),
      });
      annSet = new AnnotationSet({
        currentProject: { id: pid, name: project.name },
        notify: (msg, type) => (type === "error" ? toastError({ message: msg }) : toast(msg, type, 2200)),
      });
      annotator = new Annotator(reader, annSet, { host: this.$refs.viewer.parentElement });
      annotator.onSnipMode((on) => (this.snipMode = on));
      const jump = (a) => this.jumpToAnnotation(a);
      notesPane = new NotesPane(this.$refs.notesPane, annSet, { onJump: jump, currentPage: () => reader.currentPage });
      new SnipGallery(this.$refs.gallery, annSet, { onJump: jump, title: "Snips" });
      annSet.onChange((items) => this.onAnnotationsChanged(items));
      this.galleryOpen = store(`explore:gallery`) !== "0";
      this.$watch("galleryOpen", (v) => store(`explore:gallery`, v ? "1" : "0"));
      this.$watch("filter", () => {
        this.applySortable();
        typesetSoon(); // cards re-created by the filter come back as raw $...$
      });
      // newly visible cards may carry $...$ titles
      this.$watch("showRejected", () => typesetSoon());
      this.$watch("tab", (t) => {
        // wait until the reader is visible again (pdf.js can't scroll a hidden viewer)
        if (t === "pdf") this.$nextTick(() => this.loadPdf());
      });
      await Promise.all([this.loadBoard(), this.loadStatus()]);
      this.loadSummaries();
      notesPane.refreshProjects();
      const last = store(`explore:sel:${pid}`);
      if (last && this.cards[last]) this.selectCard(last, { scroll: true });
    },

    async loadStatus() {
      try {
        this.status = await api("GET", "/api/status");
        if (this.status.rate) this.rate = this.status.rate;
      } catch (_) {}
    },

    async loadBoard() {
      try {
        const data = await api("GET", `/api/projects/${pid}/board`);
        this.project = data.project;
        this.tags = data.tags;
        const cards = {};
        const order = { staging: [], probables: [], rejected: [] };
        for (const c of data.cards) {
          cards[c.id] = c;
          order[c.col].push(c.id);
        }
        this.cards = cards;
        this.order = order;
        typesetSoon();
        if (annSet) this.loadSummaries();
      } catch (e) {
        toastError(e);
      }
    },

    // ------------------------------------------------------------ board view
    matches(c) {
      const f = this.filter.trim().toLowerCase();
      if (!f) return true;
      return f.split(/\s+/).every((tok) => {
        if (tok.startsWith("#")) return c.tags.some((t) => t.toLowerCase().startsWith(tok.slice(1)));
        const hay = [c.title, c.author_label, c.one_liner, c.note, c.bibstem, c.year, ...c.tags]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return hay.includes(tok);
      });
    },

    columnCards(col) {
      let list = this.order[col].map((id) => this.cards[id]).filter((c) => c && this.matches(c));
      const mode = this.sortMode[col];
      if (mode === "year") list = [...list].sort((a, b) => (b.year || "").localeCompare(a.year || ""));
      else if (mode === "cites") list = [...list].sort((a, b) => (b.citation_count || 0) - (a.citation_count || 0));
      else if (mode === "author") list = [...list].sort((a, b) => a.author_label.localeCompare(b.author_label));
      return list;
    },

    countLabel(col) {
      const total = this.order[col].length;
      if (!this.filter.trim()) return total;
      return `${this.columnCards(col).length}/${total}`;
    },

    visibleSequence() {
      const cols = this.showRejected ? COLS : ["staging", "probables"];
      return cols.flatMap((col) => this.columnCards(col).map((c) => c.id));
    },

    tagColor(name) {
      const t = this.tags.find((x) => x.name === name);
      return t && t.color ? t.color : "#8b5cf6";
    },

    // ------------------------------------------------------------ drag & drop
    registerList(el, col) {
      if (lists[col]) lists[col].destroy();
      lists[col] = Sortable.create(el, {
        group: { name: "board", pull: true, put: true },
        animation: 120,
        draggable: ".card",
        sort: this.canReorder(col),
        // Remember the card's original neighbour: on drop we put the DOM back
        // exactly where it was and let Alpine re-render from state.
        onStart: (evt) => {
          evt.item._exploreNext = evt.item.nextSibling;
        },
        onEnd: (evt) => this.onBoardDrop(evt),
        onAdd: (evt) => {
          if (evt.from && evt.from.dataset.results !== undefined) this.onResultsDrop(evt);
        },
      });
    },

    registerResults(el) {
      el.dataset.results = "";
      if (resultsSortable) resultsSortable.destroy();
      resultsSortable = Sortable.create(el, {
        group: { name: "board", pull: "clone", put: false },
        sort: false,
        draggable: ".hit",
        filter: "input, button, a",
        preventOnFilter: false,
        animation: 120,
        // The copy Sortable leaves in the list is plain DOM: keep Alpine off it
        onClone: (evt) => evt.clone.setAttribute("x-ignore", ""),
      });
    },

    canReorder(col) {
      return col !== "rejected" && this.sortMode[col] === "manual" && !this.filter.trim();
    },

    applySortable() {
      for (const col of COLS) {
        if (lists[col]) lists[col].option("sort", this.canReorder(col));
      }
    },

    onBoardDrop(evt) {
      const from = evt.from.dataset.col;
      const to = evt.to.dataset.col;
      if (!from || !to) return;
      const id = evt.item.dataset.id;
      const index = evt.newDraggableIndex;
      const next = evt.item._exploreNext;
      delete evt.item._exploreNext;
      if (from === to && evt.oldDraggableIndex === evt.newDraggableIndex) return;
      // Put the DOM back as it was (Alpine's x-for assumes it); state drives the move.
      // Indexes can't be used for this: Sortable's skip the x-for <template> node.
      evt.from.insertBefore(evt.item, next && next.parentNode === evt.from ? next : null);
      this.moveTo(id, to, index);
    },

    onResultsDrop(evt) {
      const id = evt.item.dataset.id;
      const col = evt.to.dataset.col;
      // Sortable moved the real row into the column and left a clone behind:
      // swap them back so Alpine's results list stays intact
      if (evt.clone && evt.clone.parentNode) evt.clone.replaceWith(evt.item);
      else evt.item.remove();
      const ids = this.checked[id] ? this.checkedIds() : [id];
      this.addHits(ids, col);
    },

    async moveTo(id, col, index = 0) {
      const card = this.cards[id];
      if (!card) return;
      const target = this.order[col].filter((x) => x !== id);
      const pos = this.canReorder(col) ? Math.max(0, Math.min(index, target.length)) : 0;
      target.splice(pos, 0, id);
      for (const c of COLS) {
        if (c !== col) this.order[c] = this.order[c].filter((x) => x !== id);
      }
      this.order[col] = target;
      const changedCol = card.col !== col;
      card.col = col;
      typesetSoon();
      try {
        await api("POST", `/api/projects/${pid}/reorder`, { col, ids: target });
        if (changedCol) this.syncResultStatus(id);
      } catch (e) {
        toastError(e);
        this.loadBoard();
      }
    },

    async moveCard(id, col) {
      if (!this.cards[id] || this.cards[id].col === col) return;
      await this.moveTo(id, col, 0);
      if (col === "rejected" && !this.showRejected) {
        toast("Moved to Rejected (click the strip on the right of Probables to see them)");
      }
    },

    // ------------------------------------------------------------ selection / reader
    selectCard(id, { scroll = false, delay = 0 } = {}) {
      this.selected = id;
      store(`explore:sel:${pid}`, id);
      if (scroll) {
        this.$nextTick(() => {
          const el = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
          if (el) el.scrollIntoView({ block: "nearest" });
        });
      }
      if (delay) {
        this._openSoon = this._openSoon || debounce((x) => this.openPaper(x), 250);
        this._openSoon(id);
      } else {
        this.openPaper(id);
      }
    },

    async openPaper(id) {
      this.mode = "paper";
      if (!this.paper || this.paper.id !== id) {
        this.pdfState = "idle";
        this.pageInfo = "";
      }
      const ticket = ++openTicket;
      try {
        const data = await api("GET", `/api/projects/${pid}/papers/${id}`);
        if (ticket !== openTicket) return; // a later click won the race
        this.paper = data.paper;
        if (data.card) this.mergeCard(data.card);
        typesetSoon();
        // Annotations are keyed by refsync's id for the paper (same as ours unless
        // refsync already had it under another id); read both to be safe.
        const key = data.paper.refsync_id || data.paper.id;
        if (annSet.paperKey !== key) {
          annotator.setSnipMode(false);
          annotator.closePopover();
          annSet.setPaper(key, [data.paper.id]);
        }
        // after Alpine has shown the reader (pdf.js can't scroll a hidden viewer)
        if (this.tab === "pdf") this.$nextTick(() => this.loadPdf());
        const card = this.cards[id];
        if (card && card.read_state === "unseen") this.saveField(card, "read_state", "skimmed");
      } catch (e) {
        toastError(e);
      }
    },

    get card() {
      return this.paper ? this.cards[this.paper.id] || null : null;
    },

    // ------------------------------------------------------------ notes & snips
    async loadSummaries() {
      const ids = [];
      for (const c of Object.values(this.cards)) {
        ids.push(c.id);
        if (c.refsync_id && c.refsync_id !== c.id) ids.push(c.refsync_id);
      }
      if (!ids.length) return;
      try {
        this.annSummary = await api("GET", `/api/annotations/summary?paper_id=${encodeURIComponent(ids.join(","))}`);
      } catch (_) {
        /* badges are optional */
      }
    },

    _summaryFor(c) {
      return this.annSummary[c.refsync_id] || this.annSummary[c.id] || null;
    },

    coverOf(c) {
      const s = this._summaryFor(c);
      return s && s.cover ? `/api/annotations/files/${encodeURIComponent(s.cover)}` : "";
    },

    noteCount(c) {
      const s = this._summaryFor(c);
      return s ? s.highlight + s.note : 0;
    },

    onAnnotationsChanged(items) {
      const counts = { highlight: 0, note: 0, snip: 0 };
      let cover = null;
      for (const a of items) {
        counts[a.kind] += 1;
        if (a.starred) cover = a.image;
      }
      this.annCounts = { total: counts.highlight + counts.note + counts.snip, snip: counts.snip };
      if (annSet.paperKey) {
        const has = counts.highlight || counts.note || counts.snip;
        const next = { ...this.annSummary };
        for (const k of annSet.aliases) delete next[k];
        if (has) next[annSet.paperKey] = { ...counts, cover };
        this.annSummary = next;
      }
    },

    jumpToAnnotation(a) {
      if (!a.page) return;
      const go = () => annotator.jumpTo(a);
      if (this.tab !== "pdf") {
        this.tab = "pdf";
        this.$nextTick(() => setTimeout(go, 50));
      } else go();
    },

    toggleReading() {
      this.readingMode = !this.readingMode;
      if (this.readingMode && this.mode === "search" && this.paper) this.mode = "paper";
    },

    toggleSnip() {
      if (!this.paper || this.pdfState !== "ready") return;
      if (this.tab !== "pdf") this.tab = "pdf";
      annotator.setSnipMode(!this.snipMode);
    },

    loadPdf(force = false) {
      if (!this.paper || !reader) return;
      const id = this.paper.id;
      if (force) reader.currentKey = null;
      const card = this.cards[id];
      reader.open(`/api/papers/${encodeURIComponent(id)}/pdf`, {
        key: id,
        page: card && card.last_page ? card.last_page : 1,
      });
    },

    retryPdf() {
      this.loadPdf(true);
    },

    zoom(d) {
      if (reader) reader.zoom(d);
    },
    fitWidth() {
      if (reader) reader.fitWidth();
    },

    onPdfState(state, msg) {
      this.pdfState = state;
      this.pdfError = msg || "";
      if (!this.paper) return;
      const c = this.cards[this.paper.id];
      if (state === "ready") {
        this.paper.pdf_status = "ok";
        if (c) c.pdf_status = "ok";
      } else if (state === "error") {
        this.paper.pdf_status = "failed";
        if (c) c.pdf_status = "failed";
      }
    },

    onPage(n, total) {
      this.pageInfo = `p. ${n} / ${total}`;
      const c = this.paper && this.cards[this.paper.id];
      if (!c || c.last_page === n) return;
      c.last_page = n;
      this.saveFieldSoon(c, "last_page", n);
    },

    async uploadPdf(evt) {
      const file = evt.target.files && evt.target.files[0];
      if (!file || !this.paper) return;
      const form = new FormData();
      form.append("file", file);
      try {
        await api("POST", `/api/papers/${encodeURIComponent(this.paper.id)}/pdf`, form);
        toast("PDF attached", "ok");
        this.loadPdf(true);
      } catch (e) {
        toastError(e);
      }
      evt.target.value = "";
    },

    // ------------------------------------------------------------ card edits
    mergeCard(c) {
      const existing = this.cards[c.id];
      if (existing) {
        // Column/position are owned by the board (moves are applied locally
        // first); a response that raced a move must not drag the card back.
        const { col, position, ...rest } = c;
        Object.assign(existing, rest);
      } else {
        this.cards[c.id] = c;
        this.place(c.id, c.col);
      }
    },

    place(id, col, index = 0) {
      for (const c of COLS) this.order[c] = this.order[c].filter((x) => x !== id);
      const arr = [...this.order[col]];
      arr.splice(index, 0, id);
      this.order[col] = arr;
    },

    async saveField(card, field, value, force = false) {
      if (!force && card[field] === value) return;
      card[field] = value;
      try {
        const updated = await api("PATCH", `/api/projects/${pid}/papers/${card.id}`, { [field]: value });
        // Free-text fields aren't echoed back: the user may still be typing
        if (!["note", "last_page", "one_liner", "why_not"].includes(field)) this.mergeCard(updated);
        else card.read_state = updated.read_state;
        this.syncResultStatus(card.id);
      } catch (e) {
        toastError(e);
      }
    },

    saveFieldSoon(card, field, value) {
      const key = `${card.id}:${field}`;
      if (!savers[key]) {
        savers[key] = debounce((c, f, v) => this.saveField(c, f, v, true), 700);
      }
      card[field] = value;
      savers[key](card, field, value);
    },

    saveOneLiner(card, value) {
      if (this.editingOneLiner !== card.id) return;
      this.editingOneLiner = null;
      this.saveField(card, "one_liner", value.trim() || "");
    },

    tagSuggestions() {
      const d = this.tagDraft.trim().toLowerCase();
      const card = this.card;
      if (!d || !card) return [];
      return this.tags
        .filter((t) => t.name.toLowerCase().startsWith(d) && !card.tags.includes(t.name))
        .slice(0, 6);
    },

    setTags(card, tags) {
      // Apply locally first, then save in order, so quick successive edits
      // (type a tag, Enter, type another) never overwrite each other.
      card.tags = tags;
      tagQueue = tagQueue.then(async () => {
        try {
          await api("PATCH", `/api/projects/${pid}/papers/${card.id}`, { tags });
          this.tags = await api("GET", `/api/projects/${pid}/tags`);
        } catch (e) {
          toastError(e);
          this.loadBoard();
        }
      });
      return tagQueue;
    },

    addTag(card, name) {
      name = (name || "").trim().replace(/^#/, "");
      this.tagDraft = "";
      if (!name || card.tags.includes(name)) return;
      this.setTags(card, [...card.tags, name]);
    },

    removeTag(card, name) {
      this.setTags(card, card.tags.filter((t) => t !== name));
    },

    async removeFromProject(card) {
      if (!confirm("Remove this paper from the project? (Its notes and tags here are deleted.)")) return;
      try {
        await api("DELETE", `/api/projects/${pid}/papers/${card.id}`);
        for (const c of COLS) this.order[c] = this.order[c].filter((x) => x !== card.id);
        delete this.cards[card.id];
        this.syncResultStatus(card.id);
      } catch (e) {
        toastError(e);
      }
    },

    // ------------------------------------------------------------ search
    async runSearch({ label = null, start = 0 } = {}) {
      const q = this.query.trim();
      if (!q || this.searching) return;
      this.searching = true;
      this.historyOpen = false;
      try {
        const res = await api("POST", `/api/projects/${pid}/search`, { q, sort: this.sort, start, label });
        this.showResults(res, start > 0);
      } catch (e) {
        this.handleSearchError(e);
      } finally {
        this.searching = false;
      }
    },

    handleSearchError(e) {
      toastError(e);
      if (/API key/i.test(e.message) && this.status) this.status.ads_key = false;
    },

    showResults(res, append = false) {
      if (append && this.results) {
        const seen = new Set(this.results.hits.map((h) => h.id));
        this.results.hits.push(...res.hits.filter((h) => !seen.has(h.id)));
        this.results.num_found = res.num_found;
      } else {
        this.results = res;
        this.checked = {};
        this.expanded = {};
        this.cursor = 0;
        this.$nextTick(() => {
          if (this.$refs.resultsList) this.$refs.resultsList.scrollTop = 0;
        });
      }
      if (res.rate) this.rate = res.rate;
      this.mode = "search";
      // Hand the keyboard to the results so j/k/x/1-3 work right away
      if (document.activeElement === this.$refs.q) this.$refs.q.blur();
      this.history = []; // refetched next time the dropdown opens
      typesetSoon();
    },

    loadMore() {
      if (!this.results || this.results.kind === "recs") return;
      if (this.results.query !== this.query.trim()) this.query = this.results.query;
      this.sort = this.results.sort;
      this.runSearch({ label: this.results.label, start: this.results.hits.length });
    },

    resort(sort) {
      if (!this.results) return;
      this.sort = sort;
      this.query = this.results.query;
      this.runSearch({ label: this.results.label });
    },

    async hop(kind) {
      if (!this.paper || this.searching) return;
      this.searching = true;
      try {
        const res = await api("POST", `/api/projects/${pid}/hop`, { paper_id: this.paper.id, kind });
        this.query = res.query;
        this.sort = res.sort;
        this.showResults(res);
      } catch (e) {
        this.handleSearchError(e);
      } finally {
        this.searching = false;
      }
    },

    async runRecommend({ refresh = false } = {}) {
      if (this.searching) return;
      this.searching = true;
      this.historyOpen = false;
      try {
        const res = await api("POST", `/api/projects/${pid}/recommend`, {
          mode: this.recOpts.mode,
          min_year: this.recOpts.min_year || null,
          include_triaged: this.recOpts.include_triaged,
          refresh,
        });
        this.showResults(res);
        if (refresh) toast("Reference and citation lists refreshed from ADS", "ok", 2000);
      } catch (e) {
        this.handleSearchError(e);
      } finally {
        this.searching = false;
      }
    },

    async toggleHistory() {
      this.historyOpen = !this.historyOpen;
      if (this.historyOpen) {
        try {
          this.history = await api("GET", `/api/projects/${pid}/searches`);
        } catch (e) {
          toastError(e);
        }
      }
    },

    rerun(h) {
      if (h.query === "recommend()") {
        this.recOpts.mode = h.sort || "all";
        return this.runRecommend();
      }
      this.query = h.query;
      this.sort = h.sort || "relevance";
      this.runSearch({ label: h.label });
    },

    sortName(s) {
      return SORT_NAMES[s] || s;
    },

    closeSearch() {
      this.mode = this.paper ? "paper" : "empty";
    },

    backToResults() {
      if (this.results) this.mode = "search";
    },

    openHit(h) {
      this.cursor = this.results ? this.results.hits.indexOf(h) : 0;
      if (this.cards[h.id]) this.selected = h.id;
      this.openPaper(h.id);
    },

    checkedIds() {
      return Object.keys(this.checked).filter((k) => this.checked[k]);
    },

    toggleCheck(id) {
      this.checked[id] = !this.checked[id];
    },

    checkAllNew() {
      const next = {};
      for (const h of this.results.hits) if (!h.status) next[h.id] = true;
      this.checked = next;
    },

    newCount() {
      return this.results ? this.results.hits.filter((h) => !h.status).length : 0;
    },

    addChecked(col) {
      const ids = this.checkedIds();
      if (ids.length) this.addHits(ids, col);
    },

    async addHits(ids, col) {
      if (!ids.length) return;
      // keep search order for batches from the results list
      if (this.results) {
        const rank = new Map(this.results.hits.map((h, i) => [h.id, i]));
        ids = [...ids].sort((a, b) => (rank.get(a) ?? 1e9) - (rank.get(b) ?? 1e9));
      }
      try {
        const res = await api("POST", `/api/projects/${pid}/papers`, {
          paper_ids: ids,
          col,
          search_id: this.results ? this.results.search_id : null,
        });
        // newest batch goes on top of the column, in order
        for (const c of [...res.cards].reverse()) {
          if (this.cards[c.id]) Object.assign(this.cards[c.id], c);
          else this.cards[c.id] = c;
          this.place(c.id, c.col);
        }
        for (const id of ids) {
          this.checked[id] = false;
          this.syncResultStatus(id);
        }
        const verb = { staging: "Staged", probables: "Added to Probables", rejected: "Rejected" }[col];
        toast(`${verb}: ${ids.length} paper${ids.length > 1 ? "s" : ""}`, "ok", 1800);
        typesetSoon();
      } catch (e) {
        toastError(e);
      }
    },

    syncResultStatus(id) {
      if (!this.results) return;
      const hit = this.results.hits.find((h) => h.id === id);
      if (!hit) return;
      const c = this.cards[id];
      hit.status = c ? { col: c.col, read_state: c.read_state, one_liner: c.one_liner } : null;
    },

    toggleAddRef() {
      this.addRefOpen = !this.addRefOpen;
      if (this.addRefOpen) this.$nextTick(() => this.$refs.addref.focus());
    },

    async addByRef() {
      const ref = this.addRef.trim();
      if (!ref) return;
      try {
        const card = await api("POST", `/api/projects/${pid}/papers/add-ref`, { ref, col: "staging" });
        this.mergeCard(card);
        this.place(card.id, card.col);
        this.addRef = "";
        this.addRefOpen = false;
        toast("Staged", "ok", 1500);
        this.selectCard(card.id, { scroll: true });
      } catch (e) {
        toastError(e);
      }
    },

    // ------------------------------------------------------------ refsync
    openPromote() {
      const pick = {};
      for (const c of this.columnCards("probables")) pick[c.id] = !c.refsync_id;
      this.promotePick = pick;
      this.promoteShelf = this.project.name;
      this.promoteOpen = true;
    },

    async doPromote() {
      const ids = Object.keys(this.promotePick).filter((k) => this.promotePick[k]);
      if (!ids.length) return;
      this.promoting = true;
      try {
        const res = await api("POST", `/api/projects/${pid}/promote`, {
          paper_ids: ids,
          shelf: this.promoteShelf.trim() || null,
          carry_tags: this.promoteTags,
          carry_notes: this.promoteNotes,
        });
        const n = { added: 0, exists: 0, error: 0 };
        for (const r of res.results) {
          n[r.status] = (n[r.status] || 0) + 1;
          if (r.refsync_id && this.cards[r.id]) this.cards[r.id].refsync_id = r.refsync_id;
          if (this.paper && this.paper.id === r.id && r.refsync_id) this.paper.refsync_id = r.refsync_id;
        }
        const errors = res.results.filter((r) => r.status === "error");
        const msg = `refsync: ${n.added} added` + (n.exists ? `, ${n.exists} already there` : "");
        toast(errors.length ? `${msg}, ${errors.length} failed: ${errors[0].message}` : msg, errors.length ? "error" : "ok", 5000);
        this.promoteOpen = false;
      } catch (e) {
        toastError(e);
      } finally {
        this.promoting = false;
      }
    },

    refsyncLink(path) {
      return refsyncUrl(refsyncPort, path);
    },

    adsUrl(p) {
      return `https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(p.bibcode)}/abstract`;
    },

    // ------------------------------------------------------------ misc
    async renameProject() {
      const name = prompt("Rename project", this.project.name);
      if (!name || !name.trim() || name.trim() === this.project.name) return;
      try {
        this.project = await api("PATCH", `/api/projects/${pid}`, { name: name.trim() });
        document.title = `${this.project.name} · refsync explore`;
      } catch (e) {
        toastError(e);
      }
    },

    async saveKey() {
      try {
        await api("POST", "/api/settings/ads-key", { api_key: this.adsKey.trim() });
        this.adsKey = "";
        toast("ADS key saved (shared with refsync)", "ok");
        await this.loadStatus();
      } catch (e) {
        toastError(e);
      }
    },

    timeAgo,
    toggleTheme,

    // ------------------------------------------------------------ keyboard
    onKey(e) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (this.helpOpen || this.promoteOpen) {
        if (e.key === "Escape") {
          this.helpOpen = false;
          this.promoteOpen = false;
        }
        return;
      }
      if (isTyping(e)) {
        if (e.key === "Escape") e.target.blur();
        return;
      }
      const k = e.key;
      if (k === "/") {
        e.preventDefault();
        this.$refs.q.focus();
        this.$refs.q.select();
        return;
      }
      if (k === "?") {
        this.helpOpen = true;
        return;
      }
      if (k === "g") {
        this.runRecommend();
        return;
      }
      if (k === "Escape") {
        this.editingOneLiner = null;
        this.historyOpen = false;
        this.addRefOpen = false;
        if (this.mode === "search") this.closeSearch();
        return;
      }
      if (this.mode === "search" && this.results) return this.onResultsKey(e);
      return this.onBoardKey(e);
    },

    onResultsKey(e) {
      const hits = this.results.hits;
      const k = e.key;
      const cur = hits[this.cursor];
      const scroll = () =>
        this.$nextTick(() => {
          const el = document.querySelectorAll(".hit")[this.cursor];
          if (el) el.scrollIntoView({ block: "nearest" });
        });
      if (k === "j" || k === "ArrowDown") {
        e.preventDefault();
        this.cursor = Math.min(hits.length - 1, this.cursor + 1);
        scroll();
      } else if (k === "k" || k === "ArrowUp") {
        e.preventDefault();
        this.cursor = Math.max(0, this.cursor - 1);
        scroll();
      } else if (k === "x" && cur) {
        this.toggleCheck(cur.id);
      } else if (k === "Enter" && cur) {
        this.openHit(cur);
      } else if (["1", "2", "3"].includes(k) && cur) {
        const col = COLS[Number(k) - 1];
        const ids = this.checkedIds().length ? this.checkedIds() : [cur.id];
        this.addHits(ids, col);
        if (ids.length === 1) {
          this.cursor = Math.min(hits.length - 1, this.cursor + 1);
          scroll();
        }
      } else if (k === "a" && cur) {
        this.expanded[cur.id] = !this.expanded[cur.id];
      }
    },

    onBoardKey(e) {
      const k = e.key;
      if (k === "s" && this.results) {
        this.mode = "search";
        return;
      }
      if (k === "d") {
        this.tab = this.tab === "pdf" ? "details" : "pdf";
        return;
      }
      if (k === "n") {
        this.toggleReading();
        return;
      }
      if (k === "c") {
        this.toggleSnip();
        return;
      }
      if (k === "j" || k === "k" || k === "ArrowDown" || k === "ArrowUp") {
        e.preventDefault();
        const seq = this.visibleSequence();
        if (!seq.length) return;
        let i = seq.indexOf(this.selected);
        i = k === "j" || k === "ArrowDown" ? Math.min(seq.length - 1, i + 1) : Math.max(0, i - 1);
        if (i < 0) i = 0;
        this.selectCard(seq[i], { scroll: true, delay: 250 });
        return;
      }
      const card = this.selected && this.cards[this.selected];
      if (!card) return;
      if (["1", "2", "3"].includes(k)) {
        this.moveCard(card.id, COLS[Number(k) - 1]);
      } else if (k === "e") {
        e.preventDefault();
        if (card.col === "rejected" && !this.showRejected) this.showRejected = true;
        this.editingOneLiner = card.id;
      } else if (k === "r") {
        this.saveField(card, "read_state", card.read_state === "read" ? "skimmed" : "read");
      } else if (k === "t") {
        e.preventDefault();
        if (!this.paper || this.paper.id !== card.id) this.openPaper(card.id);
        this.tab = "details";
        this.$nextTick(() => {
          const el = document.querySelector(".tag-editor input");
          if (el) el.focus();
        });
      }
    },
  };
});

window.Alpine = Alpine;
Alpine.start();
