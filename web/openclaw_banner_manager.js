import { openclawNotifications } from "./openclaw_notifications.js";

export class OpenClawBannerManager {
    constructor({ notifications = openclawNotifications, onAction = null } = {}) {
        this.notifications = notifications;
        this.onAction = onAction;
        this.container = null;
        this.timer = null;
    }

    bind(container, onAction) {
        this.container = container;
        this.onAction = typeof onAction === "function" ? onAction : this.onAction;
    }

    showBanner(banner) {
        if (!this.container) return;

        let normalized = banner;
        if (arguments.length > 1 && typeof arguments[0] === "string") {
            normalized = {
                severity: arguments[0],
                message: arguments[1],
                id: `legacy_${Date.now()}`,
                ttl_ms: 5000,
            };
        }

        const {
            id,
            severity,
            message,
            ttl_ms,
            action,
            dismissible = true,
        } = normalized;

        const currentBanner = this.container.querySelector(".openclaw-banner");
        if (currentBanner) {
            const currentSeverity = currentBanner.dataset.severity;
            const sameId = currentBanner.dataset.id === id;
            const isCurrentError = currentSeverity === "error";
            const isNewError = severity === "error";
            if (isCurrentError && !isNewError && !sameId) {
                return;
            }
        }

        if (this.timer) {
            clearTimeout(this.timer);
            this.timer = null;
        }

        let bannerEl = currentBanner;
        if (!bannerEl) {
            bannerEl = document.createElement("div");
            const header = this.container.querySelector(".openclaw-header");
            if (!header) return;
            header.after(bannerEl);
        }

        bannerEl.className = `openclaw-banner openclaw-banner-${severity}`;
        bannerEl.dataset.id = id;
        bannerEl.dataset.severity = severity;
        bannerEl.innerHTML = "";

        const messageNode = document.createElement("span");
        messageNode.textContent = message;
        bannerEl.appendChild(messageNode);

        if (action) {
            const button = document.createElement("button");
            button.className = "openclaw-banner-action";
            button.textContent = action.label;
            button.addEventListener("click", () => {
                if (this.onAction) this.onAction(action);
            });
            bannerEl.appendChild(button);
        }

        if (dismissible) {
            const close = document.createElement("button");
            close.className = "openclaw-banner-close";
            close.textContent = "\u00D7";
            close.addEventListener("click", () => {
                bannerEl.remove();
                if (this.timer) {
                    clearTimeout(this.timer);
                    this.timer = null;
                }
            });
            bannerEl.appendChild(close);
        }

        if (ttl_ms > 0) {
            this.timer = setTimeout(() => {
                if (bannerEl.isConnected) bannerEl.remove();
            }, ttl_ms);
        }

        const shouldPersist = normalized.persist != null
            ? Boolean(normalized.persist)
            : severity === "warning" || severity === "error";
        if (shouldPersist) {
            this.notifications.notify({
                id: `banner_${id || severity}`,
                severity,
                message,
                source: normalized.source || "banner",
                dedupeKey: `banner:${id || `${severity}:${message}`}`,
                action,
                metadata: {
                    ttl_ms: ttl_ms || 0,
                },
            });
        }
    }
}
