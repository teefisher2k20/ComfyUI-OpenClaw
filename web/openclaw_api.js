/**
 * OpenClaw API Wrapper (R7)
 * Provides consistent fetch usage, timeout handling, and type-safe response shapes.
 */
import { OpenClawSession } from "./openclaw_session.js";
import { fetchApi, apiURL, fileURL } from "./openclaw_comfy_api.js";
import { API_PREFIXES, buildAdminTokenHeaders, getApiPathCandidates } from "./openclaw_compat.js";
import { isAbortError, linkAbortSignal, parseJsonSafe } from "./openclaw_utils.js";
import { normalizeComfyOutputRef } from "./openclaw_asset_refs.js";
import {
    composeFetchWrappersOnce,
    withAbortPassthrough,
    withGetRetry,
    withPreconnectHint,
} from "./openclaw_fetch_wrappers.js";

export class OpenClawAPI {
    constructor() {
        this._capabilitiesCache = null;
        this._capabilitiesCacheTs = 0;

        // R96: Compose fetch wrappers exactly once per fetch instance to avoid
        // duplicate retry/preconnect/abort decoration on repeated bootstrap.
        this._decoratedFetchApi = composeFetchWrappersOnce(fetchApi, [
            withAbortPassthrough(),
            withPreconnectHint(),
            withGetRetry({ retries: 1 }),
        ]);
        this._decoratedNativeFetch = composeFetchWrappersOnce(fetch.bind(window), [
            withAbortPassthrough(),
            withPreconnectHint(),
            withGetRetry({ retries: 1 }),
        ]);
    }

    /**
     * Gets the admin token from session storage (if available).
     */
    _getAdminToken() {
        return OpenClawSession.getAdminToken() || "";
    }

    _path(suffix) {
        return `${API_PREFIXES.canonical}${suffix}`;
    }

    _candidatePaths(url) {
        return getApiPathCandidates(url);
    }

    async _fetchWithCandidates(url, options = {}) {
        let response = null;
        const candidates = this._candidatePaths(url);

        for (const candidate of candidates) {
            response = await this._decoratedFetchApi(candidate, options);
            if (response.status !== 404) break;
        }

        if (response && response.status === 404 && typeof url === "string") {
            for (const candidate of candidates) {
                try {
                    response = await this._decoratedNativeFetch(fileURL(candidate), options);
                    if (response.status !== 404) break;
                } catch {
                    // ignore and continue fallback probes
                }
            }
        }
        return response;
    }

    _adminTokenHeaders(token) {
        return buildAdminTokenHeaders(token || this._getAdminToken());
    }

    /**
     * Generic fetch wrapper with timeout and error normalization.
     * @param {string} url - The URL to fetch
     * @param {object} options - Fetch options
     * @param {number} options.timeout - Timeout in ms (default: 10000)
     * @param {AbortSignal} options.signal - Optional AbortSignal from caller (R38-Lite)
     */
    async fetch(url, options = {}) {
        const { timeout = 10000, signal: externalSignal, ...fetchOptions } = options;

        // R38-Lite: Support both internal timeout and external abort signal
        const controller = new AbortController();
        let timedOut = false;
        let cancelledByCaller = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, timeout);

        // R55: Shared abort linkage helper (consistent cancel semantics)
        const detachExternalAbort = linkAbortSignal(
            externalSignal,
            controller,
            () => {
                cancelledByCaller = true;
            }
        );

