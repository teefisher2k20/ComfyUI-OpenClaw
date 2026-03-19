import { tabManager } from "./openclaw_tabs.js";
import { openclawApi } from "./openclaw_api.js";
import { buildDoctorAdvisoryBanner } from "./openclaw_security_advisory.js";

/**
 * F51: Unified Action Router.
 * Centralizes navigation and command logic for key operator tasks.
 */
export class OpenClawActions {
    constructor(ui, deps = {}) {
        this.ui = ui;
        this.api = deps.api || openclawApi;
        this.tabs = deps.tabs || tabManager;
        this.bannerBuilder = deps.bannerBuilder || buildDoctorAdvisoryBanner;
        this.documentRef = deps.documentRef || document;
        this.windowRef = deps.windowRef || window;
        this.setTimeoutRef = deps.setTimeoutRef || window.setTimeout.bind(window);
        if (Object.prototype.hasOwnProperty.call(deps, "capabilities")) {
            this.capabilities = deps.capabilities;
            this._initPromise = Promise.resolve(this.capabilities);
        } else {
            this.capabilities = null;
            this._initPromise = this._fetchCapabilities();
        }
    }

    async _fetchCapabilities() {
        try {
            const res = await this.api.getCapabilities();
            if (res.ok) {
                this.capabilities = res.data;
            }
        } catch (e) {
            console.warn("OpenClawActions: Failed to fetch capabilities", e);
        }
        return this.capabilities;
    }

    dispatch(actionId, context = null) {
        switch (actionId) {
            case "doctor":
                return this.openDoctor();
            case "queue":
                return this.openQueue();
            case "settings":
                return this.openSettings();
            case "inspect":
                return this.openExplorer();
            default:
                console.warn("Unknown action:", actionId, context);
                return undefined;
        }
    }

    _checkAction(actionName) {
        if (!this.capabilities || !this.capabilities.actions) {
            return { enabled: true, mutating: false };
        }
        const cap = this.capabilities.actions[actionName] || {
            enabled: false,
            mutating: false,
        };

        if (!cap.enabled && cap.blocked_reason) {
            this._showBlockedToast(actionName, cap.blocked_reason);
        }
        return cap;
    }

    _showBlockedToast(actionName, reason) {
        const toast = this.documentRef.createElement("div");
        toast.className = "openclaw-blocked-toast";
        toast.style.cssText = `
            position: fixed; bottom: 20px; right: 20px; z-index: 99999;
            background: #1e1e2e; border: 1px solid #f59e0b;
            border-radius: 8px; padding: 12px 16px; max-width: 380px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            font-family: 'Inter', sans-serif; font-size: 13px;
            color: #e0e0e0; animation: slideIn 0.3s ease;
        `;
        toast.innerHTML = `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                <span style="font-size:16px;">!</span>
                <strong style="color:#f59e0b;">Action Blocked - Split Mode</strong>
            </div>
            <div style="margin-bottom:4px;"><code>${actionName}</code> is not available in the current deployment mode.</div>
            <div style="font-size:11px;color:#999;">${reason || "Use the external control plane for this operation."}</div>
        `;
        this.documentRef.body.appendChild(toast);
        this.setTimeoutRef(() => toast.remove(), 6000);
    }

    async _runGuarded(actionName, fn) {
        await this._initPromise;
        const cap = this._checkAction(actionName);

        if (!cap.enabled) {
            this.ui.showBanner("warning", `Action '${actionName}' is disabled by policy.`);
            return;
        }

        if (cap.mutating) {
            this.ui.showConfirm({
                title: "Confirm Action",
                message: `This action (${actionName}) will modify system state. Proceed?`,
                onConfirm: fn,
            });
            return;
        }

        return fn();
    }

    openSettings(section = "general") {
        this.tabs.activateTab("settings");
        return section;
    }

    openQueue(filter = "all") {
        if (this.tabs.tabs["job-monitor"]) {
            this.tabs.activateTab("job-monitor");
            return filter;
        }
        this.tabs.activateTab("explorer");
        return filter;
    }

    openDoctor() {
        return this._runGuarded("doctor", async () => {
            await this._openDoctorImpl();
        });
    }

    async _openDoctorImpl() {
        this.tabs.activateTab("settings");
        try {
            const res = await this.api.fetch(this.api._path("/security/doctor"));
            if (res.ok && res.data) {
                const report = res.data.report || res.data;
                const advisoryBanner = this.bannerBuilder(report);
                if (advisoryBanner) {
                    this.ui.showBanner(advisoryBanner);
                    return;
                }

                const issueCount = Array.isArray(report?.checks)
                    ? report.checks.filter(
                        (check) => check?.severity === "warn" || check?.severity === "fail"
                    ).length
                    : 0;
                this.ui.showBanner(
                    issueCount > 0 ? "warning" : "success",
                    issueCount > 0
                        ? `Doctor found ${issueCount} issues. See Settings for details.`
                        : "Doctor check passed."
                );
                return;
            }
        } catch (_err) {
            // Capability fallback below.
        }
        this.ui.showBanner(
            "info",
            "Doctor diagnostics endpoint unavailable. Open Settings for manual checks."
        );
    }

    openExplorer(nodeType = null) {
        this.tabs.activateTab("explorer");
        return nodeType;
    }

    openCompare(node = null) {
        this.tabs.activateTab("parameter-lab");
        if (node) {
            console.log("OpenClaw: Compare requested for", node.title || node.type);
            this.setTimeoutRef(() => {
                this.windowRef.dispatchEvent(
                    new CustomEvent("openclaw:lab:compare", { detail: { node } })
                );
                this.windowRef.dispatchEvent(
                    new CustomEvent("moltbot:lab:compare", { detail: { node } })
                );
            }, 0);
        }
    }
}
