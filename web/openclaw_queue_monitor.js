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
            this.showBanner("success", "\u2705 OpenClaw Backend Connected", "connection_restored", 3000);
        }

        const type = data.event_type;
        const pid = data.prompt_id ? data.prompt_id.slice(0, 8) : "???";

        switch (type) {
            case "queued":
                this.showBanner("info", `\u23F3 Job ${pid} queued`, `job_${type}`, 2000);
                break;
            case "running":
                this.showBanner("info", `\u25B6 Job ${pid} running...`, `job_${type}`, 5000);
                break;
            case "failed":
                this.showBanner("error", `\u274C Job ${pid} failed`, `job_${type}`, 10000);
                break;
            case "completed":
                break;
        }
    }

    handleConnectionError(err) {
        if (this.isConnected) {
            this.isConnected = false;
            this.showBanner("error", "\u26A0\uFE0F Backend Disconnected. Retrying...", "connection_lost");
        }
        return err;
    }

    async checkHealth() {
        try {
            const res = await this.api.getHealth();
            if (res.ok && res.data) {
                if (!this.isConnected) {
                    this.isConnected = true;
                    this.showBanner("success", "\u2705 Connection Restored", "connection_restored", 3000);
                    if (!this.es || this.es.readyState === 2) {
                        this.connectSSE();
                    }
                }

                const stats = res.data.stats || {};
                const obs = stats.observability || {};
                if (obs.total_dropped > 0) {
                    this.showBanner(
                        "warning",
                        `\u26A0\uFE0F High load: ${obs.total_dropped} events dropped.`,
                        "backpressure"
                    );
                }
            } else if (this.isConnected) {
                this.isConnected = false;
                this.showBanner("error", "\u26A0\uFE0F Backend Unreachable", "health_check_failed");
            }
        } catch (_err) {
            if (this.isConnected) {
                this.isConnected = false;
                this.showBanner("error", "\u26A0\uFE0F Connection Error", "health_check_exception");
            }
        }
    }

    showBanner(type, message, statusId, ttl = this.bannerTTL) {
        const now = this.now();
        if (this.lastStatusId === statusId && (now - this.lastBannerTime < ttl)) {
            return;
        }

        this.lastStatusId = statusId;
        this.lastBannerTime = now;

        this.ui.showBanner({
            id: statusId || "monitor_" + now,
            severity: type,
            message,
            source: "QueueMonitor",
            ttl_ms: ttl,
            dismissible: true,
        });
    }
}
