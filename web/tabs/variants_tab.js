import { showError, clearError, parseJsonOrThrow } from "../openclaw_utils.js";

export const VariantsTab = {
    id: "variants",
    title: "Variants",
    icon: "pi pi-copy",

    render(container) {
        container.innerHTML = `
            <div class="openclaw-panel">
                <div class="openclaw-scroll-area">
                    <div class="openclaw-card">
                        <div class="openclaw-section-header">Variants Configuration</div>

                        <div class="openclaw-error-box" style="display:none"></div>

                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Base Parameters (JSON)</label>
                            <textarea id="var-base-params" class="openclaw-textarea openclaw-textarea-md">{"width": 1024, "height": 1024, "seed": 0}</textarea>
                        </div>

                        <div class="openclaw-grid-2">
                             <div class="openclaw-input-group">
                                <label class="openclaw-label">Strategy</label>
                                <select id="var-strategy" class="openclaw-select">
                                    <option value="seeds">Seed Sweep (Count)</option>
                                    <option value="cfg">CFG Scale (Range)</option>
                                </select>
                            </div>

                            <!-- Dynamic inputs based on strategy -->
                            <div id="var-opts-seeds" class="var-opts openclaw-input-group">
                                <label class="openclaw-label">Count</label>
                                <input type="number" id="var-seed-count" class="openclaw-input" value="4" min="1" max="100">
                            </div>
                        </div>

                        <button id="var-run-btn" class="openclaw-btn openclaw-btn-primary">Generate Variants JSON</button>
                    </div>

                    <div class="openclaw-card">
                         <div class="openclaw-section-header">Resulting List</div>
                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Output (List of Params)</label>
                            <textarea id="var-output" class="openclaw-textarea openclaw-textarea-lg" readonly></textarea>
                        </div>
                    </div>
                </div>
            </div>
        `;

        container.querySelector("#var-run-btn").onclick = () => {
            clearError(container);
            try {
                const baseStr = container.querySelector("#var-base-params").value;
                if (!baseStr.trim()) throw new Error("Base parameters required");

                const base = parseJsonOrThrow(
                    baseStr,
                    "Base parameters must be valid JSON"
                );

                const count = parseInt(container.querySelector("#var-seed-count").value) || 4;
                const variants = [];

                // Simple logic for MVP (Seed Sweep only)
                for (let i = 0; i < count; i++) {
                    const v = { ...base };
                    v.seed = (base.seed || 0) + i;
                    variants.push(v);
                }

                container.querySelector("#var-output").value = JSON.stringify(variants, null, 2);
            } catch (e) {
                showError(container, e.message);
            }
        };
    }
};
