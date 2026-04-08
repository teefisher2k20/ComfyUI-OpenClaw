# GitHub Security Residual Wave Reference

Date: 2026-04-08
Scope: Residual GitHub Security findings that remained open after the first `S73-S81` remediation wave.

## 1. Purpose

This reference captures the follow-up repair strategy that was used for the residual GitHub Security findings that still pointed at `main` after the initial CodeQL / Dependabot cleanup wave.

This document is planning-only guidance. It is not itself an implementation record.

Closure note:

- this intake reference is now historical context only
- the residual wave was closed during `S91`, with GitHub `Code scanning` and `Secret scanning` reduced to `0` open findings on 2026-04-08

## 2. Current Residual Findings Baseline

Authenticated GitHub Security review showed this residual baseline at intake:

- Dependabot: `0` open alerts
- Code scanning: `19` open alerts
- Secret scanning: `1` open alert

Residual CodeQL families at intake:

1. `py/path-injection`
   - current concentration: `services/model_manager_transfer.py`
   - count at intake: `9`
2. `py/weak-sensitive-data-hashing`
   - current concentration: `services/redaction.py`, `services/audit.py`, `services/bridge_token_lifecycle.py`
   - count at intake: `3`
3. `py/stack-trace-exposure`
   - current concentration: `connector/platforms/slack_webhook.py`, `connector/platforms/feishu_webhook.py`
   - count at intake: `2`
4. `py/clear-text-logging-sensitive-data`
   - current concentration: `api/bridge.py`, `services/audit.py`
   - count at intake: `2`
5. `py/clear-text-storage-sensitive-data`
   - current concentration: `services/audit.py`
   - count at intake: `1`
6. `py/xml-bomb`
   - current concentration: `connector/platforms/wechat_webhook.py`
   - count at intake: `1`
7. `js/incomplete-sanitization`
   - current concentration: `tests/e2e/specs/notifications.spec.js`
   - count at intake: `1`

Residual secret-scanning family:

- `Tencent WeChat API App ID`
  - count at intake: `1`
  - observed as a historical docs/example-style finding pending confirmation and closure workflow

## 3. Repair Principles

- Prefer real code fixes over dismissals.
- Keep the repair chain scoped to the exact residual findings.
- Add focused guard comments at high-risk fix points.
- Add or extend the smallest credible local regression seam for each repaired family.
- Do not run local CodeQL; GitHub remains the source of truth for scanner retirement.
- For this bug-fixing chain only, do not force unrelated full local test sweeps when no direct seam exists.

## 4. Proposed Execution Order

1. Residual logging / storage / identity-tag cleanup
2. Residual model-transfer path-boundary cleanup
3. Secret-scanning provenance review and closure handling
4. GitHub Security rescan verification and dismiss/close workflow
5. Repository-native CodeQL GitHub Actions baseline activation

## 5. Acceptance Model

Each implementation item in the chain should follow:

1. plan file
2. targeted implementation
3. targeted verification
4. implementation record
5. acceptance commit

The chain-wide source of truth for test procedure remains:

- `tests/TEST_SOP.md`
- `tests/E2E_TESTING_NOTICE.md`
- `tests/E2E_TESTING_SOP.md`

For this residual GitHub Security chain, the explicit local validation strategy is:

- reproduce and pin the exact local seam when possible
- run targeted regressions for the affected contract surface
- do not run local full CodeQL
- use GitHub rescans after push as the authoritative scanner-retirement check

## 6. Closure Policy

- Code scanning alerts should be fixed in code first and then re-checked after GitHub rescans the pushed `main` branch.
- Secret-scanning alerts require explicit provenance review before closure.
- False positives may be dismissed only after the code/test surface is demonstrably safe and the dismissal rationale is recorded.
