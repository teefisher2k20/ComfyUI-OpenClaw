# Support Policy

## Support Tiers

### Tier 1: Fully Supported

**Definition**: Validated by CI/CD or core maintainers. Critical bugs block releases.

- **Environment**: Linux (Ubuntu 22.04), Windows 11.
- **Python**: 3.10, 3.11.
- **ComfyUI host**: current compatibility-matrix reference anchor and close neighbors.
- **Frontend host**: current standalone frontend reference anchor for the sidebar extension contract.

### Tier 2: Best Effort

**Definition**: Should work, but not actively validated. Bugs fixed as resources allow.

- **Environment**: macOS, older Windows versions.
- **Python**: 3.12.
- **ComfyUI**: nightly builds and farther-from-anchor upstream drift.
- **Desktop host**: desktop bundle variants outside the current recorded desktop anchor, including cases where the embedded frontend lags standalone frontend.

### Tier 3: Unsupported

**Definition**: Known to be incompatible or end-of-life.

- **Python**: < 3.9.
- **OS**: Windows 7/8.

## Deprecation Policy

- **Notice Period**: Breaking changes will be announced 1 minor version in advance.
- **Legacy Support**: Deprecated features (e.g., legacy `MOLTBOT_` env vars) are supported for at least 1 major version cycle.

## Compatibility Anchor Policy

- The authoritative compatibility reference points are recorded in [`compatibility_matrix.md`](/mnt/c/Users/Ray/Documents/我的專案/ComfyUI-OpenClaw/docs/release/compatibility_matrix.md).
- `ComfyUI`, standalone `ComfyUI_frontend`, and `desktop` are tracked as separate host surfaces.
- Desktop should not be assumed to match standalone frontend HEAD; the embedded frontend version may intentionally lag and must be evaluated against its own recorded bundle anchor.
- Upstream reference refreshes should update the matrix anchors before being treated as the new default support baseline.

## Reporting Issues

Please report issues on [GitHub Issues](https://github.com/rookiestar28/ComfyUI-OpenClaw/issues).
Include:

- OS and Python version
- ComfyUI version
- Workflow JSON (redacted)
- Logs (redacted)
