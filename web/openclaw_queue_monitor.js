import { openclawApi } from "./openclaw_api.js";

/**
 * F48/F49: Queue Lifecycle Monitor.
 * Consumes R71 events (SSE) with polling fallback to show deduplicated status banners.
 * Handles disconnected state and recovery based on B-Strict/B-Loose contracts.
 */
export class QueueMonitor {
    constructor(ui, deps = {}) {
        this.ui = ui;
        this.api = deps.api || openclawApi;
        this.setIntervalRef = deps.setIntervalRef || window.setInterval.bind(window);
        this.now = deps.now || (() => Date.now());
        this.lastBannerTime = 0;
        this.lastStatusId = null;
        this.bannerTTL = 5000;
        this.es = null;
        this.isConnected = true;
    }

    start() {
        this.connectSSE();
        this.setIntervalRef(() => this.checkHealth(), 10000);
    }

    connectSSE() {
        if (this.es) {
            this.es.close();
        }

        this.es = this.api.subscribeEvents(
            (data) => this.handleEvent(data),
            (err) => this.handleConnectionError(err)
        );
    }

    handleEvent(data) {
        if (!this.isConnected) {
            this.isConnected = true;
            this.showBanner({
                severity: "success",
                message: "\u2705 OpenClaw Backend Connected",
                id: "connection_restored",
                ttl_ms: 3000,
                source: "queue-monitor",
            });
        }

        const type = data.event_type;
        const pid = data.prompt_id ? data.prompt_id.slice(0, 8) : "???";

        switch (type) {
            case "queued":
                this.showBanner({
                    severity: "info",
                    message: `\u23F3 Job ${pid} queued`,
                    id: `job_${type}`,
                    ttl_ms: 2000,
                    source: "queue-monitor",
                });
                break;
            case "running":
                this.showBanner({
                    severity: "info",
                    message: `\u25B6 Job ${pid} running...`,
                    id: `job_${type}`,
                    ttl_ms: 5000,
                    source: "queue-monitor",
                });
                break;
            case "failed":
                this.showBanner({
                    severity: "error",
                    message: `\u274C Job ${pid} failed`,
                    id: `job_${type}`,
                    ttl_ms: 10000,
                    source: "queue-monitor",
                    persist: true,
                    action: {
                        label: "Open Jobs",
                        type: "tab",
                        payload: "job-monitor",
                    },
                });
                break;
            case "completed":
                break;
        }
    }

    handleConnectionError(err) {
        if (this.isConnected) {
            this.isConnected = false;
            this.showBanner({
                severity: "error",
                message: "\u26A0\uFE0F Backend Disconnected. Retrying...",
                id: "connection_lost",
                source: "queue-monitor",
                persist: true,
            });
        }
        return err;
    }

    async checkHealth() {
        try {
            const res = await this.api.getHealth();
            if (res.ok && res.data) {
                if (!this.isConnected) {
                    this.isConnected = true;
                    this.showBanner({
                        severity: "success",
                        message: "\u2705 Connection Restored",
                        id: "connection_restored",
                        ttl_ms: 3000,
                        source: "queue-monitor",
                    });
                    if (!this.es || this.es.readyState === 2) {
                        this.connectSSE();
                    }
                }

                const stats = res.data.stats || {};
                const obs = stats.observability || {};
                if (obs.total_dropped > 0) {
                    this.showBanner({
                        severity: "warning",
                        message: `\u26A0\uFE0F High load: ${obs.total_dropped} events dropped.`,
                        id: "backpressure",
                        source: "queue-monitor",
                        persist: true,
                        action: {
                            label: "Open Explorer",
                            type: "tab",
                            payload: "explorer",
                        },
                    });
                }
            } else if (this.isConnected) {
                this.isConnected = false;
                this.showBanner({
                    severity: "error",
                    message: "\u26A0\uFE0F Backend Unreachable",
                    id: "health_check_failed",
                    source: "queue-monitor",
                    persist: true,
                });
            }
        } catch (_err) {
            if (this.isConnected) {
                this.isConnected = false;
                this.showBanner({
                    severity: "error",
                    message: "\u26A0\uFE0F Connection Error",
                    id: "health_check_exception",
                    source: "queue-monitor",
                    persist: true,
                });
            }
        }
    }

    showBanner(type, message, statusId, ttl = this.bannerTTL) {
        const payload = typeof type === "object"
            ? {
                id: type.id || `monitor_${this.now()}`,
                severity: type.severity || "info",
                message: type.message || "",
                source: type.source || "QueueMonitor",
                ttl_ms: type.ttl_ms != null ? type.ttl_ms : this.bannerTTL,
                dismissible: type.dismissible !== false,
                action: type.action,
                persist: type.persist,
            }
            : {
                id: statusId || `monitor_${this.now()}`,
                severity: type,
                message,
                source: "QueueMonitor",
                ttl_ms: ttl,
                dismissible: true,
            };
        const now = this.now();
        if (this.lastStatusId === payload.id && (now - this.lastBannerTime < payload.ttl_ms)) {
            return;
        }

        this.lastStatusId = payload.id;
        this.lastBannerTime = now;
        this.ui.showBanner(payload);
    }
}
