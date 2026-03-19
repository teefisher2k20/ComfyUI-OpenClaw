
/**
 * Shared Utilities for Moltbot UI
 */
import { openclawNotifications } from "./openclaw_notifications.js";

/**
 * Simple DOM factory helper.
 * @param {string} tag - HTML tag name
 * @param {string} className - Optional class name
 * @param {string} text - Optional text content
 */
export function makeEl(tag, className = "", text = "") {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null && text !== "") {
        el.textContent = text;
    }
    return el;
}

/**
 * F63: prefer canonical `openclaw-*` classes when both legacy and canonical
 * variants are present on the same node.
 */
export function normalizeLegacyClassTokens(className = "") {
    const tokens = String(className)
        .split(/\s+/)
        .map((token) => token.trim())
        .filter(Boolean);
    const canonical = new Set(
        tokens.filter((token) => token.startsWith("openclaw-"))
    );
    const seen = new Set();
    const normalized = [];

    tokens.forEach((token) => {
        if (token.startsWith("moltbot-")) {
            const suffix = token.slice("moltbot-".length);
            if (canonical.has(`openclaw-${suffix}`)) {
                return;
            }
        }
        if (seen.has(token)) {
            return;
        }
        seen.add(token);
        normalized.push(token);
    });

    return normalized.join(" ");
}

export function normalizeLegacyClassNames(root) {
    if (!root) return root;
    const nodes = [];
    if (typeof root.className === "string") {
        nodes.push(root);
    }
    if (typeof root.querySelectorAll === "function") {
        nodes.push(...root.querySelectorAll("[class]"));
    }

    nodes.forEach((node) => {
        if (typeof node.className !== "string") return;
        const normalized = normalizeLegacyClassTokens(node.className);
        if (normalized !== node.className) {
            node.className = normalized;
        }
    });

    return root;
}

/**
 * Lightweight toast helper for UI feedback.
 * @param {string} message
 * @param {"info"|"error"|"success"|"warning"} variant
 */
export function showToast(message, variant = "info", options = {}) {
    const toast = document.createElement("div");
    toast.className = `openclaw-toast moltbot-toast openclaw-toast-${variant} moltbot-toast-${variant}`;
    toast.textContent = message;
    toast.style.position = "fixed";
    toast.style.right = "16px";
    toast.style.bottom = "16px";
    toast.style.padding = "8px 12px";
    toast.style.borderRadius = "6px";
    toast.style.background = variant === "error" ? "#5a1e1e" : (variant === "success" ? "#1e5a2b" : "#2d2d2d");
    toast.style.color = "#fff";
    toast.style.zIndex = "9999";
    toast.style.boxShadow = "0 4px 12px rgba(0,0,0,0.3)";
    document.body.appendChild(toast);

    const shouldPersist = options.persist != null ? Boolean(options.persist) : variant === "error";
    if (shouldPersist) {
        openclawNotifications.notify({
            severity: variant,
            message,
            source: options.source || "toast",
            dedupeKey: options.dedupeKey,
            action: options.action,
            metadata: options.metadata,
        });
    }

    setTimeout(() => {
        toast.remove();
    }, Number.isFinite(options.durationMs) ? options.durationMs : 2500);
}

/**
 * Display an error message within a container.
 * Looks for an existing .openclaw-error-box (legacy: .moltbot-error-box), or creates one at the top.
 */
