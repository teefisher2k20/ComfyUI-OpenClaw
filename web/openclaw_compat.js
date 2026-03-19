/**
 * R149: Central registry for frontend compatibility surfaces.
 * Keep legacy aliases declared once so migrations do not depend on scattered
 * string literals.
 */

export const API_PREFIXES = Object.freeze({
    canonical: "/openclaw",
    legacy: "/moltbot",
});

export const SETTINGS_PREFIXES = Object.freeze({
    canonical: "OpenClaw",
    legacy: "Moltbot",
});

export const STORAGE_KEYS = Object.freeze({
    session: Object.freeze({
        adminToken: Object.freeze({
            primary: "openclaw_admin_token",
            legacy: "moltbot_admin_token",
        }),
    }),
    local: Object.freeze({
        activeTab: Object.freeze({
            primary: "openclaw-active-tab",
            legacy: "moltbot-active-tab",
        }),
        notifications: Object.freeze({
            primary: "openclaw_notifications",
            legacy: null,
        }),
        remoteAdminToken: Object.freeze({
            primary: "openclaw_remote_admin_token",
            legacy: null,
        }),
    }),
});

export const HEADER_ALIASES = Object.freeze({
    adminToken: Object.freeze({
        primary: "X-OpenClaw-Admin-Token",
        legacy: "X-Moltbot-Admin-Token",
    }),
    obsToken: Object.freeze({
        primary: "X-OpenClaw-Obs-Token",
        legacy: "X-Moltbot-Obs-Token",
    }),
});

export function getApiPathCandidates(path) {
    if (typeof path !== "string") return [path];
    if (path.startsWith(`${API_PREFIXES.canonical}/`)) {
        return [path, path.replace(API_PREFIXES.canonical, API_PREFIXES.legacy)];
    }
    if (path.startsWith(`${API_PREFIXES.legacy}/`)) {
        return [path, path.replace(API_PREFIXES.legacy, API_PREFIXES.canonical)];
    }
    return [path];
}

export function getMirroredStorageValue(storage, spec) {
    return storage.getItem(spec.primary) || (spec.legacy ? storage.getItem(spec.legacy) : null);
}

export function setMirroredStorageValue(storage, spec, value) {
    if (!value) {
        storage.removeItem(spec.primary);
        if (spec.legacy) storage.removeItem(spec.legacy);
        return;
    }

    storage.setItem(spec.primary, value);
    if (spec.legacy) storage.setItem(spec.legacy, value);
}

export function getCompatibleSettingValue(settings, key, fallbackValue) {
    if (!settings?.getSettingValue) return fallbackValue;

    const canonicalValue = settings.getSettingValue(
        `${SETTINGS_PREFIXES.canonical}.${key}`,
        undefined
    );
    if (canonicalValue !== undefined) return canonicalValue;

    const legacyValue = settings.getSettingValue(
        `${SETTINGS_PREFIXES.legacy}.${key}`,
        undefined
    );
    return legacyValue !== undefined ? legacyValue : fallbackValue;
}

function buildAliasHeaderMap(value, aliases) {
    const normalized = value || "";
    const headers = {};
    aliases.forEach((alias) => {
        headers[alias.primary] = normalized;
        if (alias.legacy) headers[alias.legacy] = normalized;
    });
    return headers;
}

export function buildAdminTokenHeaders(token) {
    return buildAliasHeaderMap(token, [HEADER_ALIASES.adminToken]);
}

export function buildRemoteAdminHeaders(token) {
    return buildAliasHeaderMap(token, [
        HEADER_ALIASES.adminToken,
        HEADER_ALIASES.obsToken,
    ]);
}