        try {
            // R26: Use ComfyUI shim (fetchApi) which handles base path automatically
            const response = await this._fetchWithCandidates(url, {
                ...fetchOptions,
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            // Best-effort body parsing
            let data = null;
            const contentType = response?.headers?.get("content-type");
            let responseText = null;
            try {
                responseText = await response.text();
            } catch (e) { }

            if (contentType && contentType.includes("application/json") && typeof responseText === "string") {
                data = parseJsonSafe(responseText, null).value;
            } else {
                data = responseText;
            }

            if (!response || !response.ok) {
                // Return normalized error shape
                return {
                    ok: false,
                    status: response ? response.status : 0,
                    error: (data && data.error) || (response ? response.statusText : "request_failed") || "request_failed",
                    data,
                };
            }

            return {
                ok: true,
                status: response.status,
                data,
            };

        } catch (err) {
            clearTimeout(timeoutId);
            // Network or Timeout/Abort errors
            const isAbort = isAbortError(err);
            const abortKind = cancelledByCaller ? "cancelled" : (timedOut ? "timeout" : "cancelled");
            return {
                ok: false,
                status: 0,
                error: isAbort ? abortKind : "network_error",
                detail: err?.message,
            };
        } finally {
            detachExternalAbort();
        }
    }

    // --- Endpoints ---

    async getHealth() {
        return this.fetch(this._path("/health"));
    }

    async getLogs(lines = 200) {
        return this.fetch(`${this._path("/logs/tail")}?lines=${lines}`);
    }

    async validateWebhook(payload) {
        return this.fetch(this._path("/webhook"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    async submitWebhook(payload) {
        return this.fetch(this._path("/webhook/submit"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    // R19: Capabilities
    async getCapabilities() {
        const now = Date.now();
        if (this._capabilitiesCache && (now - this._capabilitiesCacheTs) < 5000) {
            return this._capabilitiesCache;
        }
        const res = await this.fetch(this._path("/capabilities"));
        if (res?.ok) {
            this._capabilitiesCache = res;
            this._capabilitiesCacheTs = now;
        }
        return res;
    }

    async supportsAssistStreaming() {
        const caps = await this.getCapabilities();
        return !!caps?.ok && !!caps?.data?.features?.assist_streaming;
    }

    // F17: ComfyUI History
    async getHistory(promptId) {
        // /history is a ComfyUI native endpoint.
        // ComfyUI's shim handles it if we pass "/history/..."?
        // Wait, ComfyUI endpoints are usually /history.
        // fetchApi('/history/...') maps to /api/history/...
        // ComfyUI backend registers /history?
        // Checking ComfyUI source: yes, app.routes.get("/history"...)
        // But usually under /api ?
        // Actually ComfyUI 'fetchApi' prefixes with '/api'.
        // Does 'history' live under '/api/history'? Yes.
        const res = await this.fetch(`/history/${promptId}`);
        if (!res.ok) return res;

        // ComfyUI returns: { "<prompt_id>": { ...historyItem... } }
        const data = res.data;
        const historyItem = (data && typeof data === "object") ? data[promptId] : null;
        return { ...res, data: historyItem };
    }

    // R25: Trace timeline (optional)
    async getTrace(promptId) {
        return this.fetch(`${this._path("/trace")}/${encodeURIComponent(promptId)}`);
    }

    // Helper: Build ComfyUI /view URL
    buildViewUrl(filename, subfolder = "", type = "output") {
        const params = new URLSearchParams({ filename, type });
        if (subfolder) params.set("subfolder", subfolder);
        // apiURL returns the full path including standard base
        return apiURL(`/view?${params.toString()}`);
    }

    buildViewUrlForRef(imageRef) {
        const normalized = normalizeComfyOutputRef(imageRef);
        if (!normalized) {
            return "";
        }
        return apiURL(`/view?${new URLSearchParams(normalized.viewParams).toString()}`);
    }

    // R21/F20: Get config
    async getConfig() {
        return this.fetch(this._path("/config"));
    }

    // R21/S13/F20: Update config (requires admin token)
    async putConfig(config, adminToken) {
        return this.fetch(this._path("/config"), {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(adminToken),
            },
            body: JSON.stringify(config),
        });
    }

    // F20: Test LLM connection (uses effective config, no api_key in frontend)
    async runLLMTest() {
        return this.fetch(this._path("/llm/test"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify({}), // Empty body = use effective config
            timeout: 30000,
        });
    }

    // Backwards compatibility alias for settings_tab.js
    async testLLM(adminToken, overrides = null) {
        return this.fetch(this._path("/llm/test"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(adminToken),
            },
            // IMPORTANT: Settings UI uses this to test the currently selected provider/model
            // without requiring a config "Save" first. Backend accepts an empty body too.
            body: JSON.stringify(overrides || {}),
            timeout: 30000,
        });
    }

    // F20+: Fetch remote model list (best-effort; admin boundary)
    async getModelList(providerId, adminToken) {
        const q = providerId ? `?provider=${encodeURIComponent(providerId)}` : "";
        return this.fetch(`${this._path("/llm/models")}${q}`, {
            method: "GET",
            headers: {
                ...this._adminTokenHeaders(adminToken),
            },
            timeout: 30000,
        });
    }

    // --- S25: Secrets Management (Admin-gated) ---

    /**
     * Get secrets status (NO VALUES).
     * Admin boundary (token if configured; otherwise loopback-only).
     */
    async getSecretsStatus(adminToken) {
        return this.fetch(this._path("/secrets/status"), {
            method: "GET",
            headers: {
                ...this._adminTokenHeaders(adminToken),
            },
        });
    }

    /**
     * Save API key to server store.
     * Admin boundary (token if configured; otherwise loopback-only).
     *
     * @param {string} provider - Provider ID ("openai", "anthropic", "generic")
     * @param {string} apiKey - API key value (NEVER logged)
     * @param {string} adminToken - Admin token
     */
    async saveSecret(provider, apiKey, adminToken) {
        return this.fetch(this._path("/secrets"), {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(adminToken),
            },
            body: JSON.stringify({
                provider: provider,
                api_key: apiKey,
            }),
        });
    }

    /**
     * Clear provider secret.
     * Admin boundary (token if configured; otherwise loopback-only).
     */
    async clearSecret(provider, adminToken) {
        return this.fetch(this._path(`/secrets/${encodeURIComponent(provider)}`), {
            method: "DELETE",
            headers: {
                ...this._adminTokenHeaders(adminToken),
            },
        });
    }

    // --- Assist Endpoints (F8/F21) ---

    _parseSSEChunk(rawChunk) {
        const lines = rawChunk.split(/\r?\n/);
        let event = "message";
        const dataLines = [];
        for (const line of lines) {
            if (!line) continue;
            if (line.startsWith("event:")) {
                event = line.slice(6).trim() || "message";
            } else if (line.startsWith("data:")) {
                dataLines.push(line.slice(5).trim());
            }
        }
        if (!dataLines.length) return null;
        const joined = dataLines.join("\n");
        let data = null;
        try {
            data = JSON.parse(joined);
        } catch {
            data = { raw: joined };
        }
        return { event, data };
    }

    async streamSSEPost(url, payload, { signal = null, timeout = 60000, onEvent = null } = {}) {
        const controller = new AbortController();
        let timedOut = false;
        let cancelledByCaller = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, timeout);

        if (signal) {
            if (signal.aborted) {
                cancelledByCaller = true;
                controller.abort();
            } else {
                signal.addEventListener("abort", () => {
                    cancelledByCaller = true;
                    controller.abort();
                }, { once: true });
            }
        }

        try {
            const response = await this._fetchWithCandidates(url, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    ...this._adminTokenHeaders(),
                },
                body: JSON.stringify(payload),
                signal: controller.signal,
            });

            if (!response || !response.ok) {
                clearTimeout(timeoutId);
                let data = null;
                try {
                    data = await response?.json?.();
                } catch {
                    try { data = await response?.text?.(); } catch { }
                }
                return {
                    ok: false,
                    status: response ? response.status : 0,
                    error: (data && data.error) || response?.statusText || "request_failed",
                    data,
                };
            }

            const finalEnvelope = { value: null };
            const dispatchEvent = (evt) => {
                if (!evt) return;
                if (evt.event === "final") {
                    finalEnvelope.value = evt.data;
                }
                if (typeof onEvent === "function") onEvent(evt);
            };

            if (!response.body || typeof response.body.getReader !== "function") {
                const text = await response.text();
                const chunks = text.split(/\r?\n\r?\n/);
                for (const chunk of chunks) dispatchEvent(this._parseSSEChunk(chunk));
            } else {
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = "";
                const findBoundary = (text) => {
                    const idxCRLF = text.indexOf("\r\n\r\n");
                    const idxLF = text.indexOf("\n\n");
                    if (idxCRLF === -1) return { index: idxLF, len: 2 };
                    if (idxLF === -1) return { index: idxCRLF, len: 4 };
                    return idxCRLF < idxLF ? { index: idxCRLF, len: 4 } : { index: idxLF, len: 2 };
                };
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    let boundary = findBoundary(buffer);
                    while (boundary.index >= 0) {
                        const rawChunk = buffer.slice(0, boundary.index);
                        buffer = buffer.slice(boundary.index + boundary.len);
                        dispatchEvent(this._parseSSEChunk(rawChunk));
                        boundary = findBoundary(buffer);
                    }
                }
                buffer += decoder.decode();
                if (buffer.trim()) {
                    dispatchEvent(this._parseSSEChunk(buffer));
                }
            }

            clearTimeout(timeoutId);
            if (finalEnvelope.value?.ok) {
                return {
                    ok: true,
                    status: 200,
                    data: finalEnvelope.value.result,
                    stream: finalEnvelope.value.streaming || {},
                    envelope: finalEnvelope.value,
                };
            }
            if (finalEnvelope.value && finalEnvelope.value.ok === false) {
                return {
                    ok: false,
                    status: 500,
                    error: finalEnvelope.value.error || "stream_failed",
                    data: finalEnvelope.value,
                };
            }
            return { ok: false, status: 0, error: "stream_incomplete" };
        } catch (err) {
            clearTimeout(timeoutId);
            const isAbort = err?.name === "AbortError";
            const abortKind = cancelledByCaller ? "cancelled" : (timedOut ? "timeout" : "cancelled");
            return {
                ok: false,
                status: 0,
                error: isAbort ? abortKind : "network_error",
                detail: err?.message,
            };
        }
    }

