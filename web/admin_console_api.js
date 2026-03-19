import {
    API_PREFIXES,
    STORAGE_KEYS,
    buildRemoteAdminHeaders,
    getMirroredStorageValue,
    setMirroredStorageValue,
} from "./openclaw_compat.js";

function parseResponseText(text) {
    try {
        return text ? JSON.parse(text) : null;
    } catch {
        return { raw: text };
    }
}

export function parseSseChunk(chunk) {
    const lines = String(chunk || "").split(/\r?\n/);
    let type = "message";
    const dataLines = [];
    lines.forEach((line) => {
        if (line.startsWith("event:")) {
            type = line.slice(6).trim() || "message";
        } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
        }
    });
    if (!dataLines.length) return null;

    const raw = dataLines.join("\n");
    const payload = parseResponseText(raw) || { raw };
    payload.event_type = payload.event_type || type;
    return payload;
}

export function createRemoteAdminApi({
    fetchImpl = fetch.bind(globalThis),
    storage = globalThis.localStorage,
    tokenSpec = STORAGE_KEYS.local.remoteAdminToken,
    prefixes = API_PREFIXES,
    headerBuilder = buildRemoteAdminHeaders,
} = {}) {
    const state = {
        canonicalPrefix: prefixes.canonical,
        legacyPrefix: prefixes.legacy,
        token: getMirroredStorageValue(storage, tokenSpec) || "",
        lastSeq: 0,
        sseAbort: null,
    };

    const getHeaders = () => headerBuilder(state.token || "");

    async function request(path, options = {}) {
        const urls = [`${state.canonicalPrefix}${path}`, `${state.legacyPrefix}${path}`];
        let last = { ok: false, status: 0, error: "request_failed" };
        for (const url of urls) {
            try {
                const response = await fetchImpl(url, {
                    method: options.method || "GET",
                    headers: {
                        ...getHeaders(),
                        ...(options.body ? { "Content-Type": "application/json" } : {}),
                        ...(options.headers || {}),
                    },
                    body: options.body ? JSON.stringify(options.body) : undefined,
                    signal: options.signal,
                });
                const text = await response.text();
                const data = parseResponseText(text);
                if (response.status === 404) {
                    last = { ok: false, status: 404, error: "not_found", data };
                    continue;
                }
                return {
                    ok: response.ok,
                    status: response.status,
                    error: (data && data.error) || (!response.ok ? response.statusText : ""),
                    data,
                    url,
                };
            } catch (error) {
                last = { ok: false, status: 0, error: String(error) };
            }
        }
        return last;
    }

    async function openStream(path, options = {}) {
        const urls = [`${state.canonicalPrefix}${path}`, `${state.legacyPrefix}${path}`];
        for (const url of urls) {
            try {
                const response = await fetchImpl(url, {
                    method: options.method || "GET",
                    headers: {
                        ...getHeaders(),
                        ...(options.headers || {}),
                    },
                    signal: options.signal,
                });
                if (response.status === 404) {
                    continue;
                }
                return response;
            } catch {
                // try next compatible path
            }
        }
        return null;
    }

    return {
        state,
        request,
        openStream,
        getHeaders,
        getToken() {
            return state.token;
        },
        setToken(token) {
            state.token = String(token || "").trim();
            setMirroredStorageValue(storage, tokenSpec, state.token);
            return state.token;
        },
        clearToken() {
            state.token = "";
            setMirroredStorageValue(storage, tokenSpec, "");
        },
    };
}