export function showError(container, message) {
    let errorBox = container.querySelector('.openclaw-error-box');

    if (!errorBox) {
        // Try to find one by ID pattern if specific class missing? No, stick to class.
        // If not found, inject at top of panel
        const panel = container.querySelector('.openclaw-panel') || container;
        errorBox = document.createElement("div");
        errorBox.className = "openclaw-error-box moltbot-error-box";
        // Insert after panel header or at top
        const header = panel.querySelector('.openclaw-section-header');
        if (header && header.nextSibling) {
            panel.insertBefore(errorBox, header.nextSibling);
        } else {
            panel.prepend(errorBox);
        }
    }

    errorBox.textContent = message;
    errorBox.style.display = "block";

    // Auto-scroll to error
    errorBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/**
 * Clear error message in container.
 */
export function clearError(container) {
    const errorBox = container.querySelector('.openclaw-error-box');
    if (errorBox) {
        errorBox.style.display = "none";
        errorBox.textContent = "";
    }
}

/**
 * Copy text to clipboard and show ephemeral tooltip/toast
 */
export async function copyToClipboard(text, btnElement) {
    try {
        await navigator.clipboard.writeText(text);

        // Show feedback on button
        const origText = btnElement.textContent;
        btnElement.textContent = "Copied!";
        btnElement.classList.add("openclaw-btn-success", "moltbot-btn-success");

        setTimeout(() => {
            btnElement.textContent = origText;
            btnElement.classList.remove("openclaw-btn-success", "moltbot-btn-success");
        }, 1500);

    } catch (err) {
        console.error("Failed to copy:", err);
        alert("Failed to copy to clipboard");
    }
}

/**
 * R55: Safe JSON parse helper with deterministic fallback semantics.
 */
export function parseJsonSafe(raw, fallbackValue = null) {
    if (typeof raw !== "string") {
        return {
            ok: false,
            value: fallbackValue,
            error: new Error("json_input_must_be_string"),
        };
    }
    try {
        return { ok: true, value: JSON.parse(raw), error: null };
    } catch (error) {
        return { ok: false, value: fallbackValue, error };
    }
}

/**
 * R55: Parse JSON or throw with a consistent message.
 */
export function parseJsonOrThrow(raw, message = "Invalid JSON") {
    const parsed = parseJsonSafe(raw);
    if (parsed.ok) return parsed.value;
    const suffix = parsed.error?.message ? `: ${parsed.error.message}` : "";
    throw new Error(`${message}${suffix}`);
}

/**
 * R55: Link an external AbortSignal to a local AbortController.
 * Returns a cleanup function to remove listeners.
 */
export function linkAbortSignal(externalSignal, controller, onAbort = null) {
    if (!externalSignal || !controller) {
        return () => { };
    }

    const handleAbort = () => {
        if (typeof onAbort === "function") onAbort();
        controller.abort();
    };

    if (externalSignal.aborted) {
        handleAbort();
        return () => { };
    }

    externalSignal.addEventListener("abort", handleAbort, { once: true });
    return () => externalSignal.removeEventListener("abort", handleAbort);
}

/**
 * R55: Normalize abort error detection across modules.
 */
export function isAbortError(err) {
    return Boolean(err && err.name === "AbortError");
}

/**
 * R38-Lite: Create a shared request lifecycle controller for staged loading + elapsed timer + cancel.
 *
 * @param {HTMLElement} container
 * @param {object} selectors
 * @param {string} selectors.loading
 * @param {string} selectors.runButton
 * @param {string} selectors.stage
 * @param {string} selectors.elapsed
 */
export function createRequestLifecycleController(container, selectors) {
    const loadingEl = container.querySelector(selectors.loading);
    const runBtnEl = container.querySelector(selectors.runButton);
    const stageEl = container.querySelector(selectors.stage);
    const elapsedEl = container.querySelector(selectors.elapsed);

    let abortController = null;
    let timerInterval = null;
    let startTime = 0;

    const showLoading = (show) => {
        if (loadingEl) loadingEl.style.display = show ? "block" : "none";
        if (runBtnEl) runBtnEl.style.display = show ? "none" : "block";
    };

    const stopTimer = () => {
        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }
    };

    const setStage = (text) => {
        if (stageEl) stageEl.textContent = text;
    };

    const startTimer = () => {
        startTime = Date.now();
        if (elapsedEl) elapsedEl.textContent = "Elapsed: 0s";
        stopTimer();
        timerInterval = setInterval(() => {
            if (!elapsedEl) return;
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            elapsedEl.textContent = `Elapsed: ${elapsed}s`;
        }, 500);
    };

    const begin = (initialStage = "Preparing request...") => {
        if (abortController) {
            abortController.abort();
        }
        abortController = new AbortController();
        setStage(initialStage);
        showLoading(true);
        startTimer();
        return abortController.signal;
    };

    const end = () => {
        stopTimer();
        showLoading(false);
        abortController = null;
    };

    const cancel = () => {
        if (!abortController) return false;
        abortController.abort();
        end();
        return true;
    };

    return {
        begin,
        end,
        cancel,
        setStage,
    };
}
