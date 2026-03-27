function pickAssetHash(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    const direct = typeof imageRef.asset_hash === "string" ? imageRef.asset_hash.trim() : "";
    if (direct) {
        return direct;
    }
    const nested = imageRef.asset;
    if (nested && typeof nested === "object" && typeof nested.asset_hash === "string") {
        return nested.asset_hash.trim();
    }
    return "";
}

function pickFilename(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    if (typeof imageRef.filename === "string" && imageRef.filename.trim()) {
        return imageRef.filename.trim();
    }
    if (typeof imageRef.name === "string" && imageRef.name.trim()) {
        return imageRef.name.trim();
    }
    return "";
}

export function normalizeComfyOutputRef(imageRef = {}) {
    const assetHash = pickAssetHash(imageRef);
    const filename = pickFilename(imageRef) || assetHash;
    const subfolder = typeof imageRef.subfolder === "string" ? imageRef.subfolder : "";
    const type = typeof imageRef.type === "string" && imageRef.type ? imageRef.type : "output";

    if (!filename) {
        return null;
    }

    // IMPORTANT: asset-backed refs still resolve through /view; do not turn this
    // helper into a direct /api/assets dependency or classic history parity breaks.
    const viewParams = assetHash
        ? { filename: assetHash }
        : {
            filename,
            type,
            ...(subfolder ? { subfolder } : {}),
        };

    return {
        filename,
        subfolder,
        type,
        asset_hash: assetHash || "",
        is_asset_backed: Boolean(assetHash),
        viewParams,
    };
}

export function extractHistoryImageRefs(historyItem = {}) {
    const results = [];
    const outputs = historyItem && typeof historyItem === "object" ? (historyItem.outputs || {}) : {};

    for (const nodeOutput of Object.values(outputs)) {
        const images = Array.isArray(nodeOutput?.images) ? nodeOutput.images : [];
        for (const imageRef of images) {
            const normalized = normalizeComfyOutputRef(imageRef);
            if (normalized) {
                results.push(normalized);
            }
        }
    }

    return results;
}
