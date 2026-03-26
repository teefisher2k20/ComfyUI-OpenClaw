const MAX_PROMOTED_WIDGET_DEPTH = 24;
const COMPARE_WIDGET_NAMES = new Set(["ckpt_name", "lora_name", "unet_name"]);

function normalizeNodeId(nodeId) {
    if (nodeId === null || nodeId === undefined || nodeId === "") {
        return null;
    }
    return String(nodeId);
}

function getDirectGraphNodes(graph) {
    if (!graph || typeof graph !== "object") {
        return [];
    }
    if (Array.isArray(graph._nodes)) {
        return graph._nodes;
    }
    if (Array.isArray(graph.nodes)) {
        return graph.nodes;
    }
    return [];
}

function getNodeTitle(node, fallbackId) {
    return node?.title || node?.type || `Node ${fallbackId}`;
}

function getNodeWidgets(node) {
    return Array.isArray(node?.widgets) ? node.widgets : [];
}

function getNodeSubgraph(node) {
    return node?.subgraph && typeof node.subgraph === "object" ? node.subgraph : null;
}

function isPromotedWidgetView(widget) {
    return !!widget && typeof widget === "object" && widget.sourceNodeId !== undefined && widget.sourceWidgetName !== undefined;
}

function buildNodeEntry(node, graph, executionId, pathTitles) {
    const rawId = normalizeNodeId(node?.id);
    const title = getNodeTitle(node, rawId || executionId);
    return {
        id: executionId,
        rawId,
        title,
        displayTitle: pathTitles.join(" / "),
        type: node?.type || title,
        node,
        graph,
        pathTitles,
        isNested: pathTitles.length > 1,
    };
}

function findDirectNodeByRawId(graph, rawId) {
    const normalized = normalizeNodeId(rawId);
    if (!normalized) {
        return null;
    }
    return getDirectGraphNodes(graph).find((node) => normalizeNodeId(node?.id) === normalized) || null;
}

function getChildNodeEntry(hostEntry, rawChildId) {
    const subgraph = getNodeSubgraph(hostEntry?.node);
    if (!subgraph) {
        return null;
    }
    const child = findDirectNodeByRawId(subgraph, rawChildId);
    if (!child) {
        return null;
    }
    const rawId = normalizeNodeId(child.id);
    const title = getNodeTitle(child, rawId || rawChildId);
    const executionId = `${hostEntry.id}:${rawId}`;
    return buildNodeEntry(child, subgraph, executionId, [...hostEntry.pathTitles, title]);
}

function findWidgetByIdentity(widgets, widgetName, sourceNodeId) {
    if (!Array.isArray(widgets) || !widgetName) {
        return null;
    }

    if (sourceNodeId !== null && sourceNodeId !== undefined && sourceNodeId !== "") {
        const normalizedSourceId = String(sourceNodeId);
        return (
            widgets.find(
                (entry) =>
                    isPromotedWidgetView(entry) &&
                    normalizeNodeId(entry.disambiguatingSourceNodeId ?? entry.sourceNodeId) === normalizedSourceId &&
                    (entry.sourceWidgetName === widgetName || entry.name === widgetName)
            ) || null
        );
    }

    return widgets.find((entry) => entry?.name === widgetName || entry?.sourceWidgetName === widgetName) || null;
}

function resolvePromotedWidget(entry, widget) {
    let currentEntry = entry;
    let currentWidget = widget;

    for (let depth = 0; depth < MAX_PROMOTED_WIDGET_DEPTH; depth += 1) {
        if (!isPromotedWidgetView(currentWidget)) {
            return {
                hostEntry: entry,
                nodeEntry: currentEntry,
                node: currentEntry.node,
                widget: currentWidget,
                promotedDepth: depth,
            };
        }

        const sourceEntry = getChildNodeEntry(currentEntry, currentWidget.sourceNodeId);
        if (!sourceEntry) {
            return null;
        }

        const sourceWidget = findWidgetByIdentity(
            getNodeWidgets(sourceEntry.node),
            currentWidget.sourceWidgetName,
            currentWidget.disambiguatingSourceNodeId
        );
        if (!sourceWidget) {
            return null;
        }

        currentEntry = sourceEntry;
        currentWidget = sourceWidget;
    }

    return null;
}

export function getGraphNodeCatalog(graph) {
    const entries = [];
    const visitedGraphs = new WeakSet();

    function visit(currentGraph, parentExecutionId = "", parentPathTitles = []) {
        if (!currentGraph || typeof currentGraph !== "object" || visitedGraphs.has(currentGraph)) {
            return;
        }
        visitedGraphs.add(currentGraph);

        for (const node of getDirectGraphNodes(currentGraph)) {
            const rawId = normalizeNodeId(node?.id);
            if (!rawId) {
                continue;
            }
            const title = getNodeTitle(node, rawId);
            const executionId = parentExecutionId ? `${parentExecutionId}:${rawId}` : rawId;
            const pathTitles = [...parentPathTitles, title];
            const entry = buildNodeEntry(node, currentGraph, executionId, pathTitles);
            entries.push(entry);

            const subgraph = getNodeSubgraph(node);
            if (subgraph && subgraph !== currentGraph) {
                visit(subgraph, executionId, pathTitles);
            }
        }
    }

    visit(graph);
    return entries;
}

