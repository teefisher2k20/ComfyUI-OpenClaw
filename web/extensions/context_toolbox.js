// CRITICAL: this module is served from /extensions/<pack>/web/extensions/*.js in ComfyUI.
// Keep ../../../scripts/app.js so it resolves to ComfyUI core /scripts/app.js (not /extensions/<pack>/scripts/app.js).
import { app } from "../../../scripts/app.js";
import { hasComparableWidget } from "../openclaw_graph_host.js";

/**
 * F51: In-Canvas Context Toolbox
 * Adds quick actions to the node context menu.
 */
export function registerContextToolbox() {
    app.registerExtension({
        name: "OpenClaw.ContextToolbox",
        async setup() {
            // Wait for OpenClaw actions singleton to be available.
            const { openclawActions } = await import("../openclaw_ui.js");

            const originalGetNodeMenuOptions = LGraphCanvas.prototype.getNodeMenuOptions;

            LGraphCanvas.prototype.getNodeMenuOptions = function (node) {
                const options = originalGetNodeMenuOptions.apply(this, arguments);
                if (!options) return options;

                // F51: Add OpenClaw Actions
                options.push(null); // Separator

                // 1. Inspect in Explorer
                options.push({
                    content: "\uD83D\uDD0D OpenClaw: Inspect",
                    callback: () => {
                        openclawActions.openExplorer(node.type);
                    }
                });

                // 2. Doctor / Stats
                options.push({
                    content: "\uD83D\uDC89 OpenClaw: Doctor",
                    callback: () => {
                        openclawActions.openDoctor();
                    }
                });

                // 3. Queue / Status
                options.push({
                    content: "\u23F3 OpenClaw: Queue Status",
                    callback: () => {
                        openclawActions.openQueue("all");
                    }
                });

                // F50: OpenClaw Compare
                // Only show if node has inputs/widgets that can be compared
                if (hasComparableWidget(app.graph, node)) {
                    options.push({
                        content: "\u2696\uFE0F OpenClaw: Compare...",
                        callback: () => {
                            openclawActions.openCompare(node);
                        }
                    });
                }

                // 4. Settings
                options.push({
                    content: "\u2699\uFE0F OpenClaw: Settings",
                    callback: () => {
                        openclawActions.openSettings();
                    }
                });

                // 5. History (if applicable)
                // options.push({ ... });

                return options;
            };
        }
    });
}
