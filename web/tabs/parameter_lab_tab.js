// CRITICAL: this tab module is loaded under /extensions/<pack>/web/tabs/*.js.
// Must resolve ComfyUI core app from /scripts/app.js via ../../../ prefix.
import { app } from "../../../scripts/app.js";
import { openclawApi } from "../openclaw_api.js";
import {
    findComparableWidget,
    getGraphNodeCatalog,
    getGraphWidgetCatalog,
    getGraphWidgetValueCandidates,
} from "../openclaw_graph_host.js";
import { openclawUI } from "../openclaw_ui.js";

/**
 * F52: Parameter Lab Tab
 * Allows users to configure and run bounded parameter sweeps.
 * F50: Includes "Compare Models" wizard.
 */
export const ParameterLabTab = {
    id: "parameter-lab",
    // IMPORTANT: TabManager expects a CSS icon class; using emoji text here hides the tab icon.
    icon: "pi pi-sliders-h",
    title: "Parameter Lab",
    tooltip: "Run experiments with parameter sweeps",

    // State
    dimensions: [],
    plan: null,
    experimentId: null,
    isRunning: false,
    results: [],

    render(container) {
        container.innerHTML = "";
        container.className = "openclaw-tab-content openclaw-tab-content moltbot-tab-content openclaw-lab-container openclaw-lab-container moltbot-lab-container";

        // 1. Header / Toolbar
        const header = document.createElement("div");
        header.className = "openclaw-lab-header openclaw-lab-header moltbot-lab-header";
        header.innerHTML = `
            <div class="openclaw-lab-title-wrap openclaw-lab-title-wrap moltbot-lab-title-wrap">
                <h3>Parameter Lab</h3>
                <p>Build bounded sweeps and compare model variants directly from canvas.</p>
            </div>
            <div class="openclaw-lab-actions openclaw-lab-actions moltbot-lab-actions">
                <button id="lab-history" class="openclaw-btn openclaw-btn moltbot-btn has-icon openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn" title="View History">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">\uD83D\uDCDC</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">History</span>
                </button>
                <div class="openclaw-separator openclaw-separator moltbot-separator"></div>
                <button id="lab-compare-models" class="openclaw-btn openclaw-btn moltbot-btn has-icon openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn" title="Wizard: Compare Models">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">\u2696\uFE0F</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">Compare Models</span>
                </button>
                <div class="openclaw-separator openclaw-separator moltbot-separator"></div>
                <button id="lab-add-dim" class="openclaw-btn openclaw-btn moltbot-btn openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">&#x2795;</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">+ Dimension</span>
                </button>
                <button id="lab-generate" class="openclaw-btn openclaw-btn moltbot-btn openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">&#x1F9ED;</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">Generate Plan</span>
                </button>
            </div>
        `;
        container.appendChild(header);
        this.container = container;

        const main = document.createElement("div");
        main.className = "openclaw-lab-main openclaw-lab-main moltbot-lab-main";
        container.appendChild(main);

        // 2. Configuration Area (Dimensions)
        const configCard = document.createElement("section");
        configCard.className = "openclaw-lab-card openclaw-lab-card moltbot-lab-card";
        configCard.innerHTML = `
            <div class="openclaw-lab-card-head openclaw-lab-card-head moltbot-lab-card-head">
                <h4>Dimensions</h4>
                <span class="openclaw-lab-meta openclaw-lab-meta moltbot-lab-meta" id="lab-dimension-count">0 configured</span>
            </div>
        `;
        const configArea = document.createElement("div");
        configArea.className = "openclaw-lab-config openclaw-lab-config moltbot-lab-config";
        configCard.appendChild(configArea);
        main.appendChild(configCard);
        this.configContainer = configArea;
        this.dimensionCountEl = configCard.querySelector("#lab-dimension-count");

        // 3. Plan / Results Area
        const resultsCard = document.createElement("section");
        resultsCard.className = "openclaw-lab-card openclaw-lab-card moltbot-lab-card";
        resultsCard.innerHTML = `
            <div class="openclaw-lab-card-head openclaw-lab-card-head moltbot-lab-card-head">
                <h4>Plan & Results</h4>
                <span class="openclaw-lab-meta openclaw-lab-meta moltbot-lab-meta">Live status</span>
            </div>
        `;
        const resultsArea = document.createElement("div");
        resultsArea.className = "openclaw-lab-results openclaw-lab-results moltbot-lab-results";
        resultsCard.appendChild(resultsArea);
        main.appendChild(resultsCard);
        this.resultsContainer = resultsArea;

        // Bind Events
        container.querySelector("#lab-add-dim").onclick = () => {
            this.setActiveToolbarButton("lab-add-dim");
            this.addDimensionUI();
        };
        container.querySelector("#lab-generate").onclick = () => {
            this.setActiveToolbarButton("lab-generate");
            this.generatePlan();
        };
        container.querySelector("#lab-compare-models").onclick = () => {
            this.setActiveToolbarButton("lab-compare-models");
            this.showCompareWizard();
        };
        container.querySelector("#lab-history").onclick = () => {
            this.setActiveToolbarButton("lab-history");
            this.showHistory();
        };

        // Start without forced selection state.
        this.setActiveToolbarButton(null);

        // Initial Render
        this.renderDimensions();

        // F50: Listen for Compare Request (once)
        if (!this._listeningForCompare) {
            const onCompare = (e) => {
                const node = e.detail.node;
                if (node) this.showCompareWizard(node);
            };
            window.addEventListener("openclaw:lab:compare", onCompare);
            // Legacy event name for compatibility.
            window.addEventListener("moltbot:lab:compare", onCompare);
            this._listeningForCompare = true;
        }
    },

    async showHistory() {
        this.resultsContainer.innerHTML = "<div class='openclaw-loading openclaw-loading moltbot-loading'>Loading history...</div>";
        try {
            const res = await openclawApi.fetch(openclawApi._path("/lab/experiments"));
            if (res.ok && res.data) {
                this.renderHistoryList(res.data.experiments);
            } else {
                this.resultsContainer.innerHTML = "<div class='openclaw-error openclaw-error moltbot-error'>Failed to load history.</div>";
            }
        } catch (e) {
            this.resultsContainer.innerHTML = "<div class='openclaw-error openclaw-error moltbot-error'>Error: " + e.message + "</div>";
        }
    },

    setActiveToolbarButton(buttonId) {
        if (!this.container) return;
        this.container.querySelectorAll(".openclaw-lab-action-btn").forEach((btn) => {
            btn.classList.toggle("active", buttonId ? btn.id === buttonId : false);
        });
    },

    renderHistoryList(experiments) {
        this.resultsContainer.innerHTML = "";
        const header = document.createElement("div");
        header.className = "openclaw-lab-plan-header openclaw-lab-plan-header moltbot-lab-plan-header";
        header.innerHTML = `<h4>Experiment History</h4><span>${experiments.length} Records</span>`;
        this.resultsContainer.appendChild(header);

        const list = document.createElement("div");
        list.className = "openclaw-lab-run-list openclaw-lab-run-list moltbot-lab-run-list";

        if (experiments.length === 0) {
            list.innerHTML = "<div class='openclaw-hint openclaw-hint moltbot-hint'>No history found. Run a sweep or compare to see results here.</div>";
        }

        experiments.forEach(exp => {
            const item = document.createElement("div");
            item.className = "openclaw-lab-run-item openclaw-lab-run-item moltbot-lab-run-item";
            const dateStr = new Date(exp.created_at * 1000).toLocaleString();
            item.innerHTML = `
                <span class="run-idx">${exp.id.slice(0, 8)}</span>
                <span class="run-params">${dateStr}</span>
                <span class="run-status">${exp.completed_count}/${exp.run_count} runs</span>
                <button class="openclaw-btn-icon openclaw-btn-icon moltbot-btn-icon load-exp" title="Load Details">\u2192</button>
             `;
            item.querySelector(".load-exp").onclick = () => this.loadExperiment(exp.id);
            list.appendChild(item);
        });
        this.resultsContainer.appendChild(list);
    },

    async loadExperiment(expId) {
        this.resultsContainer.innerHTML = "<div class='openclaw-loading openclaw-loading moltbot-loading'>Loading details...</div>";
        try {
            const res = await openclawApi.fetch(openclawApi._path(`/lab/experiments/${expId}`));
            if (res.ok && res.data) {
                this.plan = res.data.experiment;
                this.experimentId = this.plan.experiment_id;
                this.renderPlan();
            }
        } catch (e) {
            this.resultsContainer.innerHTML = "<div class='openclaw-error openclaw-error moltbot-error'>Failed to load experiment.</div>";
        }
    },

    // --- Dynamic Data Helpers ---

    _coerceSelectedNodeId(nodeId) {
        if (nodeId === null || nodeId === undefined || nodeId === "") {
            return null;
        }
        const raw = String(nodeId);
        return /^\d+$/.test(raw) ? parseInt(raw, 10) : raw;
    },

    getNodeCatalog() {
        return getGraphNodeCatalog(app.graph).map((entry) => ({
            id: entry.id,
            title: entry.displayTitle,
            type: entry.type,
        }));
    },

    getWidgetCatalog(nodeId) {
        return getGraphWidgetCatalog(app.graph, nodeId).map((widget) => ({
            name: widget.name,
            type: widget.type,
            value: widget.value,
            options: widget.options,
        }));
    },

    getValueCandidates(nodeId, widgetName) {
        return getGraphWidgetValueCandidates(app.graph, nodeId, widgetName);
    },

    addDimensionUI(defaults = null) {
        // Add a default blank dimension or use defaults
        // Allow migration from legacy values_str if needed
        const newDim = defaults || {
            node_id: null,
            widget_name: "",
            values: [], // Primary state
            values_str: "", // Legacy/Fallback
            strategy: "grid"
        };

        // Migration: if values_str exists but values is empty, parse it?
        // Done lazily at render time or generation time.
        // Better to canonicalize here if defaults provided.
        if (defaults && defaults.values_str && (!defaults.values || defaults.values.length === 0)) {
            newDim.values = defaults.values_str.split(",").map(s => s.trim()).filter(Boolean);
        }

        this.dimensions.push(newDim);
        this.renderDimensions();
    },

    removeDimension(index) {
        this.dimensions.splice(index, 1);
        this.renderDimensions();
    },

    renderDimensions() {
        this.configContainer.innerHTML = "";
        if (this.dimensionCountEl) {
            this.dimensionCountEl.textContent = `${this.dimensions.length} configured`;
        }

        // "Refresh" button (lightweight, just re-renders to pick up graph changes)
        const toolbar = document.createElement("div");
        toolbar.className = "openclaw-lab-config-toolbar openclaw-lab-config-toolbar moltbot-lab-config-toolbar";
        const refreshBtn = document.createElement("button");
        refreshBtn.className = "openclaw-btn-text openclaw-btn-text moltbot-btn-text";
        refreshBtn.id = "lab-refresh-graph";
        refreshBtn.title = "Refresh from Canvas";
        refreshBtn.textContent = "\u21BB Refresh Options";
        refreshBtn.onclick = () => this.renderDimensions();
        toolbar.appendChild(refreshBtn);
        this.configContainer.appendChild(toolbar);

        if (this.dimensions.length === 0) {
            const hint = document.createElement("div");
            hint.className = "openclaw-hint openclaw-hint moltbot-hint";
            hint.textContent = "No dimensions configured. Add one or use 'Compare Models'.";
            this.configContainer.appendChild(hint);
            return;
        }

        const nodeCatalog = this.getNodeCatalog();

        this.dimensions.forEach((dim, idx) => {
            // Migration: Check before rendering
            if ((!dim.values || dim.values.length === 0) && dim.values_str) {
                const migrated = dim.values_str.split(",").map(s => s.trim()).filter(Boolean);
                if (migrated.length > 0) dim.values = migrated;
            }

            const row = document.createElement("div");
            row.className = "openclaw-lab-dim-row openclaw-lab-dim-row moltbot-lab-dim-row dynamic";

            // 1. Node Selector
            const nodeGroup = document.createElement("div");
            nodeGroup.className = "openclaw-form-group openclaw-form-group moltbot-form-group narrow";
            nodeGroup.innerHTML = `<label>Node</label>`;
            const nodeSelect = document.createElement("select");
            nodeSelect.className = "dim-node-select";

            const defaultOpt = document.createElement("option");
            defaultOpt.value = "";
            defaultOpt.textContent = "Select Node...";
            nodeSelect.appendChild(defaultOpt);

            nodeCatalog.forEach(n => {
                const opt = document.createElement("option");
                opt.value = String(n.id);
                opt.textContent = `[${n.id}] ${n.title}`;
                if (String(dim.node_id) === String(n.id)) opt.selected = true;
                nodeSelect.appendChild(opt);
            });

            nodeSelect.onchange = (e) => {
                const newVal = this._coerceSelectedNodeId(e.target.value);
                if (newVal !== null) {
                    dim.node_id = newVal;
                    dim.widget_name = ""; // Reset widget on node change
                    dim.values = [];      // Reset values
                    this.renderDimensions();
                }
            };
            nodeGroup.appendChild(nodeSelect);
            row.appendChild(nodeGroup);

            // 2. Widget Selector (Dependent)
            const widgetGroup = document.createElement("div");
            widgetGroup.className = "openclaw-form-group openclaw-form-group moltbot-form-group narrow";
            widgetGroup.innerHTML = `<label>Widget</label>`;
            const widgetSelect = document.createElement("select");
            widgetSelect.className = "dim-widget-select";

            if (dim.node_id) {
                const widgets = this.getWidgetCatalog(dim.node_id);
                const wDefaultOpt = document.createElement("option");
                wDefaultOpt.value = "";
                wDefaultOpt.textContent = "Select Widget...";
                widgetSelect.appendChild(wDefaultOpt);

                widgets.forEach(w => {
                    const opt = document.createElement("option");
                    opt.value = w.name;
                    opt.textContent = `${w.name} (${w.type})`;
                    if (dim.widget_name === w.name) opt.selected = true;
                    widgetSelect.appendChild(opt);
                });
            } else {
                const disabledOpt = document.createElement("option");
                disabledOpt.value = "";
                disabledOpt.textContent = "Select Node first";
                disabledOpt.disabled = true;
                disabledOpt.selected = true;
                widgetSelect.appendChild(disabledOpt);
                widgetSelect.disabled = true;
            }

            widgetSelect.onchange = (e) => {
                dim.widget_name = e.target.value;
                dim.values = []; // Reset val on widget change
                this.renderDimensions();
            };
            widgetGroup.appendChild(widgetSelect);
            row.appendChild(widgetGroup);

            // 3. Value Management (Candidates + Chips)
            const valueGroup = document.createElement("div");
            valueGroup.className = "openclaw-form-group openclaw-form-group moltbot-form-group wide dynamic-values";
            valueGroup.innerHTML = `<label>Values</label>`;

            const valueControls = document.createElement("div");
            valueControls.className = "dim-value-controls";

            // Candidate Dropdown
            const candidateSelect = document.createElement("select");
            candidateSelect.className = "dim-candidate-select";
            let candidates = [];
            if (dim.node_id && dim.widget_name) {
                candidates = this.getValueCandidates(dim.node_id, dim.widget_name);
                const cDefaultOpt = document.createElement("option");
                cDefaultOpt.value = "";
                cDefaultOpt.textContent = "Add option...";
                candidateSelect.appendChild(cDefaultOpt);

                candidates.forEach(c => {
                    const opt = document.createElement("option");
                    // CRITICAL: keep DOM-construction + textContent; do not switch back to dynamic innerHTML interpolation.
                    // Use stringified value for option value to ensure it works in HTML
                    opt.value = String(c);
                    opt.textContent = String(c);
                    candidateSelect.appendChild(opt);
                });

                candidateSelect.onchange = (e) => {
                    if (e.target.value) {
                        // Attempt to preserve type from candidate list?
                        // Candidates are mixed types. The value in option is stringified.
                        // Fix: match original candidate by string comparison
                        const match = candidates.find(c => String(c) === e.target.value);
                        const valToAdd = match !== undefined ? match : e.target.value;

                        if (!dim.values) dim.values = [];
                        if (!dim.values.includes(valToAdd)) {
                            dim.values.push(valToAdd);
                            this.renderDimensions();
                        }
                        e.target.value = ""; // Reset
                    }
                };
            } else {
                candidateSelect.disabled = true;
                candidateSelect.innerHTML = `<option>...</option>`;
            }
            valueControls.appendChild(candidateSelect);

            // Manual Input (for floats, non-enums)
            const manualInput = document.createElement("input");
            manualInput.type = "text";
            manualInput.className = "dim-manual-input";
            manualInput.placeholder = "Custom val";
            manualInput.onkeydown = (e) => {
                if (e.key === "Enter") {
                    const val = manualInput.value.trim();
                    if (val) {
                        // Try parse number/bool
                        let typedVal = val;
                        if (val === "true") typedVal = true;
                        else if (val === "false") typedVal = false;
                        else if (!isNaN(parseFloat(val)) && isFinite(val) && !val.match(/[a-zA-Z]/)) typedVal = parseFloat(val);

                        if (!dim.values) dim.values = [];
                        dim.values.push(typedVal);
                        this.renderDimensions();
                    }
                }
            };
            valueControls.appendChild(manualInput);

            valueGroup.appendChild(valueControls);

            // Chips Container
            const chips = document.createElement("div");
            chips.className = "dim-value-chips";
            (dim.values || []).forEach((v, vIdx) => {
                const chip = document.createElement("span");
                chip.className = "openclaw-chip openclaw-chip moltbot-chip";
                // IMPORTANT: render value via textContent to avoid UI injection/markup breakage from workflow-provided strings.
                chip.textContent = String(v) + " ";

                const rmBtn = document.createElement("span");
                rmBtn.className = "chip-rm";
                rmBtn.dataset.idx = vIdx;
                rmBtn.textContent = "x";
                rmBtn.onclick = (e) => {
                    dim.values.splice(vIdx, 1);
                    this.renderDimensions();
                };
                chip.appendChild(rmBtn);
                chips.appendChild(chip);
            });
            valueGroup.appendChild(chips);

            // Legacy fallback removed (handled at start of loop)

            row.appendChild(valueGroup);

            // Remove Button
            const rmBtn = document.createElement("button");
            rmBtn.className = "openclaw-btn-icon openclaw-btn-icon moltbot-btn-icon remove-dim";
            rmBtn.textContent = "x";
            rmBtn.title = "Remove Dimension";
            rmBtn.onclick = () => this.removeDimension(idx);
            row.appendChild(rmBtn);

            this.configContainer.appendChild(row);
        });
    },

    // F50: Compare Models Wizard
    showCompareWizard(targetNode = null) {
        // 1. Scan for loader nodes if no target provided
        let target = targetNode ? findComparableWidget(app.graph, targetNode) : null;
        if (!target) {
            const compareTargets = getGraphNodeCatalog(app.graph)
                .filter((entry) =>
                    entry.node?.type === "CheckpointLoaderSimple" ||
                    entry.node?.type === "LORALoader" ||
                    entry.node?.type === "UNETLoader"
                )
                .map((entry) => findComparableWidget(app.graph, entry.node))
                .filter(Boolean);
            if (compareTargets.length === 0) {
                openclawUI.showBanner("warning", "No Checkpoint/LoRA loaders found in workflow.");
                return;
            }
            [target] = compareTargets;
        }

        if (!target?.widget) {
            openclawUI.showBanner("error", `Could not find model widget on node ${targetNode?.id ?? "unknown"}`);
            return;
        }

        // Reset dimensions
        if (this.dimensions.length > 0) {
            if (!confirm("This will clear current dimensions. Continue?")) return;
        }
        this.dimensions = [];

        // Add dimension pre-filled
        const options = target.widget.options?.values || [];
        let initialValues = [];
        if (options.length > 0) {
            // Pick top 2 as example
            initialValues = options.slice(0, 2);
        }

        this.addDimensionUI({
            node_id: this._coerceSelectedNodeId(target.nodeId),
            widget_name: target.widgetName,
            values: initialValues,
            values_str: initialValues.join(", "), // Legacy fallback
            strategy: "compare"
        });

        openclawUI.showBanner(
            "info",
            `Setup comparison for Node ${target.nodeId} (${target.nodeEntry.title}). Edit values to select models.`
        );
    },

    async generatePlan() {
        // Validate: logic updated to check values array
        const validDims = this.dimensions.filter(d => d.node_id && d.widget_name && d.values && d.values.length > 0);

        if (validDims.length === 0) {
            openclawUI.showBanner("error", "Please configure at least one valid dimension with values.");
            return;
        }

        // Prepare Payload
        const params = validDims.map(d => {
            // Use values directly (already typed from inputs/chips)
            return {
                node_id: d.node_id,
                widget_name: d.widget_name,
                values: d.values,
                strategy: d.strategy || "grid"
            };
        });

        const hasCompare = params.some(p => p.strategy === "compare");
        if (hasCompare && params.length !== 1) {
            openclawUI.showBanner(
                "error",
                "Compare mode supports exactly one comparison dimension."
            );
            return;
        }

        try {
            // Serialize current workflow
            // Use app.graph.serialize() to get state
            const graphJson = JSON.stringify(app.graph.serialize());

            let res;
            if (hasCompare) {
                const compare = params[0];
                openclawUI.showBanner("info", "Generating compare plan...");
                res = await openclawApi.fetch(openclawApi._path("/lab/compare"), {
                    method: "POST",
                    body: JSON.stringify({
                        workflow_json: graphJson,
                        items: compare.values,
                        node_id: compare.node_id,
                        widget_name: compare.widget_name
                    })
                });
            } else {
                openclawUI.showBanner("info", "Generating sweep plan...");
                res = await openclawApi.fetch(openclawApi._path("/lab/sweep"), {
                    method: "POST",
                    body: JSON.stringify({
                        workflow_json: graphJson,
                        params: params
                    })
                });
            }

            if (res.ok && res.data) {
                this.plan = res.data.plan;
                this.experimentId = this.plan.experiment_id;
                this.renderPlan();
                openclawUI.showBanner("success", `Plan generated: ${this.plan.runs.length} runs.`);
            } else {
                openclawUI.showBanner("error", "Failed to generate plan: " + (res.error || "Unknown"));
            }
        } catch (e) {
            openclawUI.showBanner("error", "Plan generation error: " + e.message);
        }
    },

    renderPlan() {
        this.resultsContainer.innerHTML = "";
        if (!this.plan) return;

        const header = document.createElement("div");
        header.className = "openclaw-lab-plan-header openclaw-lab-plan-header moltbot-lab-plan-header";
        header.innerHTML = `
            <h4>Experiment: ${this.experimentId.slice(0, 8)}</h4>
            <span>${this.plan.runs.length} Runs</span>
            <button id="lab-run-all" class="openclaw-btn openclaw-btn moltbot-btn primary">Run Experiment</button>
        `;
        this.resultsContainer.appendChild(header);

        const list = document.createElement("div");
        list.className = "openclaw-lab-run-list openclaw-lab-run-list moltbot-lab-run-list";

        this.plan.runs.forEach((run, idx) => {
            const item = document.createElement("div");
            item.className = "openclaw-lab-run-item openclaw-lab-run-item moltbot-lab-run-item";
            item.innerHTML = `
                <span class="run-idx">#${idx + 1}</span>
                <span class="run-params">${JSON.stringify(run).slice(0, 50)}...</span>
                <span class="run-status ${run.status || 'pending'}">${run.status || 'Pending'}</span>
                <button class="openclaw-btn-icon openclaw-btn-icon moltbot-btn-icon replay-run" title="Replay (Apply Values)">\u21A9\uFE0F</button>
            `;
            item.dataset.idx = idx;
            item.querySelector(".replay-run").onclick = (e) => {
                e.stopPropagation();
                this.replayRun(run);
            };
            list.appendChild(item);
        });

        this.resultsContainer.appendChild(list);

        // F50: Side-by-Side Comparison Layout
        if (this.plan.dimensions.some(d => d.strategy === "compare")) {
            this.resultsContainer.classList.add("openclaw-lab-compare-mode", "moltbot-lab-compare-mode");
        } else {
            this.resultsContainer.classList.remove("openclaw-lab-compare-mode", "moltbot-lab-compare-mode");
        }

        this.resultsContainer.querySelector("#lab-run-all").onclick = () => this.runExperiment();
    },

    async runExperiment() {
        if (this.isRunning) return;
        this.isRunning = true;
        openclawUI.showBanner("info", "Starting experiment...");

        const items = this.resultsContainer.querySelectorAll(".openclaw-lab-run-item");

        // Subscribe to events for status updates
        const es = openclawApi.subscribeEvents((data) => {
            if (!this.isRunning) return; // Note: we might want to keep listening even after queuing finishes
            const pid = data.prompt_id;
            if (!pid) return;

            // Find run with this prompt_id
            const runIdx = this.plan.runs.findIndex(r => r.prompt_id === pid);
            if (runIdx !== -1) {
                const item = items[runIdx];
                const statusSpan = item.querySelector(".run-status");

                if (data.event_type === "execution_success" || data.event_type === "completed") {
                    statusSpan.className = "run-status success";
                    statusSpan.textContent = "Completed";
                    // Update backend
                    openclawApi.fetch(openclawApi._path(`/lab/experiments/${this.experimentId}/runs/${runIdx}`), {
                        method: "POST", body: JSON.stringify({ status: "completed" })
                    });
                } else if (data.event_type === "execution_error" || data.event_type === "failed") {
                    statusSpan.className = "run-status error";
                    statusSpan.textContent = "Failed";
                    openclawApi.fetch(openclawApi._path(`/lab/experiments/${this.experimentId}/runs/${runIdx}`), {
                        method: "POST", body: JSON.stringify({ status: "failed" })
                    });
                } else if (data.event_type === "executing") {
                    statusSpan.className = "run-status running";
                    statusSpan.textContent = "Executing Node " + data.node;
                }
            }
        });

        this.es = es;

        try {
            for (let i = 0; i < this.plan.runs.length; i++) {
                // If user stops? (TODO: Add stop button)

                const run = this.plan.runs[i];
                const item = items[i];
                const statusSpan = item.querySelector(".run-status");

                statusSpan.className = "run-status running";
                statusSpan.textContent = "Queuing...";

                try {
                    // 1. Apply overrides
                    this.applyOverrides(run);

                    // 2. Queue Prompt & Capture ID
                    const res = await app.queuePrompt(0, 1);

                    if (res && res.prompt_id) {
                        run.prompt_id = res.prompt_id;
                        statusSpan.textContent = "Queued (" + res.prompt_id.slice(0, 4) + ")";

                        // Register with backend
                        openclawApi.fetch(openclawApi._path(`/lab/experiments/${this.experimentId}/runs/${i}`), {
                            method: "POST",
                            body: JSON.stringify({ status: "queued", output: { prompt_id: res.prompt_id } })
                        });
                    } else {
                        throw new Error("No prompt_id returned");
                    }

                } catch (e) {
                    statusSpan.className = "run-status error";
                    statusSpan.textContent = "Queue Failed";
                    console.error(e);
                }

                await new Promise(r => setTimeout(r, 1000));
            }
        } finally {
            // Keep monitoring
            openclawUI.showBanner("success", "All runs queued. Monitoring progress...");
        }
    },

    replayRun(run) {
        if (confirm("Apply these parameter values to the current workflow?")) {
            this.applyOverrides(run);
            openclawUI.showBanner("success", "Values applied to nodes.");
        }
    },

    applyOverrides(run) {
        Object.entries(run).forEach(([key, value]) => {
            if (key === "prompt_id" || key === "status") return;
            const [nodeId, widgetName] = key.split(".");
            const node = app.graph.getNodeById(parseInt(nodeId));
            if (node) {
                const widget = node.widgets.find(w => w.name === widgetName);
                if (widget) {
                    widget.value = value;
                }
            }
        });
    }
};
