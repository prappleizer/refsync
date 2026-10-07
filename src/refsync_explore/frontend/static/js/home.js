import { CDN, api, refsyncUrl, timeAgo, toast, toastError, toggleTheme } from "./common.js";

const { default: Alpine } = await import(CDN.alpine);

Alpine.data("homePage", (refsyncPort) => ({
  projects: [],
  loaded: false,
  newName: "",
  newDesc: "",
  status: null,
  adsKey: "",
  showArchived: false,
  archivedCount: 0,

  async init() {
    await Promise.all([this.load(), this.loadStatus()]);
    this.$refs.name && this.$refs.name.focus();
  },

  async load() {
    try {
      const all = await api("GET", "/api/projects?include_archived=true");
      this.archivedCount = all.filter((p) => p.archived).length;
      this.projects = this.showArchived ? all : all.filter((p) => !p.archived);
    } catch (e) {
      toastError(e);
    }
    this.loaded = true;
  },

  async loadStatus() {
    try {
      this.status = await api("GET", "/api/status");
    } catch (_) {
      /* non-fatal */
    }
  },

  async create() {
    const name = this.newName.trim();
    if (!name) return;
    try {
      const p = await api("POST", "/api/projects", { name, description: this.newDesc.trim() || null });
      location.href = `/p/${p.id}`;
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

  refsyncLink() {
    return refsyncUrl(refsyncPort, "/library");
  },
  timeAgo,
  toggleTheme,
}));

window.Alpine = Alpine;
Alpine.start();
