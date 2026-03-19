/**
 * F7: OpenClaw UI Shell
 * Manages the main sidebar layout: Header, Tabs, Content.
 */
import { tabManager } from "./openclaw_tabs.js";
import { ErrorBoundary } from "./ErrorBoundary.js";
import { openclawApi } from "./openclaw_api.js";
import { normalizeLegacyClassNames } from "./openclaw_utils.js";
import { OpenClawActions } from "./openclaw_actions.js";
import { QueueMonitor } from "./openclaw_queue_monitor.js";
import { openclawNotifications } from "./openclaw_notifications.js";

export class OpenClawUI {
    constructor() {
        this.container = null;
        this.boundary = new ErrorBoundary("OpenClawUI");
        this.floating = {
            panel: null,
            content: null,
        };
        this.notificationsOpen = false;
        this.notificationNodes = null;
        this._notificationSnapshot = [];
        this._unsubscribeNotifications = openclawNotifications.subscribe((entries) => {
            this._notificationSnapshot = entries;
            this._renderNotificationSnapshot();
        });
    }

    /**
     * Mount the UI into a provided container (sidebar render target).
     */
    mount(container) {
        this.container = container;
        // CRITICAL: Must run before render to prevent first-paint clipping in Splitter sidebar host.
        this._enforceSidebarMinWidth(container);
        this.boundary.run(container, () => this._render(container));
    }

    _enforceSidebarMinWidth(container) {
        // IMPORTANT: Keep this value aligned with CSS .openclaw-sidebar-container min-width.
        const minWidthPx = 560;

        const applyMinWidth = () => {
            // CRITICAL: Sidebar width is controlled by ComfyUI SplitterPanel (.side-bar-panel),
            // not by our inner root container. If we only set inner min-width, content gets clipped.
            const sidePanel = container.closest(".side-bar-panel");
            const splitterPanel = sidePanel || container.closest(".p-splitterpanel");
            if (splitterPanel) {
                splitterPanel.style.minWidth = `${minWidthPx}px`;
            }

            const sidebarContent = container.closest(".sidebar-content-container");
            if (sidebarContent) {
                sidebarContent.style.minWidth = `${minWidthPx}px`;
            }

            container.style.minWidth = `${minWidthPx}px`;
        };

        // Run once now, then again after mount/paint to handle late-attached sidebar wrappers.
        applyMinWidth();
        if (typeof requestAnimationFrame === "function") {
            requestAnimationFrame(applyMinWidth);
        } else {
            setTimeout(applyMinWidth, 0);
        }
    }

    /**
     * Legacy fallback: toggle a floating panel (must not touch document.body directly).
     */
    toggleFloatingPanel() {
        if (!this.floating.panel) {
            const panel = document.createElement("div");
            panel.className = "openclaw-floating-panel";

            const close = document.createElement("button");
            close.className = "openclaw-floating-close";
            close.textContent = "\u00D7";
            close.title = "Close";
            close.addEventListener("click", () => {
                panel.classList.remove("visible");
            });

            const content = document.createElement("div");
            content.className = "openclaw-floating-content";

            panel.appendChild(close);
            panel.appendChild(content);
            document.body.appendChild(panel);

            this.floating.panel = panel;
            this.floating.content = content;
        }

        const panel = this.floating.panel;
        const content = this.floating.content;
        const isVisible = panel.classList.contains("visible");

        if (isVisible) {
            panel.classList.remove("visible");
            return;
        }

        panel.classList.add("visible");
        this.mount(content);
    }

