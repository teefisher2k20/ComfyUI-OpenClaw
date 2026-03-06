/**
 * S48 frontend advisory banner contract helpers.
 */

export function buildDoctorAdvisoryBanner(report) {
    const advisory = report?.advisory_status;
    if (!advisory || advisory.affected !== true) {
        return null;
    }

    const highCount = Number(advisory.high_severity_affected || 0);
    if (highCount <= 0) {
        return null;
    }

    const mitigation = String(advisory.mitigation || "").trim();
    const suffix = mitigation ? ` Mitigation: ${mitigation}` : "";

    return {
        id: "security_advisory_high_severity",
        severity: "error",
        message: `Security advisory: current version is affected by ${highCount} high-severity issue(s).${suffix}`,
        source: "SecurityDoctor",
        ttl_ms: 0,
        dismissible: true,
        action: {
            type: "tab",
            payload: "settings",
            label: "Review"
        }
    };
}