    /**
     * Run Prompt Planner.
     * @param {object} params - { profile, requirements, style_directives, seed }
     * @param {AbortSignal} signal - Optional AbortSignal for cancellation (R38-Lite)
     */
    async runPlanner(params, signal = null) {
        return this.fetch(this._path("/assist/planner"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(params),
            timeout: 60000, // LLM calls may be slow
            signal, // R38-Lite: Pass signal
        });
    }

    async listPlannerProfiles(signal = null) {
        return this.fetch(this._path("/assist/planner/profiles"), {
            headers: {
                ...this._adminTokenHeaders(),
            },
            signal,
        });
    }

    async runPlannerStream(params, { signal = null, onEvent = null } = {}) {
        return this.streamSSEPost(this._path("/assist/planner/stream"), params, {
            signal,
            timeout: 60000,
            onEvent,
        });
    }

    /**
     * Run Prompt Refiner.
     * @param {object} params - { image_b64, orig_positive, orig_negative, issue, params_json, goal }
     * @param {AbortSignal} signal - Optional AbortSignal for cancellation (R38-Lite)
     */
    async runRefiner(params, signal = null) {
        return this.fetch(this._path("/assist/refiner"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(params),
            timeout: 60000,
            signal, // R38-Lite: Pass signal
        });
    }

