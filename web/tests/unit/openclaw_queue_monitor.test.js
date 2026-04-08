import { describe, expect, it, vi } from "vitest";

vi.mock("../../openclaw_api.js", () => ({
    openclawApi: {
        getHealth: vi.fn(),
        subscribeEvents: vi.fn(),
    },
}));

const { QueueMonitor } = await import("../../openclaw_queue_monitor.js");

describe("QueueMonitor", () => {
    it("deduplicates repeated status banners within the ttl window", () => {
        const ui = { showBanner: vi.fn() };
        let nowValue = 1000;
        const monitor = new QueueMonitor(ui, {
            api: {},
            now: () => nowValue,
            setIntervalRef: vi.fn(),
        });

        monitor.showBanner("info", "Queued", "job_queued", 5000);
        nowValue = 2000;
        monitor.showBanner("info", "Queued", "job_queued", 5000);
        nowValue = 7000;
        monitor.showBanner("info", "Queued", "job_queued", 5000);

        expect(ui.showBanner).toHaveBeenCalledTimes(2);
        expect(ui.showBanner.mock.calls[0][0].severity).toBe("info");
    });

    it("reconnects the event stream when health checks recover from a disconnect", async () => {
        const ui = { showBanner: vi.fn() };
        const closedStream = { readyState: 2, close: vi.fn() };
        const subscribeEvents = vi.fn(() => ({ readyState: 1, close: vi.fn() }));
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                subscribeEvents,
            },
            setIntervalRef: vi.fn(),
        });

        monitor.isConnected = false;
        monitor.es = closedStream;

        await monitor.checkHealth();

        expect(subscribeEvents).toHaveBeenCalledTimes(1);
        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_restored",
                severity: "success",
            })
        );
    });

    it("does not alert immediately for the first startup disconnect", () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });

        monitor.start();
        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).not.toHaveBeenCalled();
        expect(monitor.isConnected).toBe(false);
    });

    it("alerts after sustained startup disconnect failures cross the grace threshold", () => {
        const ui = { showBanner: vi.fn() };
        let nowValue = 0;
        const monitor = new QueueMonitor(ui, {
            api: {
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            now: () => nowValue,
            setIntervalRef: vi.fn(),
            startupGraceMs: 1000,
            disconnectAlertThreshold: 3,
        });

        monitor.start();
        monitor.handleConnectionError(new Error("offline"));
        nowValue = 500;
        monitor.handleConnectionError(new Error("offline"));
        nowValue = 1500;
        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_lost",
                severity: "error",
                persist: true,
            })
        );
    });

    it("alerts immediately once a previously healthy backend disconnects", async () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                subscribeEvents: vi.fn(),
            },
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });

        await monitor.checkHealth();
        ui.showBanner.mockClear();

        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_lost",
                severity: "error",
                persist: true,
            })
        );
    });

    it("emits persistent failed-job notifications with a job-monitor jump action", () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {},
            now: () => 1000,
            setIntervalRef: vi.fn(),
        });

        monitor.handleEvent({
            event_type: "failed",
            prompt_id: "prompt-12345678",
        });

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                severity: "error",
                persist: true,
                action: expect.objectContaining({
                    type: "tab",
                    payload: "job-monitor",
                }),
            })
        );
    });
});