    _render(container) {
        container.innerHTML = "";
        container.className = "openclaw-sidebar-container";

        // 1. Header
        const header = document.createElement("div");
        header.className = "openclaw-header";

        const statusDot = document.createElement("div");
        statusDot.className = "openclaw-status-dot ok";
        statusDot.title = "System Status";
        this.statusDot = statusDot;

        const title = document.createElement("div");
        title.className = "openclaw-title";
        title.textContent = "OpenClaw";

        // F9: About badges (version fetched from /openclaw/health; legacy /moltbot/health)
        const badges = document.createElement("div");
        badges.className = "openclaw-badges";
        const versionSpan = document.createElement("span");
        versionSpan.className = "openclaw-version";
        versionSpan.textContent = "v...";
        const repoLink = document.createElement("a");
        repoLink.href = "https://github.com/rookiestar28/ComfyUI-OpenClaw";
        repoLink.target = "_blank";
        repoLink.className = "openclaw-repo-link";
        repoLink.title = "View on GitHub";
        repoLink.textContent = "View on GitHub";
        badges.appendChild(versionSpan);
        badges.appendChild(repoLink);
        badges.appendChild(this._buildNotificationToggle());

        // Fetch version from health endpoint
        openclawApi.getHealth().then(res => {
            if (res.ok && res.data) {
                const data = res.data;
                if (data.pack) {
                    versionSpan.textContent = `v${data.pack.version}`;
                }

                // F55: Control plane mode indicator badge
                const cpMode = data?.control_plane?.mode || data?.deployment_profile || "local";
                const modeBadge = document.createElement("span");
                modeBadge.className = `openclaw-mode-badge openclaw-mode-${cpMode}`;
                modeBadge.textContent = cpMode.toUpperCase();
                modeBadge.title = `Control plane: ${cpMode}`;
                // Style inline for immediate visibility
                modeBadge.style.cssText = `
                    font-size: 10px; padding: 1px 6px; border-radius: 4px;
                    font-weight: 600; margin-left: 6px; letter-spacing: 0.5px;
                `;
                if (cpMode === "split") {
                    modeBadge.style.background = "#f59e0b";
                    modeBadge.style.color = "#1a1a2e";
                } else if (cpMode === "hardened") {
                    modeBadge.style.background = "#ef4444";
                    modeBadge.style.color = "#fff";
                } else {
                    modeBadge.style.background = "#22c55e";
                    modeBadge.style.color = "#1a1a2e";
                }
                badges.appendChild(modeBadge);
                this._controlPlaneMode = cpMode;

                // S15: Check exposure
                this.checkExposure(data?.access_policy);

                // R87: Check Backpressure
                const obs = data.stats?.observability;
                if (obs && obs.total_dropped > 0) {
                    const dropCount = obs.total_dropped;
                    this.showBanner("warning", `\u26A0\uFE0F High load: ${dropCount} observability events dropped (Queue full). logs/traces might be incomplete.`);
                }
            } else {
                versionSpan.textContent = "v?.?.?";
            }
        }).catch(() => {
            versionSpan.textContent = "v?.?.?";
        });

        header.appendChild(statusDot);
        header.appendChild(title);
        header.appendChild(badges);
        container.appendChild(header);
        container.appendChild(this._buildNotificationPanel());

        // 2. Tab Bar
        const tabBar = document.createElement("div");
        tabBar.className = "openclaw-tabs";
        this.tabBar = tabBar;
        container.appendChild(tabBar);

        // 3. Content Area
        const contentArea = document.createElement("div");
        contentArea.className = "openclaw-content";
        this.contentArea = contentArea;
        container.appendChild(contentArea);

        // Initialize Tabs
        tabManager.init(tabBar, contentArea);
        normalizeLegacyClassNames(container);
        this._renderNotificationSnapshot();
    }

    _buildNotificationToggle() {
        const button = document.createElement("button");
        button.className = "openclaw-notification-toggle";
        button.id = "openclaw-notification-toggle";
        button.type = "button";
        button.innerHTML = `
            <span class="openclaw-notification-toggle-label">Alerts</span>
            <span class="openclaw-notification-badge" hidden>0</span>
        `;
        button.addEventListener("click", () => this.toggleNotifications());
        this.notificationNodes = this.notificationNodes || {};
        this.notificationNodes.toggle = button;
        this.notificationNodes.badge = button.querySelector(".openclaw-notification-badge");
        return button;
    }

    _buildNotificationPanel() {
        const panel = document.createElement("div");
        panel.className = "openclaw-notification-panel";
        panel.id = "openclaw-notification-panel";
        panel.hidden = true;

        panel.innerHTML = `
            <div class="openclaw-notification-panel-header">
                <div>
                    <div class="openclaw-notification-panel-title">Notification Center</div>
                    <div class="openclaw-notification-panel-subtitle">Persistent operator alerts and actions</div>
                </div>
                <button type="button" class="openclaw-notification-close" aria-label="Close notification center">x</button>
            </div>
            <div class="openclaw-notification-list"></div>
        `;

        panel.querySelector(".openclaw-notification-close").addEventListener("click", () => {
            this.notificationsOpen = false;
            this._renderNotificationSnapshot();
        });

        panel.querySelector(".openclaw-notification-list").addEventListener("click", (event) => {
            const button = event.target.closest("button[data-notification-action]");
            if (!button) return;
            const action = button.getAttribute("data-notification-action");
            const id = button.getAttribute("data-notification-id");
            if (!id) return;

            if (action === "ack") {
                openclawNotifications.acknowledge(id);
                return;
            }
            if (action === "dismiss") {
                openclawNotifications.dismiss(id);
                return;
            }
            if (action === "open") {
                const entry = this._notificationSnapshot.find((item) => item.id === id);
                if (!entry?.action) return;
                openclawNotifications.acknowledge(id);
                this.handleAction(entry.action);
            }
        });

        this.notificationNodes = this.notificationNodes || {};
        this.notificationNodes.panel = panel;
        this.notificationNodes.list = panel.querySelector(".openclaw-notification-list");
        return panel;
    }