export function getGraphNodeEntry(graph, nodeId) {
    const normalized = normalizeNodeId(nodeId);
    if (!normalized) {
        return null;
    }

    const segments = normalized.split(":");
    let currentGraph = graph;
    let currentEntry = null;
    let currentExecutionId = "";
    let pathTitles = [];

    for (let idx = 0; idx < segments.length; idx += 1) {
        const segment = segments[idx];
        const node = findDirectNodeByRawId(currentGraph, segment);
        if (!node) {
            currentEntry = null;
            break;
        }

        currentExecutionId = currentExecutionId ? `${currentExecutionId}:${segment}` : segment;
        pathTitles = [...pathTitles, getNodeTitle(node, segment)];
        currentEntry = buildNodeEntry(node, currentGraph, currentExecutionId, pathTitles);

        if (idx < segments.length - 1) {
            currentGraph = getNodeSubgraph(node);
            if (!currentGraph) {
                return null;
            }
        }
    }

    if (currentEntry) {
        return currentEntry;
    }

    if (segments.length === 1) {
        const matches = getGraphNodeCatalog(graph).filter((entry) => entry.rawId === normalized);
        if (matches.length === 1) {
            return matches[0];
        }
    }

    return null;
}

export function getGraphNodeEntryByObject(graph, targetNode) {
    if (!targetNode || typeof targetNode !== "object") {
        return null;
    }
    return getGraphNodeCatalog(graph).find((entry) => entry.node === targetNode) || null;
}

export function resolveGraphWidget(graph, nodeRefOrId, widgetName, sourceNodeId = null) {
    // IMPORTANT: keep nested-subgraph and promoted-widget host resolution centralized here.
    // Replacing callers with direct graph._nodes/getNodeById/node.widgets access silently drops newer frontend host shapes.
    const entry =
        typeof nodeRefOrId === "object" && nodeRefOrId
            ? getGraphNodeEntryByObject(graph, nodeRefOrId)
            : getGraphNodeEntry(graph, nodeRefOrId);
    if (!entry || !widgetName) {
        return null;
    }

    const widget = findWidgetByIdentity(getNodeWidgets(entry.node), widgetName, sourceNodeId);
    if (!widget) {
        return null;
    }

    const resolved = resolvePromotedWidget(entry, widget);
    if (!resolved) {
        return {
            hostEntry: entry,
            nodeEntry: entry,
            node: entry.node,
            widget,
            promotedDepth: 0,
        };
    }

    return resolved;
}

export function getGraphWidgetCatalog(graph, nodeId) {
    const entry = getGraphNodeEntry(graph, nodeId);
    if (!entry) {
        return [];
    }

    return getNodeWidgets(entry.node).map((widget) => {
        const widgetName = widget?.name || widget?.sourceWidgetName || "";
        const resolved = resolveGraphWidget(
            graph,
            entry.id,
            widgetName,
            widget?.disambiguatingSourceNodeId || null
        );
        return {
            name: widgetName,
            type: widget?.type || resolved?.widget?.type || "unknown",
            value: widget?.value,
            options: widget?.options,
            isPromoted: isPromotedWidgetView(widget),
            sourceNodeId: widget?.sourceNodeId,
            sourceWidgetName: widget?.sourceWidgetName,
            resolvedNodeId: resolved?.nodeEntry?.id || entry.id,
            resolvedWidgetName: resolved?.widget?.name || widgetName,
        };
    });
}

export function getGraphWidgetValueCandidates(graph, nodeId, widgetName) {
    const resolved = resolveGraphWidget(graph, nodeId, widgetName);
    if (!resolved || !resolved.widget) {
        return [];
    }

    const opts =
        resolved.widget.options && Array.isArray(resolved.widget.options.values)
            ? [...resolved.widget.options.values]
            : [];
    if (!opts.some((candidate) => String(candidate) === String(resolved.widget.value))) {
        opts.unshift(resolved.widget.value);
    }
    return opts.filter((candidate) => candidate !== undefined);
}

export function findComparableWidget(graph, nodeRefOrId) {
    const entry =
        typeof nodeRefOrId === "object" && nodeRefOrId
            ? getGraphNodeEntryByObject(graph, nodeRefOrId)
            : getGraphNodeEntry(graph, nodeRefOrId);
    if (!entry) {
        return null;
    }

    for (const widget of getNodeWidgets(entry.node)) {
        const widgetName = widget?.name || widget?.sourceWidgetName;
        if (!COMPARE_WIDGET_NAMES.has(widgetName)) {
            continue;
        }
        const resolved = resolveGraphWidget(
            graph,
            entry.id,
            widgetName,
            widget?.disambiguatingSourceNodeId || null
        );
        if (!resolved) {
            continue;
        }
        return {
            hostEntry: entry,
            nodeEntry: resolved.nodeEntry,
            widget: resolved.widget,
            hostWidgetName: widgetName,
            nodeId: resolved.nodeEntry.id,
            widgetName: resolved.widget.name || widgetName,
        };
    }

    return null;
}

export function hasComparableWidget(graph, nodeRefOrId) {
    return !!findComparableWidget(graph, nodeRefOrId);
}
