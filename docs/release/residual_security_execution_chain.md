# Residual Security Execution Chain

Date: 2026-04-08
Roadmap chain: `S88 -> S89 -> S90 -> S91`

## 1. Purpose

This document mirrors the active execution chain for the remaining GitHub Security findings after the first remediation wave and the initial residual follow-up fixes.

It is intended as a repo-visible planning reference in `docs/` and should stay aligned with `.planning/roadmap.md` and `.planning/roadmap/open/SECURITY_OPEN.md`.

## 2. Active Item Order

1. `S88`: GitHub Security residual alert verification, dismissal, and closure execution wave
2. `S89`: Residual audit and bridge alert retirement sweep
3. `S90`: Residual model-manager path-boundary false-positive retirement wave
4. `S91`: GitHub code-scanning mode switch and final residual alert closure wave

## 3. Current State

- `S88` remains the umbrella verification item until GitHub rescans, final dismissals, and the secret-scanning closure workflow are complete.
- `S89` is completed. The audit and bridge identifier cleanup reduced the residual alert set but left two GitHub-managed follow-up alerts in `services/audit.py`.
- `S90` is completed. The model-manager path-boundary proof was strengthened, but the remaining `services/model_manager_transfer.py` findings did not retire after rescan and therefore move to authenticated dismissal review.
- `S91` is now the only active execution lane. It is a GitHub-side closure wave that requires repository administration access for the CodeQL mode switch and alert-write capability for final dismissals and secret-alert closure.

## 4. Execution Rules

- Add hotspot comments at every high-risk repair seam.
- Update or add the smallest credible regression seam for each fix.
- Use targeted local tests for the changed contract surface.
- Use GitHub rescans after push as the source of truth for code-scanning retirement.
- Do not dismiss unresolved true positives.
- Do not close the historical secret-scanning alert until provenance and placeholder status are fully confirmed.

## 5. Current Remaining Risk Shape

After the latest rescans, the remaining residual families are:

- `2` audit-related CodeQL alerts still attached to `services/audit.py`
- `8` model-manager `py/path-injection` alerts in `services/model_manager_transfer.py`
- `1` historical secret-scanning alert tied to a WeChat App ID-shaped documentation example
- GitHub `default setup` is still enabled, so the committed in-repo `codeql.yml` workflow is not yet the authoritative scanner baseline

## 6. Expected End State

This chain is complete only when:

- the remaining code-scanning alerts are either fixed in code or dismissed with explicit false-positive rationale
- the secret-scanning alert is manually closed with recorded provenance evidence
- the repository uses the committed advanced CodeQL workflow as its authoritative scanner baseline
- the final GitHub-side actions are performed with the required repository-administration and alert-write permissions