    toggleNotifications() {
        this.notificationsOpen = !this.notificationsOpen;
        this._renderNotificationSnapshot();
    }

    _formatNotificationTime(value) {
        if (!value) return "";
        try {
            return new Date(value).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
            });
        } catch {
            return "";
        }
    }

    _renderNotificationSnapshot() {
        const badge = this.notificationNodes?.badge;
        const panel = this.notificationNodes?.panel;
        const list = this.notificationNodes?.list;
        if (!badge || !panel || !list) return;

        const activeEntries = this._notificationSnapshot.filter((entry) => !entry.dismissed_at);
        const unreadCount = activeEntries.filter((entry) => !entry.acknowledged_at).length;
        badge.textContent = unreadCount > 99 ? "99+" : String(unreadCount);
        badge.hidden = unreadCount <= 0;
        panel.hidden = !this.notificationsOpen;

        if (activeEntries.length === 0) {
            list.innerHTML = '<div class="openclaw-notification-empty">No active operator notifications.</div>';
            return;
        }

        list.innerHTML = activeEntries.map((entry) => {
            const actionHtml = entry.action?.type && entry.action?.payload
                ? `<button type="button" class="openclaw-btn openclaw-btn-sm" data-notification-action="open" data-notification-id="${entry.id}">${entry.action.label || "Open"}</button>`
                : "";
            const countHtml = entry.count > 1
                ? `<span class="openclaw-notification-count">x${entry.count}</span>`
                : "";
            const ackLabel = entry.acknowledged_at ? "Acknowledged" : "Acknowledge";
            const ackDisabled = entry.acknowledged_at ? "disabled" : "";
            return `
                <div class="openclaw-notification-item openclaw-notification-${entry.severity}">
                    <div class="openclaw-notification-meta">
                        <span class="openclaw-notification-source">${entry.source}</span>
                        <span class="openclaw-notification-time">${this._formatNotificationTime(entry.updated_at)}</span>
                    </div>
                    <div class="openclaw-notification-message">${entry.message}</div>
                    <div class="openclaw-notification-footer">
                        <div class="openclaw-notification-state">
                            <span class="openclaw-notification-severity">${entry.severity}</span>
                            ${countHtml}
                        </div>
                        <div class="openclaw-notification-actions">
                            ${actionHtml}
                            <button type="button" class="openclaw-btn openclaw-btn-sm" data-notification-action="ack" data-notification-id="${entry.id}" ${ackDisabled}>${ackLabel}</button>
                            <button type="button" class="openclaw-btn openclaw-btn-sm openclaw-btn-danger" data-notification-action="dismiss" data-notification-id="${entry.id}">Dismiss</button>
                        </div>
                    </div>
                </div>
            `;
        }).join("");
    }

    /**
     * S15: Check if OpenClaw is exposed remotely and warn user.
     */
    checkExposure(policy) {
        if (!policy) return;

        const isLocal = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

        // Warn if not local
        if (!isLocal) {
            const isProtected = policy.observability === "token" && policy.token_configured;

            if (!isProtected) {
                // High risk: Remote + No Token
                this.showBanner("warning", "\u26A0\uFE0F Remote access detected; logs/config are protected unless you explicitly enable token-based access.");
            } else {
                // Medium risk: Remote + Token (Just info)
                // Optionally show nothing, or a small "Remote Access Secured" badge
                // console.log("OpenClaw remote access secured by token.");
            }
        }
    }

    /**
     * F49: Display a banner with id, severity, ttl, and optional action.
     * @param {Object} banner - { id, severity, message, source, ttl_ms, dismissible, action }
     */
    showBanner(banner) {
        // If passed raw args (legacy compatibility)
        if (arguments.length > 1 && typeof arguments[0] === "string") {
            banner = {
                severity: arguments[0],
                message: arguments[1],
                id: "legacy_" + Date.now(),
                ttl_ms: 5000
            };
        }

        const { id, severity, message, ttl_ms, action, dismissible = true } = banner;

        // 1. Priority Check
        // If an error is currently shown, don't replace with info/warning unless it's a new error
        const currentBanner = this.container.querySelector('.openclaw-banner');
        if (currentBanner) {
            const currentSeverity = currentBanner.dataset.severity;
            const isCurrentError = currentSeverity === "error";
            const isNewError = severity === "error";

            // If current is error and new is not, ignore new (unless current is stale? handled by TTL)
            // Exception: update content if same ID
            const sameId = currentBanner.dataset.id === id;
            if (isCurrentError && !isNewError && !sameId) {
                return; // Suppress lower priority
            }
        }

        // 2. Clear existing timer
        if (this._bannerTimer) {
            clearTimeout(this._bannerTimer);
            this._bannerTimer = null;
        }

        // 3. Render
        let bannerEl = currentBanner; // Reuse or create
        if (!bannerEl) {
            bannerEl = document.createElement("div");
            // Insert after header
            const header = this.container.querySelector('.openclaw-header');
            header.after(bannerEl);
        }

        bannerEl.className = `openclaw-banner openclaw-banner-${severity}`;
        bannerEl.dataset.id = id;
        bannerEl.dataset.severity = severity;
        bannerEl.innerHTML = ""; // Clear content

        // Message
        const msgSpan = document.createElement("span");
        msgSpan.textContent = message;
        bannerEl.appendChild(msgSpan);

        // Action Button
        if (action) {
            const btn = document.createElement("button");
            btn.className = "openclaw-banner-action";
            btn.textContent = action.label;
            btn.addEventListener("click", () => this.handleAction(action));
            bannerEl.appendChild(btn);
        }

        // Dismiss Button
        if (dismissible) {
            const close = document.createElement("button");
            close.className = "openclaw-banner-close";
            close.textContent = "\u00D7";
            close.addEventListener("click", () => {
                bannerEl.remove();
                if (this._bannerTimer) clearTimeout(this._bannerTimer);
            });
            bannerEl.appendChild(close);
        }

        // 4. TTL / Auto-dismiss
        if (ttl_ms > 0) {
            this._bannerTimer = setTimeout(() => {
                if (bannerEl.isConnected) bannerEl.remove();
            }, ttl_ms);
        }

        const shouldPersist = banner.persist != null
            ? Boolean(banner.persist)
            : severity === "warning" || severity === "error";
        if (shouldPersist) {
            openclawNotifications.notify({
                id: `banner_${id || severity}`,
                severity,
                message,
                source: banner.source || "banner",
                dedupeKey: `banner:${id || `${severity}:${message}`}`,
                action,
                metadata: {
                    ttl_ms: ttl_ms || 0,
                },
            });
        }
    }

    handleAction(action) {
        if (!action) return;

        const run = () => {
            switch (action.type) {
                case "url":
                    window.open(action.payload, "_blank");
                    break;
                case "tab":
                    tabManager.activateTab(action.payload);
                    break;
                case "action":
                    // F51: Route through OpenClaw actions singleton.
                    if (openclawActions && openclawActions.dispatch) {
                        openclawActions.dispatch(action.payload);
                    } else {
                        console.log("Action triggered:", action.payload);
                    }
                    break;
            }
        };

        // F51: Check if action requires confirmation (heuristic or explicit)
        // For now, only explicit 'confirm' property in action banner handles this,
        // OR if the action type itself implies mutation.
        // But Banner actions are usually just navigation.
        // Use showConfirm if the banner action metadata says so?
        // Let's assume standard banner actions are safe unless specified.
        run();
    }

    /**
     * F51: Glassmorphism Confirmation Modal.
     * @param {Object} options - { title, message, fatal, onConfirm }
     */
    showConfirm({ title, message, fatal = false, onConfirm }) {
        // Create modal overlay
        const overlay = document.createElement("div");
        overlay.className = "openclaw-modal-overlay";

        const modal = document.createElement("div");
        modal.className = `openclaw-modal ${fatal ? "fatal" : ""}`;

        const h3 = document.createElement("h3");
        h3.textContent = title || "Confirm Action";

        const p = document.createElement("p");
        p.textContent = message || "Are you sure?";

        const buttons = document.createElement("div");
        buttons.className = "openclaw-modal-buttons";

        const cancelBtn = document.createElement("button");
        cancelBtn.className = "openclaw-btn secondary";
        cancelBtn.textContent = "Cancel";
        cancelBtn.onclick = () => overlay.remove();

        const confirmBtn = document.createElement("button");
        confirmBtn.className = `openclaw-btn ${fatal ? "danger" : "primary"}`;
        confirmBtn.textContent = "Confirm";
        confirmBtn.onclick = () => {
            overlay.remove();
            if (onConfirm) onConfirm();
        };

        buttons.appendChild(cancelBtn);
        buttons.appendChild(confirmBtn);

        modal.appendChild(h3);
        modal.appendChild(p);
        modal.appendChild(buttons);
        overlay.appendChild(modal);

        this.container.appendChild(overlay);
        normalizeLegacyClassNames(overlay);
    }
}

export const openclawUI = new OpenClawUI();
export const openclawActions = new OpenClawActions(openclawUI);
export { OpenClawActions } from "./openclaw_actions.js";
export { QueueMonitor } from "./openclaw_queue_monitor.js";

const monitor = new QueueMonitor(openclawUI);
monitor.start();