    async runRefinerStream(params, { signal = null, onEvent = null } = {}) {
        return this.streamSSEPost(this._path("/assist/refiner/stream"), params, {
            signal,
            timeout: 60000,
            onEvent,
        });
    }

    // --- F22: Presets ---

    async listPresets(category) {
        const query = category ? `?category=${encodeURIComponent(category)}` : "";
        return this.fetch(`${this._path("/presets")}${query}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async getPreset(id) {
        return this.fetch(`${this._path("/presets")}/${encodeURIComponent(id)}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async createPreset(data) {
        return this.fetch(this._path("/presets"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(data),
        });
    }

    async updatePreset(id, data) {
        return this.fetch(`${this._path("/presets")}/${encodeURIComponent(id)}`, {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(data),
        });
    }

    async deletePreset(id) {
        return this.fetch(`${this._path("/presets")}/${encodeURIComponent(id)}`, {
            method: "DELETE",
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }
    // --- S7: Approval Gates ---

    async getApprovals({ status, limit = 100, offset = 0 } = {}) {
        const params = new URLSearchParams({ limit, offset });
        if (status) params.set("status", status);

        return this.fetch(`${this._path("/approvals")}?${params.toString()}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async getApproval(id) {
        return this.fetch(`${this._path("/approvals")}/${encodeURIComponent(id)}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async approveRequest(id, { actor = "web_user", autoExecute = true } = {}) {
        return this.fetch(`${this._path("/approvals")}/${encodeURIComponent(id)}/approve`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify({ actor, auto_execute: autoExecute }),
        });
    }

    async rejectRequest(id, { actor = "web_user" } = {}) {
        return this.fetch(`${this._path("/approvals")}/${encodeURIComponent(id)}/reject`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify({ actor }),
        });
    }

    // --- S8/F11: Asset Packs ---

    async getPacks() {
        return this.fetch(this._path("/packs"), {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async importPack(file, overwrite = false) {
        const formData = new FormData();
        formData.append("file", file);

        const query = overwrite ? "?overwrite=true" : "";

        return this.fetch(`${this._path("/packs/import")}${query}`, {
            method: "POST",
            headers: {
                ...this._adminTokenHeaders(),
                // Let browser set Content-Type for FormData
            },
            body: formData,
        });
    }

    async exportPack(name, version) {
        // Return URL for download (or blob fetch if needed)
        // Since it requires a token, we might need to fetch blob
        // But for simplicity, we can use a token parameter if supported, or fetch blob and create object URL.

        // Fetch as blob
        // R26: Use fetchApi to ensure base path
        const primaryPath = `${this._path("/packs/export")}/${encodeURIComponent(name)}/${encodeURIComponent(version)}`;
        const legacyPath = getApiPathCandidates(primaryPath)[1];

        const headers = this._adminTokenHeaders();

        let res = await fetchApi(primaryPath, { headers });
        if (res.status === 404) res = await fetchApi(legacyPath, { headers });

        if (res.status === 404) {
            try {
                res = await fetch(fileURL(primaryPath), { headers });
            } catch { }
        }
        if (res.status === 404) {
            try {
                res = await fetch(fileURL(legacyPath), { headers });
            } catch { }
        }

        if (res.ok) {
            const blob = await res.blob();
            return { ok: true, data: blob };
        }

        // If error, try to parse json error
        let error = "Download failed";
        try {
            const json = await res.json();
            error = json.error || error;
        } catch (e) { }

        return { ok: false, error };
    }

    async deletePack(name, version) {
        return this.fetch(`${this._path("/packs")}/${encodeURIComponent(name)}/${encodeURIComponent(version)}`, {
            method: "DELETE",
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    // --- R42/F28: Preflight & Explorer ---

    async runPreflight(workflow) {
        return this.fetch(this._path("/preflight"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(workflow),
        });
    }

    async getInventory() {
        return this.fetch(this._path("/preflight/inventory"), {
            method: "GET",
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    // --- R47: Checkpoints ---

    async listCheckpoints() {
        return this.fetch(this._path("/checkpoints"), {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async createCheckpoint(name, workflow, description = "") {
        return this.fetch(this._path("/checkpoints"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders()
            },
            body: JSON.stringify({ name, workflow, description })
        });
    }

    async getCheckpoint(id) {
        return this.fetch(`${this._path("/checkpoints")}/${encodeURIComponent(id)}`, {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async deleteCheckpoint(id) {
        return this.fetch(`${this._path("/checkpoints")}/${encodeURIComponent(id)}`, {
            method: "DELETE",
            headers: { ...this._adminTokenHeaders() }
        });
    }

    // --- F54: Model Search / Download / Import ---

    async searchModels(params = {}) {
        const qs = new URLSearchParams();
        if (params.q) qs.set("q", String(params.q));
        if (params.source) qs.set("source", String(params.source));
        if (params.model_type) qs.set("model_type", String(params.model_type));
        if (typeof params.installed === "boolean") qs.set("installed", params.installed ? "true" : "false");
        if (params.limit != null) qs.set("limit", String(params.limit));
        if (params.offset != null) qs.set("offset", String(params.offset));
        const suffix = qs.toString() ? `?${qs.toString()}` : "";
        return this.fetch(`${this._path("/models/search")}${suffix}`, {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async createModelDownloadTask(payload) {
        return this.fetch(this._path("/models/downloads"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders()
            },
            body: JSON.stringify(payload || {})
        });
    }

    async listModelDownloadTasks(params = {}) {
        const qs = new URLSearchParams();
        if (params.state) qs.set("state", String(params.state));
        if (params.limit != null) qs.set("limit", String(params.limit));
        if (params.offset != null) qs.set("offset", String(params.offset));
        if (params.since_seq != null) qs.set("since_seq", String(params.since_seq));
        const suffix = qs.toString() ? `?${qs.toString()}` : "";
        return this.fetch(`${this._path("/models/downloads")}${suffix}`, {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async getModelDownloadTask(taskId) {
        return this.fetch(`${this._path("/models/downloads")}/${encodeURIComponent(taskId)}`, {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async cancelModelDownloadTask(taskId) {
        return this.fetch(`${this._path("/models/downloads")}/${encodeURIComponent(taskId)}/cancel`, {
            method: "POST",
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async importDownloadedModel(payload) {
        return this.fetch(this._path("/models/import"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders()
            },
            body: JSON.stringify(payload || {})
        });
    }

    async listModelInstallations(params = {}) {
        const qs = new URLSearchParams();
        if (params.model_type) qs.set("model_type", String(params.model_type));
        if (params.limit != null) qs.set("limit", String(params.limit));
        if (params.offset != null) qs.set("offset", String(params.offset));
        const suffix = qs.toString() ? `?${qs.toString()}` : "";
        return this.fetch(`${this._path("/models/installations")}${suffix}`, {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async parsePngInfo(imageB64) {
        return this.fetch(this._path("/pnginfo"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders()
            },
            body: JSON.stringify({
                image_b64: String(imageB64 || "")
            }),
            timeout: 30000,
        });
    }

    // --- R71: Job Events ---

    /**
     * Poll for recent events (fallback).
     * @param {number} lastSeq - Sequence ID to start from
     */
    async getEvents(lastSeq = 0) {
        return this.fetch(`${this._path("/events")}?since=${lastSeq}`);
    }

    /**
     * Subscribe to SSE event stream.
     * @param {function} onEvent - Callback for events (eventData) => void
     * @param {function} onError - Callback for errors (error) => void
     * @returns {EventSource} The event source instance (caller must .close() it)
     */
    subscribeEvents(onEvent, onError) {
        // Use apiURL from shim to get full path
        const url = apiURL(this._path("/events/stream"));
        const es = new EventSource(url);

        const handle = (e) => {
            if (!e.data) return;
            const parsed = parseJsonSafe(e.data);
            if (!parsed.ok || !parsed.value || typeof parsed.value !== "object") {
                console.warn("[OpenClaw] Failed to parse SSE event:", parsed.error);
                return;
            }
            const data = parsed.value;
            // Unified event type injection if missing
            if (!data.event_type && e.type !== "message") {
                data.event_type = e.type;
            }
            onEvent(data);
        };

        es.onmessage = handle;
        es.addEventListener("queued", handle);
        es.addEventListener("running", handle);
        es.addEventListener("completed", handle);
        es.addEventListener("failed", handle);

        es.onerror = (err) => {
            if (onError) onError(err);
        };

        return es;
    }
}

export const openclawApi = new OpenClawAPI();
