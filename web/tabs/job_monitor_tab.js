/**
 * F17: Job Monitor Tab
 * Tracks prompt execution and displays outputs.
 */
import { openclawApi } from "../openclaw_api.js";
import { extractHistoryImageRefs } from "../openclaw_asset_refs.js";
import { parseJsonSafe } from "../openclaw_utils.js";

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 150;
const STORAGE_KEY = "openclaw-job-monitor-jobs";
const LEGACY_STORAGE_KEY = "moltbot-job-monitor-jobs";

let currentJobs = [];
let pollIntervals = {};

function loadJobs() {
    try {
        // Keep one-way fallback so existing users keep their tracked jobs after rename.
        const stored = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY);
        currentJobs = stored ? parseJsonSafe(stored, []).value : [];
        if (stored && !localStorage.getItem(STORAGE_KEY)) {
            localStorage.setItem(STORAGE_KEY, stored);
        }
    } catch {
        currentJobs = [];
    }
}

function saveJobs() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(currentJobs.slice(0, 20)));
}

export function addJob(promptId, traceId = null) {
    if (!promptId || currentJobs.some((j) => j.promptId === promptId)) return;
    currentJobs.unshift({ promptId, traceId, timeline: [], status: "pending", outputs: [], addedAt: Date.now() });
    saveJobs();
}

export const jobMonitorTab = {
    id: "job-monitor",
    title: "Jobs",
    icon: "pi pi-briefcase",
    render: async (container) => {
        loadJobs();
        container.innerHTML = "";

        // Header
        const header = document.createElement("div");
        header.className = "openclaw-section moltbot-section";
        header.innerHTML = `<h4>Job Monitor</h4>`;

        // Add Manual Job
        const addRow = document.createElement("div");
        addRow.style.display = "flex";
        addRow.style.gap = "8px";
        addRow.style.marginBottom = "8px";

        const input = document.createElement("input");
        input.type = "text";
        input.placeholder = "prompt_id";
        input.style.flex = "1";
        input.style.padding = "4px";

        const addBtn = document.createElement("button");
        addBtn.textContent = "Add";
        addBtn.onclick = () => {
            const val = input.value.trim();
            if (val) {
                addJob(val);
                input.value = "";
                renderJobList();
            }
        };

        addRow.appendChild(input);
        addRow.appendChild(addBtn);
        header.appendChild(addRow);
        container.appendChild(header);

        // Job List
        const listContainer = document.createElement("div");
        listContainer.id = "openclaw-job-list";
        container.appendChild(listContainer);

        renderJobList();

        function renderJobList() {
            listContainer.innerHTML = "";

            if (currentJobs.length === 0) {
                listContainer.innerHTML = "<div style='opacity: 0.5; padding: 8px;'>No jobs tracked.</div>";
                return;
            }

            currentJobs.forEach((job) => {
                const row = document.createElement("div");
                row.className = "openclaw-job-row moltbot-job-row";
                row.style.borderBottom = "1px solid var(--border-color)";
                row.style.padding = "8px 0";

                // Header
                const jobHeader = document.createElement("div");
                jobHeader.style.display = "flex";
                jobHeader.style.justifyContent = "space-between";
                jobHeader.style.alignItems = "center";

                const idSpan = document.createElement("span");
                idSpan.style.fontFamily = "monospace";
                idSpan.textContent = job.promptId.substring(0, 16) + "...";
                idSpan.title = job.promptId;

                const statusBadge = document.createElement("span");
                statusBadge.className = `openclaw-kv-val moltbot-kv-val ${job.status === "completed" ? "ok" : job.status === "error" ? "error" : ""}`;
                statusBadge.textContent = job.status;

                const removeBtn = document.createElement("button");
                removeBtn.textContent = "×";
                removeBtn.title = "Remove";
                removeBtn.style.marginLeft = "8px";
                removeBtn.onclick = () => {
                    currentJobs = currentJobs.filter((j) => j.promptId !== job.promptId);
                    if (pollIntervals[job.promptId]) {
                        clearInterval(pollIntervals[job.promptId]);
                        delete pollIntervals[job.promptId];
                    }
                    saveJobs();
                    renderJobList();
                };

                jobHeader.appendChild(idSpan);
                jobHeader.appendChild(statusBadge);
                jobHeader.appendChild(removeBtn);
                row.appendChild(jobHeader);

                if (job.traceId) {
                    const traceLine = document.createElement("div");
                    traceLine.style.marginTop = "4px";
                    traceLine.style.opacity = "0.75";
                    traceLine.style.fontSize = "12px";
                    traceLine.style.fontFamily = "monospace";
                    traceLine.textContent = `trace: ${job.traceId}`;
                    row.appendChild(traceLine);

                    // Milestone D: Timeline Visualization
                    if (job.timeline && job.timeline.length > 0) {
                        const timelineDiv = document.createElement("div");
                        timelineDiv.style.marginTop = "4px";
                        timelineDiv.style.fontSize = "11px";
                        timelineDiv.style.display = "flex";
                        timelineDiv.style.alignItems = "center";
                        timelineDiv.style.gap = "4px";
                        timelineDiv.style.flexWrap = "wrap";

                        job.timeline.forEach((evt, idx) => {
                            const evtSpan = document.createElement("span");
                            evtSpan.textContent = evt.event; // e.g. "queued"
                            evtSpan.title = new Date(evt.ts * 1000).toLocaleString();
                            evtSpan.style.padding = "2px 4px";
                            evtSpan.style.background = "var(--bg-color)";
                            evtSpan.style.border = "1px solid var(--border-color)";
                            evtSpan.style.borderRadius = "3px";

                            timelineDiv.appendChild(evtSpan);

                            if (idx < job.timeline.length - 1) {
                                const arrow = document.createElement("span");
                                arrow.textContent = "→";
                                arrow.style.opacity = "0.5";
                                timelineDiv.appendChild(arrow);
                            }
                        });
                        row.appendChild(timelineDiv);
                    }
                }

                // Outputs
                if (job.outputs && job.outputs.length > 0) {
                    const outputGrid = document.createElement("div");
                    outputGrid.style.display = "flex";
                    outputGrid.style.flexWrap = "wrap";
                    outputGrid.style.gap = "4px";
                    outputGrid.style.marginTop = "8px";

                    job.outputs.forEach((out) => {
                        const img = document.createElement("img");
                        img.src = out.view_url;
                        img.style.maxWidth = "80px";
                        img.style.maxHeight = "80px";
                        img.style.objectFit = "cover";
                        img.style.cursor = "pointer";
                        img.title = out.filename;
                        img.onclick = () => window.open(out.view_url, "_blank");
                        outputGrid.appendChild(img);
                    });

                    row.appendChild(outputGrid);
                }

                listContainer.appendChild(row);

                // Start polling if pending/unknown
                if (job.status === "pending" && !pollIntervals[job.promptId]) {
                    startPolling(job.promptId, renderJobList);
                }
            });
        }
    },
};

