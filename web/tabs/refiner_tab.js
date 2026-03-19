import { openclawApi } from "../openclaw_api.js";
import { showError, clearError, showToast, createRequestLifecycleController } from "../openclaw_utils.js";

export const RefinerTab = {
    id: "refiner",
    title: "Refiner",
    icon: "pi pi-sliders-h",

    render(container) {
        container.innerHTML = `
            <div class="openclaw-panel">
                <div class="openclaw-scroll-area">
                    <div class="openclaw-card">
                         <div class="openclaw-section-header">Source Context</div>

                        <div class="openclaw-error-box" style="display:none"></div>

                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Source Image</label>
                            <div style="display:flex; gap:10px; align-items:center;">
                                <input type="file" id="refiner-img-upload" class="openclaw-input" accept="image/png, image/jpeg">
                                <img id="refiner-img-preview" style="height:40px; border-radius:4px; display:none; border:1px solid #444;">
                            </div>
                        </div>

                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Original Positive</label>
                            <textarea id="refiner-orig-pos" class="openclaw-textarea"></textarea>
                        </div>

                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Original Negative</label>
                            <textarea id="refiner-orig-neg" class="openclaw-textarea" rows="2"></textarea>
                        </div>
                    </div>

                    <div class="openclaw-card">
                        <div class="openclaw-section-header">Goal / Issue</div>
                        <div class="openclaw-input-group">
                            <textarea id="refiner-issue" class="openclaw-textarea openclaw-textarea-sm" placeholder="What's wrong? or What to change?"></textarea>
                        </div>

                        <!-- R38-Lite: Loading state container -->
                        <div id="refiner-loading" style="display:none; margin: 12px 0; padding: 12px; background: var(--input-background); border-radius: 6px;">
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <div class="spinner-border" style="width: 20px; height: 20px; border: 2px solid; border-color: var(--primary-color) transparent transparent transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                                <div>
                                    <div id="refiner-stage" style="font-weight: 600; margin-bottom: 4px;">Preparing request...</div>
                                    <div id="refiner-elapsed" style="font-size: 0.9em; opacity: 0.7;">Elapsed: 0s</div>
                                </div>
                            </div>
                            <div style="margin-top:8px;">
                                <div style="font-size:0.85em; opacity:0.8; margin-bottom:4px;">Live Preview (best effort)</div>
                                <textarea id="refiner-stream-preview" class="openclaw-textarea" style="min-height:70px;" readonly></textarea>
                            </div>
                            <button id="refiner-cancel-btn" class="openclaw-btn" style="margin-top: 8px; width: 100%; background: var(--input-background); border: 1px solid var(--border-color);">Cancel</button>
                        </div>

                        <button id="refiner-run-btn" class="openclaw-btn openclaw-btn-primary">Refine Prompts</button>
                    </div>


                    <div id="refiner-results" style="display:none;" class="openclaw-split-v">
                        <div class="openclaw-card">
                            <div class="openclaw-section-header">Refinement</div>
                            <div class="openclaw-input-group">
                                <label class="openclaw-label">Rationale</label>
                                <div id="refiner-rationale" class="openclaw-markdown-box"></div>
                            </div>
                            <div class="openclaw-input-group">
                                <label class="openclaw-label">New Positive</label>
                                <textarea id="refiner-new-pos" class="openclaw-textarea openclaw-textarea-md"></textarea>
                            </div>
                             <div class="openclaw-input-group">
                                <label class="openclaw-label">New Negative</label>
                                <textarea id="refiner-new-neg" class="openclaw-textarea" rows="2"></textarea>
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

        // Image preview logic
        const imgInput = container.querySelector("#refiner-img-upload");
        const imgPreview = container.querySelector("#refiner-img-preview");
        let currentImgB64 = null;

        imgInput.onchange = async () => {
            const file = imgInput.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    currentImgB64 = e.target.result; // data:image/...
                    imgPreview.src = currentImgB64;
                    imgPreview.style.display = "block";
                };
                reader.readAsDataURL(file);
            }
        };

        const lifecycle = createRequestLifecycleController(container, {
            loading: "#refiner-loading",
            runButton: "#refiner-run-btn",
            stage: "#refiner-stage",
            elapsed: "#refiner-elapsed",
        });
        let activeRequestId = 0;

        container.querySelector("#refiner-run-btn").onclick = async () => {
            clearError(container);
            const resDiv = container.querySelector("#refiner-results");
            const previewEl = container.querySelector("#refiner-stream-preview");
            resDiv.style.display = "none";
            if (previewEl) previewEl.value = "";

            const requestId = ++activeRequestId;
            const signal = lifecycle.begin("Preparing request...");

            try {
                lifecycle.setStage("Sending request to backend...");
                await new Promise((resolve) => requestAnimationFrame(resolve));
                lifecycle.setStage("Waiting for provider response...");

                const payload = {
                    image_b64: currentImgB64,
                    orig_positive: container.querySelector("#refiner-orig-pos").value,
                    orig_negative: container.querySelector("#refiner-orig-neg").value,
                    issue: container.querySelector("#refiner-issue").value
                };

                let res;
                const streamingSupported = await openclawApi.supportsAssistStreaming();
                if (streamingSupported) {
                    res = await openclawApi.runRefinerStream(payload, {
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
                        lifecycle.setStage("Streaming unavailable, falling back...");
                        res = await openclawApi.runRefiner(payload, signal);
                    }
                } else {
                    res = await openclawApi.runRefiner(payload, signal);
                }

                if (requestId !== activeRequestId) {
                    return;
                }

                if (res.ok) {
                    lifecycle.setStage("Parsing and validating output...");
                    await new Promise((resolve) => requestAnimationFrame(resolve));
                    container.querySelector("#refiner-new-pos").value = res.data.refined_positive || "";
                    container.querySelector("#refiner-new-neg").value = res.data.refined_negative || "";
                    container.querySelector("#refiner-rationale").textContent = res.data.rationale || "";
                    resDiv.style.display = "flex";
                } else if (res.error === "timeout") {
                    showError(container, "Request timed out");
                } else if (res.error === "cancelled") {
                    showToast("Request cancelled by user", "info");
                } else {
                    showError(container, res.error || "Refinement failed");
                }

            } catch (e) {
                if (requestId !== activeRequestId) {
                    return;
                }
                showError(container, `Refine Failed: ${e.message}`);
            } finally {
                if (requestId === activeRequestId) {
                    lifecycle.end();
                }
            }
        };

        // R38-Lite: Cancel button handler
        container.querySelector("#refiner-cancel-btn").onclick = () => {
            if (lifecycle.cancel()) {
                // Invalidate pending promise handlers so stale responses cannot mutate UI.
                activeRequestId += 1;
                showToast("Request cancelled by user", "info");
            }
        };
    }
};
