/**
 * R164: Explicit frontend host-surface detection helpers.
 * Keep desktop-vs-standalone assumptions centralized so extension code does not
 * silently treat the desktop bundle as identical to standalone frontend HEAD.
 */

export const HOST_SURFACES = Object.freeze({
    standaloneFrontend: "standalone_frontend",
    desktop: "desktop",
});

function normalizeSurfaceName(surface) {
    if (surface === HOST_SURFACES.desktop || surface === "desktop") {
        return HOST_SURFACES.desktop;
    }
    if (
        surface === HOST_SURFACES.standaloneFrontend ||
        surface === "standalone" ||
        surface === "standalone_frontend" ||
        surface === "localhost"
    ) {
        return HOST_SURFACES.standaloneFrontend;
    }
    return null;
}

export function resolveHostSurface({ app = null, win = window } = {}) {
    const explicitSurface = normalizeSurfaceName(
        app?.openclawHostSurface || app?.hostSurface || win?.__OPENCLAW_HOST_SURFACE__
    );
    if (explicitSurface) return explicitSurface;

    const distributionSurface = normalizeSurfaceName(win?.__DISTRIBUTION__);
    if (distributionSurface) return distributionSurface;

    if (win?.electronAPI) {
        return HOST_SURFACES.desktop;
    }

    return HOST_SURFACES.standaloneFrontend;
}

export function getHostSurfaceCapabilities(options = {}) {
    const hostSurface = resolveHostSurface(options);
    return {
        hostSurface,
        isDesktop: hostSurface === HOST_SURFACES.desktop,
        supportsElectronBridge:
            hostSurface === HOST_SURFACES.desktop && !!options?.win?.electronAPI,
    };
}

export function stampHostSurfaceMetadata(container, options = {}) {
    const capabilities = getHostSurfaceCapabilities(options);
    if (container?.dataset) {
        container.dataset.openclawHostSurface = capabilities.hostSurface;
        container.dataset.openclawDesktopHost = capabilities.isDesktop
            ? "true"
            : "false";
    }
    return capabilities;
}
