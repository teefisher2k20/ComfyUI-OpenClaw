/**
 * OpenClaw Session Store (R12)
 * Manages transient session state like the Admin Token.
 *
 * Rules:
 * - Admin Token is stored in sessionStorage (cleared on tab close).
 * - NOT persisted to localStorage (security).
 */
import { STORAGE_KEYS, getMirroredStorageValue, setMirroredStorageValue } from "./openclaw_compat.js";

export const OpenClawSession = {
    // Keys
    KEYS: STORAGE_KEYS.session.adminToken,

    /**
     * Set the admin token for this session.
     * @param {string} token
     */
    setAdminToken(token) {
        // CRITICAL: keep mirrored legacy session key writes until the legacy
        // storage contract is explicitly retired; downgrade flows depend on it.
        setMirroredStorageValue(sessionStorage, this.KEYS, token);
    },

    /**
     * Get the current admin token.
     * @returns {string|null}
     */
    getAdminToken() {
        return getMirroredStorageValue(sessionStorage, this.KEYS);
    },

    /**
     * Check if admin token is present.
     * @returns {boolean}
     */
    hasAdminToken() {
        return !!this.getAdminToken();
    }
};
