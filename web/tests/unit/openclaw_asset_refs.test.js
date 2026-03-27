import { describe, expect, it } from "vitest";

import { extractHistoryImageRefs, normalizeComfyOutputRef } from "../../openclaw_asset_refs.js";

describe("openclaw asset refs", () => {
    it("keeps classic history refs on the /view filename+type contract", () => {
        expect(
            normalizeComfyOutputRef({
                filename: "result.png",
                subfolder: "session-a",
                type: "temp",
            })
        ).toEqual({
            filename: "result.png",
            subfolder: "session-a",
            type: "temp",
            asset_hash: "",
            is_asset_backed: false,
            viewParams: {
                filename: "result.png",
                subfolder: "session-a",
                type: "temp",
            },
        });
    });

    it("prefers asset hashes while keeping display filename metadata", () => {
        expect(
            normalizeComfyOutputRef({
                filename: "preview.png",
                type: "output",
                asset_hash: "blake3:abc123",
            })
        ).toEqual({
            filename: "preview.png",
            subfolder: "",
            type: "output",
            asset_hash: "blake3:abc123",
            is_asset_backed: true,
            viewParams: {
                filename: "blake3:abc123",
            },
        });
    });

    it("accepts upload-style nested asset metadata", () => {
        expect(
            normalizeComfyOutputRef({
                name: "uploaded.png",
                asset: {
                    asset_hash: "blake3:def456",
                },
            })
        ).toEqual({
            filename: "uploaded.png",
            subfolder: "",
            type: "output",
            asset_hash: "blake3:def456",
            is_asset_backed: true,
            viewParams: {
                filename: "blake3:def456",
            },
        });
    });

    it("extracts mixed history outputs without dropping temp classifications", () => {
        expect(
            extractHistoryImageRefs({
                outputs: {
                    "1": {
                        images: [
                            {
                                filename: "classic.png",
                                subfolder: "",
                                type: "output",
                            },
                            {
                                filename: "temp-preview.png",
                                subfolder: "preview",
                                type: "temp",
                                asset_hash: "blake3:temp123",
                            },
                        ],
                    },
                },
            })
        ).toEqual([
            {
                filename: "classic.png",
                subfolder: "",
                type: "output",
                asset_hash: "",
                is_asset_backed: false,
                viewParams: {
                    filename: "classic.png",
                    type: "output",
                },
            },
            {
                filename: "temp-preview.png",
                subfolder: "preview",
                type: "temp",
                asset_hash: "blake3:temp123",
                is_asset_backed: true,
                viewParams: {
                    filename: "blake3:temp123",
                },
            },
        ]);
    });
});
