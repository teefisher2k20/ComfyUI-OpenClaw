import { openclawApi } from "../openclaw_api.js";
import { showError, clearError, showToast, createRequestLifecycleController } from "../openclaw_utils.js";

export const PlannerTab = {
    id: "planner",
    title: "Planner",
    icon: "pi pi-pencil",

    render(container) {
        container.innerHTML = `
            <div class="openclaw-panel">
                <div class="openclaw-scroll-area">
                    <div class="openclaw-card">
                        <div class="openclaw-section-header">Generation Goal</div>

                        <div class="openclaw-error-box" style="display:none" id="planner-error"></div>

                        <div class="openclaw-grid-2">
                             <div class="openclaw-input-group">
                                <label class="openclaw-label">Profile</label>
                                <select id="planner-profile" class="openclaw-select">
                                    <option value="SDXL-v1">SDXL v1</option>
                                    <option value="Flux-Dev">Flux Dev</option>
                                </select>
                            </div>
                            <div class="openclaw-input-group">
                                <label class="openclaw-label">Style / Directives</label>
                                <input type="text" id="planner-style" class="openclaw-input" placeholder="e.g. Cyberpunk, 8k...">
                            </div>
                        </div>

                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Requirements</label>
                            <textarea id="planner-reqs" class="openclaw-textarea openclaw-textarea-sm" placeholder="Describe the image..."></textarea>
                        </div>

                        <!-- R38-Lite: Loading state container -->
                        <div id="planner-loading" style="display:none; margin: 12px 0; padding: 12px; background: var(--input-background); border-radius: 6px;">
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <div class="spinner-border" style="width: 20px; height: 20px; border: 2px solid; border-color: var(--primary-color) transparent transparent transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                                <div>
                                    <div id="planner-stage" style="font-weight: 600; margin-bottom: 4px;">Preparing request...</div>
                                    <div id="planner-elapsed" style="font-size: 0.9em; opacity: 0.7;">Elapsed: 0s</div>
                                </div>
                            </div>
                            <div style="margin-top:8px;">
                                <div style="font-size:0.85em; opacity:0.8; margin-bottom:4px;">Live Preview (best effort)</div>
                                <textarea id="planner-stream-preview" class="openclaw-textarea" style="min-height:70px;" readonly></textarea>
                            </div>
                            <button id="planner-cancel-btn" class="openclaw-btn" style="margin-top: 8px; width: 100%; background: var(--input-background); border: 1px solid var(--border-color);">Cancel</button>
                        </div>

                        <button id="planner-run-btn" class="openclaw-btn openclaw-btn-primary">Plan Generation</button>
                    </div>

                    <div id="planner-results" style="display:none;" class="openclaw-split-v">
                        <div class="openclaw-card">
                            <div class="openclaw-section-header">Plan Output</div>
                            <div class="openclaw-input-group">
                                <label class="openclaw-label">Positive</label>
                                <textarea id="planner-out-pos" class="openclaw-textarea openclaw-textarea-md" readonly></textarea>
                            </div>
                            <div class="openclaw-input-group">
                                <label class="openclaw-label">Negative</label>
                                <textarea id="planner-out-neg" class="openclaw-textarea" rows="2" readonly></textarea>
                            </div>
                            <div class="openclaw-input-group">
                                <label class="openclaw-label">Params (JSON)</label>
                                <textarea id="planner-out-params" class="openclaw-textarea openclaw-textarea-md" readonly></textarea>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <style>
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            </style>
        `;

        const profileSelect = container.querySelector("#planner-profile");
        const fallbackProfiles = [
            { id: "SDXL-v1", label: "SDXL v1" },
            { id: "Flux-Dev", label: "Flux Dev" },
        ];
        const applyProfiles = (profiles, defaultProfile = "SDXL-v1") => {
            const current = profileSelect.value;
            profileSelect.innerHTML = "";
            (profiles || []).forEach((profile) => {
                const option = document.createElement("option");
                option.value = profile.id;
                option.textContent = profile.label || profile.id;
                profileSelect.appendChild(option);
            });
            const desired = current && [...profileSelect.options].some((opt) => opt.value === current)
                ? current
                : defaultProfile;
            if (desired) {
                profileSelect.value = desired;
            }
        };
        const loadProfiles = async () => {
            try {
                const profileRes = await openclawApi.listPlannerProfiles();
                if (
                    profileRes.ok &&
                    Array.isArray(profileRes.data?.profiles) &&
                    profileRes.data.profiles.length > 0
                ) {
                    applyProfiles(
                        profileRes.data.profiles,
                        profileRes.data.default_profile || "SDXL-v1"
                    );
                } else {
                    applyProfiles(fallbackProfiles, "SDXL-v1");
                }
            } catch {
                applyProfiles(fallbackProfiles, "SDXL-v1");
            }
        };
        loadProfiles();

        const lifecycle = createRequestLifecycleController(container, {
            loading: "#planner-loading",
            runButton: "#planner-run-btn",
            stage: "#planner-stage",
            elapsed: "#planner-elapsed",
        });
        let activeRequestId = 0;

        container.querySelector("#planner-run-btn").onclick = async () => {
            const profile = container.querySelector("#planner-profile").value;
            const reqs = container.querySelector("#planner-reqs").value;
            const style = container.querySelector("#planner-style").value;

            const resDiv = container.querySelector("#planner-results");
            const previewEl = container.querySelector("#planner-stream-preview");

            clearError(container);
            resDiv.style.display = "none";
            if (previewEl) previewEl.value = "";

            const requestId = ++activeRequestId;
            const signal = lifecycle.begin("Preparing request...");

            try {
                lifecycle.setStage("Sending request to backend...");
                await new Promise((resolve) => requestAnimationFrame(resolve));
                lifecycle.setStage("Waiting for provider response...");

                const payload = {
                    profile,
                    requirements: reqs,
                    style_directives: style
                };

                let res;
                const streamingSupported = await openclawApi.supportsAssistStreaming();
                if (streamingSupported) {
                    res = await openclawApi.runPlannerStream(payload, {
                        signal,
                        onEvent: (evt) => {
                            if (requestId !== activeRequestId || !evt) return;
                            if (evt.event === "stage" && evt.data?.message) {
                                lifecycle.setStage(evt.data.message);
                            } else if (evt.event === "delta" && typeof evt.data?.text === "string" && previewEl) {
                                previewEl.value += evt.data.text;
                                previewEl.scrollTop = previewEl.scrollHeight;
                            }
                        }
                    });
                    if (!res.ok && !["cancelled", "timeout"].includes(res.error || "")) {
                        // Fallback to classic path if streaming transport/path degrades.
                        lifecycle.setStage("Streaming unavailable, falling back...");
                        res = await openclawApi.runPlanner(payload, signal);
                    }
                } else {
                    res = await openclawApi.runPlanner(payload, signal);
                }

                if (requestId !== activeRequestId) {
                    return;
                }

                if (res.ok) {
                    lifecycle.setStage("Parsing and validating output...");
                    await new Promise((resolve) => requestAnimationFrame(resolve));
                    resDiv.style.display = "flex"; // Re-enable flex layout
                    container.querySelector("#planner-out-pos").value = res.data.positive || "";
                    container.querySelector("#planner-out-neg").value = res.data.negative || "";
                    container.querySelector("#planner-out-params").value = JSON.stringify(res.data.params || {}, null, 2);
                } else if (res.error === "timeout") {
                    showError(container, "Request timed out");
                } else if (res.error === "cancelled") {
                    showToast("Request cancelled by user", "info");
                } else {
                    showError(container, res.error || "Planning failed");
                }
            } catch (err) {
                if (requestId !== activeRequestId) {
                    return;
                }
                showError(container, err.message || "Unexpected error");
            } finally {
                if (requestId === activeRequestId) {
                    lifecycle.end();
                }
            }
        };

        // R38-Lite: Cancel button handler
        container.querySelector("#planner-cancel-btn").onclick = () => {
            if (lifecycle.cancel()) {
                // Invalidate pending promise handlers so stale responses cannot mutate UI.
                activeRequestId += 1;
                showToast("Request cancelled by user", "info");
            }
        };
    }
};
