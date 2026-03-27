import { describe, expect, it } from "vitest";
import {
    HOST_SURFACES,
    getHostSurfaceCapabilities,
    resolveHostSurface,
    stampHostSurfaceMetadata,
} from "../../openclaw_host_surface.js";

describe("openclaw_host_surface", () => {
    it("treats electron bridge presence as desktop host surface", () => {
        const hostSurface = resolveHostSurface({
            win: { electronAPI: { getPlatform() {} } },
        });
        expect(hostSurface).toBe(HOST_SURFACES.desktop);
    });

    it("prefers explicit standalone host hints over generic runtime defaults", () => {
        const hostSurface = resolveHostSurface({
            app: { openclawHostSurface: "standalone_frontend" },
            win: { electronAPI: { getPlatform() {} } },
        });
        expect(hostSurface).toBe(HOST_SURFACES.standaloneFrontend);
    });

    it("derives desktop capabilities and stamps container metadata", () => {
        const container = document.createElement("div");
        const capabilities = stampHostSurfaceMetadata(container, {
            win: { electronAPI: { getPlatform() {} } },
        });

        expect(capabilities).toEqual({
            hostSurface: HOST_SURFACES.desktop,
            isDesktop: true,
            supportsElectronBridge: true,
        });
        expect(container.dataset.openclawHostSurface).toBe("desktop");
        expect(container.dataset.openclawDesktopHost).toBe("true");
    });

    it("falls back to standalone frontend when desktop-only signals are absent", () => {
        expect(
            getHostSurfaceCapabilities({
                win: {},
            })
        ).toEqual({
            hostSurface: HOST_SURFACES.standaloneFrontend,
            isDesktop: false,
            supportsElectronBridge: false,
        });
    });
});
