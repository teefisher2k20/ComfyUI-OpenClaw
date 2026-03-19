/**
 * F7: Tab Manager
 * Handles tab creation, switching, and lazy rendering.
 */
import { ErrorBoundary } from "./ErrorBoundary.js";
import { STORAGE_KEYS, getMirroredStorageValue, setMirroredStorageValue } from "./openclaw_compat.js";
import {
    applyLegacyClassAliases,
    normalizeLegacyClassNames,
} from "./openclaw_utils.js";

export class TabManager {
    constructor() {
        this.tabs = [
            // { id, title, renderFn, loaded }
        ];
        this.tabsEl = null;
        this.contentEl = null;
        this.activeTabId = null;
    }

    init(tabsEl, contentEl) {
        this.tabsEl = tabsEl;
        this.contentEl = contentEl;

        // IMPORTANT: ComfyUI may remount extension DOM.
        // When containers are new, previously-rendered tabs must be re-rendered.
        this.tabs = this.tabs.map(t => ({ ...t, loaded: false }));

        this._loadTabs();
        this._restoreActiveTab();
    }

    registerTab(tabDef) {
        // Idempotent: replace existing tab definition by id
        const idx = this.tabs.findIndex(t => t.id === tabDef.id);
        if (idx >= 0) {
            this.tabs[idx] = { ...tabDef, loaded: false };
        } else {
            this.tabs.push({ ...tabDef, loaded: false });
        }
        // If initialized, re-render
        if (this.tabsEl) this._renderTabs();
    }

    _loadTabs() {
        // In a real implementation, we might auto-discover tabs.
        // For now, tabs are registered via exposed API or by importing them in entry point.
        this._renderTabs();
    }

    _renderTabs() {
        this.tabsEl.innerHTML = "";

        this.tabs.forEach(tab => {
            const btn = document.createElement("div");
            btn.className = "openclaw-tab";
            if (tab.icon) {
                const icon = document.createElement("i");
                icon.className = `openclaw-tab-icon ${tab.icon}`;
                const label = document.createElement("span");
                label.className = "openclaw-tab-label";
                label.textContent = tab.title;
                btn.appendChild(icon);
                btn.appendChild(label);
            } else {
                btn.textContent = tab.title;
            }
            btn.onclick = () => this.activateTab(tab.id);
            if (tab.id === this.activeTabId) btn.classList.add("active");

            this.tabsEl.appendChild(btn);

            // Create container for tab content if not exists
            if (!this.contentEl.querySelector(`#openclaw-tab-${tab.id}`)) {
                const pane = document.createElement("div");
                pane.id = `openclaw-tab-${tab.id}`;
                pane.className = "openclaw-tab-pane";
                this.contentEl.appendChild(pane);
            }
        });

        normalizeLegacyClassNames(this.tabsEl);
        normalizeLegacyClassNames(this.contentEl);
        applyLegacyClassAliases(this.tabsEl);
        applyLegacyClassAliases(this.contentEl);
    }

    activateTab(id) {
        this.activeTabId = id;
        setMirroredStorageValue(localStorage, STORAGE_KEYS.local.activeTab, id);

        // Update Tab Buttons
        Array.from(this.tabsEl.children).forEach((btn, idx) => {
            const tab = this.tabs[idx];
            if (tab.id === id) btn.classList.add("active");
            else btn.classList.remove("active");
        });

        // Update Panes
        Array.from(this.contentEl.children).forEach(pane => {
            if (pane.id === `openclaw-tab-${id}` || pane.id === `moltbot-tab-${id}`) {
                pane.classList.add("active");
            }
            else pane.classList.remove("active");
        });

        // Lazy Render
        const tab = this.tabs.find(t => t.id === id);
        const pane =
            this.contentEl.querySelector(`#openclaw-tab-${id}`) ||
            this.contentEl.querySelector(`#moltbot-tab-${id}`);
        const shouldRender = tab && pane && (!tab.loaded || !pane.hasChildNodes());
        if (shouldRender) {
            const boundary = new ErrorBoundary(`Tab: ${tab.title}`);
            boundary.run(pane, () => {
                const maybePromise = tab.render(pane);
                if (maybePromise && typeof maybePromise.then === "function") {
                    maybePromise.catch((err) => boundary.showFallback(pane, err));
                }
            });
            tab.loaded = true;
        }

        if (pane) {
            normalizeLegacyClassNames(pane);
            applyLegacyClassAliases(pane);
        }
    }

    _restoreActiveTab() {
        // CRITICAL: keep legacy key fallback to avoid tab-state loss across migration.
        const saved = getMirroredStorageValue(localStorage, STORAGE_KEYS.local.activeTab);
        const defaultTab = this.tabs.length > 0 ? this.tabs[0].id : null;
        this.activateTab(saved && this.tabs.find(t => t.id === saved) ? saved : defaultTab);
    }
}

export const tabManager = new TabManager(); // Singleton for easy registration
