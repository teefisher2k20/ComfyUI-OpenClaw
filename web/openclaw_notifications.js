import { STORAGE_KEYS, getMirroredStorageValue, setMirroredStorageValue } from "./openclaw_compat.js";

const DEFAULT_LIMIT = 60;

function toIsoString(nowValue) {
    if (typeof nowValue === "number") {
        return new Date(nowValue).toISOString();
    }
    return new Date().toISOString();
}

function safeLocalStorage() {
    try {
        return window.localStorage;
    } catch {
        return null;
    }
}

function cloneAction(action) {
    if (!action || typeof action !== "object") return null;
    return {
        label: String(action.label || "").trim(),
        type: String(action.type || "").trim(),
        payload: String(action.payload || "").trim(),
    };
}

function normalizeEntry(raw) {
    if (!raw || typeof raw !== "object") return null;
    const id = String(raw.id || "").trim();
    const message = String(raw.message || "").trim();
    if (!id || !message) return null;

    return {
        id,
        source: String(raw.source || "system").trim() || "system",
        severity: String(raw.severity || "info").trim() || "info",
        message,
        dedupe_key: String(raw.dedupe_key || "").trim() || `${raw.source || "system"}:${message}`,
        created_at: String(raw.created_at || raw.updated_at || new Date().toISOString()),
        updated_at: String(raw.updated_at || raw.created_at || new Date().toISOString()),
        count: Math.max(1, Number.parseInt(raw.count, 10) || 1),
        acknowledged_at: raw.acknowledged_at ? String(raw.acknowledged_at) : null,
        dismissed_at: raw.dismissed_at ? String(raw.dismissed_at) : null,
        action: cloneAction(raw.action),
        metadata: raw.metadata && typeof raw.metadata === "object" ? { ...raw.metadata } : {},
    };
}

export class OpenClawNotifications {
    constructor(deps = {}) {
        this.storage = deps.storage || safeLocalStorage();
        this.storageKey = deps.storageKey || STORAGE_KEYS.local.notifications;
        this.now = deps.now || (() => Date.now());
        this.limit = deps.limit || DEFAULT_LIMIT;
        this.listeners = new Set();
        this.entries = this._load();
    }

    _storageValue() {
        if (!this.storage || !this.storageKey) return null;
        return getMirroredStorageValue(this.storage, this.storageKey);
    }

    _load() {
        const raw = this._storageValue();
        if (!raw) return [];
        try {
            const data = JSON.parse(raw);
            if (!Array.isArray(data)) return [];
            return data
                .map((entry) => normalizeEntry(entry))
                .filter(Boolean)
                .sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)))
                .slice(0, this.limit);
        } catch {
            return [];
        }
    }

    _save() {
        if (!this.storage || !this.storageKey) return;
        setMirroredStorageValue(this.storage, this.storageKey, JSON.stringify(this.entries.slice(0, this.limit)));
    }

    _emit() {
        const snapshot = this.getEntries({ includeDismissed: true });
        this.listeners.forEach((listener) => listener(snapshot));
    }

    subscribe(listener) {
        if (typeof listener !== "function") {
            return () => { };
        }
        this.listeners.add(listener);
        listener(this.getEntries({ includeDismissed: true }));
        return () => {
            this.listeners.delete(listener);
        };
    }

    getEntries(options = {}) {
        const includeDismissed = Boolean(options.includeDismissed);
        const limit = Math.max(1, Number.parseInt(options.limit, 10) || this.limit);
        return this.entries
            .filter((entry) => includeDismissed || !entry.dismissed_at)
            .slice(0, limit)
            .map((entry) => ({
                ...entry,
                action: cloneAction(entry.action),
                metadata: { ...entry.metadata },
            }));
    }

    getUnreadCount() {
        return this.entries.filter((entry) => !entry.dismissed_at && !entry.acknowledged_at).length;
    }

    notify(payload = {}) {
        const message = String(payload.message || "").trim();
        if (!message) return null;

        const nowIso = toIsoString(this.now());
        const source = String(payload.source || "system").trim() || "system";
        const severity = String(payload.severity || payload.variant || "info").trim() || "info";
        const action = cloneAction(payload.action);
        const dedupeKey = String(payload.dedupeKey || payload.dedupe_key || `${source}:${severity}:${message}`).trim();
        const metadata = payload.metadata && typeof payload.metadata === "object" ? { ...payload.metadata } : {};

        const existing = this.entries.find((entry) => entry.dedupe_key === dedupeKey && !entry.dismissed_at);
        if (existing) {
            existing.message = message;
            existing.source = source;
            existing.severity = severity;
            existing.updated_at = nowIso;
            existing.count = Math.max(1, Number(existing.count || 1) + 1);
            existing.acknowledged_at = null;
            existing.action = action;
            existing.metadata = metadata;
        } else {
            const dismissed = this.entries.find((entry) => entry.dedupe_key === dedupeKey && entry.dismissed_at);
            if (dismissed && dismissed.message === message && dismissed.severity === severity) {
                dismissed.updated_at = nowIso;
                dismissed.action = action;
                dismissed.metadata = metadata;
                this._save();
                this._emit();
                return { ...dismissed };
            }
            this.entries.unshift({
                id: String(payload.id || `ntf_${Math.random().toString(36).slice(2, 10)}`),
                source,
                severity,
                message,
                dedupe_key: dedupeKey,
                created_at: nowIso,
                updated_at: nowIso,
                count: 1,
                acknowledged_at: null,
                dismissed_at: null,
                action,
                metadata,
            });
        }

        this.entries = this.entries
            .sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)))
            .slice(0, this.limit);
        this._save();
        this._emit();
        return this.entries[0];
    }

    acknowledge(id) {
        const target = this.entries.find((entry) => entry.id === id && !entry.dismissed_at);
        if (!target || target.acknowledged_at) return null;
        target.acknowledged_at = toIsoString(this.now());
        target.updated_at = target.updated_at || target.acknowledged_at;
        this._save();
        this._emit();
        return { ...target };
    }

    dismiss(id) {
        const target = this.entries.find((entry) => entry.id === id && !entry.dismissed_at);
        if (!target) return null;
        target.dismissed_at = toIsoString(this.now());
        if (!target.acknowledged_at) {
            target.acknowledged_at = target.dismissed_at;
        }
        this._save();
        this._emit();
        return { ...target };
    }

    clearAll() {
        this.entries = [];
        this._save();
        this._emit();
    }
}

export const openclawNotifications = new OpenClawNotifications();
