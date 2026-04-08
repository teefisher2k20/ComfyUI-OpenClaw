import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiMock, utilsMock } = vi.hoisted(() => ({
    apiMock: {
        parsePngInfo: vi.fn(),
    },
    utilsMock: {
        clearError: vi.fn(),
        copyToClipboard: vi.fn(),
        showError: vi.fn(),
    },
}));

vi.mock("../../openclaw_api.js", () => ({
    openclawApi: apiMock,
}));

vi.mock("../../openclaw_utils.js", () => utilsMock);

import { PngInfoTab } from "../../tabs/png_info_tab.js";

describe("png_info_tab", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
        apiMock.parsePngInfo.mockReset();
        Object.values(utilsMock).forEach((fn) => fn.mockReset());

        class MockFileReader {
            readAsDataURL(file) {
                this.result = `data:${file.type};base64,ZmFrZQ==`;
                queueMicrotask(() => this.onload?.());
            }
        }
        globalThis.FileReader = MockFileReader;
    });

    it("parses selected images and renders structured metadata with copy actions", async () => {
        apiMock.parsePngInfo.mockResolvedValue({
            ok: true,
            data: {
                source: "a1111",
                info: "A1111 metadata detected.",
                parameters: {
                    positive_prompt: "portrait lighting",
                    negative_prompt: "lowres",
                    Steps: "28",
                    Model: "demoModel",
                },
                items: {
                    workflow: {
                        nodes: [{ id: 1, type: "KSampler" }],
                    },
                },
            },
        });

        const container = document.createElement("div");
        PngInfoTab.render(container);

        const fileInput = container.querySelector("#pnginfo-file-input");
        const file = new File(["x"], "meta.png", { type: "image/png" });
        Object.defineProperty(fileInput, "files", {
            configurable: true,
            value: [file],
        });

        fileInput.dispatchEvent(new Event("change"));

        await vi.waitFor(() => {
            expect(apiMock.parsePngInfo).toHaveBeenCalledTimes(1);
        });

        expect(container.querySelector("#pnginfo-summary-card").textContent).toContain("demoModel");
        expect(container.querySelector("#pnginfo-positive").textContent).toContain("portrait lighting");
        expect(container.querySelector("#pnginfo-negative").textContent).toContain("lowres");
        expect(container.querySelector("#pnginfo-raw").textContent).toContain('"type": "KSampler"');

        container.querySelector('[data-action="copy-positive"]').click();
        expect(utilsMock.copyToClipboard).toHaveBeenCalledWith(
            "portrait lighting",
            expect.any(HTMLButtonElement)
        );

        container.querySelector('[data-action="copy-negative"]').click();
        expect(utilsMock.copyToClipboard).toHaveBeenCalledWith(
            "lowres",
            expect.any(HTMLButtonElement)
        );
    });

    it("shows an empty metadata status when the backend returns no metadata blocks", async () => {
        apiMock.parsePngInfo.mockResolvedValue({
            ok: true,
            data: {
                source: "unknown",
                info: "",
                parameters: {},
                items: {},
            },
        });

        const container = document.createElement("div");
        PngInfoTab.render(container);

        const fileInput = container.querySelector("#pnginfo-file-input");
        const file = new File(["x"], "plain.png", { type: "image/png" });
        Object.defineProperty(fileInput, "files", {
            configurable: true,
            value: [file],
        });

        fileInput.dispatchEvent(new Event("change"));

        await vi.waitFor(() => {
            expect(container.querySelector("#pnginfo-status").textContent).toBe("No metadata found");
        });

        expect(container.querySelector("#pnginfo-summary-card").textContent).toContain("UNKNOWN");
        expect(container.querySelector("#pnginfo-raw").textContent).toContain("No raw metadata blocks found.");
    });
});
