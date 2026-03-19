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
});