async function startPolling(promptId, onUpdate) {
    let attempts = 0;
    pollIntervals[promptId] = setInterval(async () => {
        attempts++;
        if (attempts > POLL_MAX_ATTEMPTS) {
            clearInterval(pollIntervals[promptId]);
            delete pollIntervals[promptId];
            return;
        }

        const res = await openclawApi.getHistory(promptId);
        if (!res.ok) return;

        const job = currentJobs.find((j) => j.promptId === promptId);
        if (!job) {
            clearInterval(pollIntervals[promptId]);
            delete pollIntervals[promptId];
            return;
        }

        const historyItem = res.data;
        if (!historyItem) return;

        // R25: Best-effort trace lookup (optional endpoint; ignore 403/404)
        if (!job.traceId && (attempts === 1 || attempts % 5 === 0)) {
            const t = await openclawApi.getTrace(promptId);
            if (t.ok && t.data?.trace?.trace_id) {
                job.traceId = t.data.trace.trace_id;
                job.timeline = t.data.trace.events || [];
                saveJobs();
                onUpdate();
            }
        }

        const statusStr = historyItem?.status?.status_str;
        if (statusStr === "error") {
            job.status = "error";
            saveJobs();
            onUpdate();
            clearInterval(pollIntervals[promptId]);
            delete pollIntervals[promptId];
            return;
        }

        if (statusStr === "success" || historyItem.outputs) {
            job.status = "completed";
            job.outputs = extractImages(historyItem);
            saveJobs();
            onUpdate();
            clearInterval(pollIntervals[promptId]);
            delete pollIntervals[promptId];
        }
    }, POLL_INTERVAL_MS);

}

function extractImages(historyItem) {
    return extractHistoryImageRefs(historyItem).map((img) => ({
        filename: img.filename,
        subfolder: img.subfolder,
        type: img.type,
        asset_hash: img.asset_hash,
        view_url: openclawApi.buildViewUrlForRef(img),
    }));
}
