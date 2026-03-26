import { describe, expect, it } from "vitest";
import {
    findComparableWidget,
    getGraphNodeCatalog,
    getGraphWidgetCatalog,
    getGraphWidgetValueCandidates,
    resolveGraphWidget,
} from "../../openclaw_graph_host.js";

function createGraphFixture() {
    const nestedLoader = {
        id: 7,
        type: "CheckpointLoaderSimple",
        title: "Nested Loader",
        widgets: [
            {
                name: "ckpt_name",
                type: "combo",
                value: "base.ckpt",
                options: { values: ["base.ckpt", "xl.ckpt"] },
            },
        ],
    };
    const nestedSampler = {
        id: 8,
        type: "KSampler",
        title: "Nested Sampler",
        widgets: [
            {
                name: "seed",
                type: "number",
                value: 1234,
                options: { values: [1234, 4321] },
            },
        ],
    };
    const subgraph = {
        _nodes: [nestedLoader, nestedSampler],
        getNodeById(id) {
            return this._nodes.find((node) => String(node.id) === String(id));
        },
    };
    const subgraphHost = {
        id: 50,
        type: "SubgraphNode",
        title: "Workflow Pack",
        widgets: [
            {
                name: "ckpt_name",
                type: "combo",
                value: "base.ckpt",
                options: {},
                sourceNodeId: "7",
                sourceWidgetName: "ckpt_name",
            },
            {
                name: "seed",
                type: "number",
                value: 1234,
                options: {},
                sourceNodeId: "8",
                sourceWidgetName: "seed",
            },
        ],
        subgraph,
    };
    const rootSampler = {
        id: 10,
        type: "KSampler",
        title: "Root Sampler",
        widgets: [
            {
                name: "steps",
                type: "number",
                value: 20,
                options: { values: [20, 30] },
            },
        ],
    };

    return {
        _nodes: [rootSampler, subgraphHost],
        getNodeById(id) {
            return this._nodes.find((node) => String(node.id) === String(id));
        },
    };
}

describe("openclaw_graph_host", () => {
    it("builds catalog entries for nested subgraph nodes", () => {
        const graph = createGraphFixture();
        const catalog = getGraphNodeCatalog(graph);

        expect(catalog.map((entry) => entry.id)).toEqual(["10", "50", "50:7", "50:8"]);
        expect(catalog.find((entry) => entry.id === "50:7")?.displayTitle).toBe(
            "Workflow Pack / Nested Loader"
        );
    });

    it("resolves promoted widget catalogs and candidate values from the nested source widget", () => {
        const graph = createGraphFixture();
        const widgetCatalog = getGraphWidgetCatalog(graph, "50");
        const promotedWidget = widgetCatalog.find((entry) => entry.name === "ckpt_name");

        expect(promotedWidget?.isPromoted).toBe(true);
        expect(promotedWidget?.resolvedNodeId).toBe("50:7");
        expect(getGraphWidgetValueCandidates(graph, "50", "ckpt_name")).toEqual([
            "base.ckpt",
            "xl.ckpt",
        ]);
    });

    it("finds compare targets through promoted widget metadata", () => {
        const graph = createGraphFixture();
        const compareTarget = findComparableWidget(graph, "50");
        const resolved = resolveGraphWidget(graph, "50", "ckpt_name");

        expect(compareTarget?.nodeId).toBe("50:7");
        expect(compareTarget?.widgetName).toBe("ckpt_name");
        expect(resolved?.nodeEntry.id).toBe("50:7");
        expect(resolved?.widget.name).toBe("ckpt_name");
    });
});
