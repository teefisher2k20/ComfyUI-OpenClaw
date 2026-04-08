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

## 3. Final State

- `S88` is completed. Authenticated GitHub verification confirmed the repaired findings retired or were closed with explicit rationale after the advanced CodeQL switch.
- `S89` is completed. The audit and bridge identifier cleanup removed the true residual sinks and reduced the remaining audit findings to GitHub-managed false positives.
- `S90` is completed. The model-manager path-boundary proof stayed fail-closed, and the remaining `py/path-injection` alerts were retired through authenticated false-positive dismissal after the advanced rescan.
- `S91` is completed. GitHub code scanning default setup was switched off, the committed `.github/workflows/codeql.yml` run on `main` succeeded, the final residual CodeQL alerts were dismissed with recorded rationale, and the historical secret-scanning docs example was resolved.

## 4. Execution Rules

- Add hotspot comments at every high-risk repair seam.
- Update or add the smallest credible regression seam for each fix.
- Use targeted local tests for the changed contract surface.
- Use GitHub rescans after push as the source of truth for code-scanning retirement.
- Do not dismiss unresolved true positives.
- Do not close the historical secret-scanning alert until provenance and placeholder status are fully confirmed.

## 5. Closure Evidence

Authenticated GitHub evidence on 2026-04-08:

- `GET /code-scanning/default-setup` now returns `state=not-configured`
- the in-repo `CodeQL` workflow completed successfully on `main` head `0abdafab73e42ea4503992e7bc8cf76ef05fae03`
- `GET /code-scanning/alerts?state=open` now returns `0`
- secret-scanning alert `#1` is now `resolved` with `resolution=false_positive`

## 6. Expected End State

This chain is now complete:

- the remaining code-scanning alerts were either fixed in code or dismissed with explicit false-positive rationale
- the historical secret-scanning alert was closed with recorded provenance evidence
- the repository now relies on the committed advanced CodeQL workflow rather than GitHub default setup
- the final GitHub-side actions were performed with the required repository-administration and alert-write permissions
