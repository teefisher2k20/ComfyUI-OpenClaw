#!/usr/bin/env python3
"""
R156: verify coverage + mutation governance baseline configuration.

This script is intentionally stdlib-only so it can run early in local/full-test
gates before any optional tooling is installed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

MIN_COVERAGE_FAIL_UNDER = 35.0
SMOKE_MUTATION_THRESHOLD = 20.0
EXTENDED_MUTATION_THRESHOLD = 80.0


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _extract_toml_section(text: str, header: str) -> Optional[str]:
    pattern = re.compile(rf"(?ms)^\[{re.escape(header)}\]\s*$\n(?P<body>.*?)(?=^\[|\Z)")
    match = pattern.search(text)
    if not match:
        return None
    return match.group("body")


def _extract_float_assignment(section_text: str, key: str) -> Optional[float]:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        section_text,
    )
    if not match:
        return None
    return float(match.group(1))


def _extract_bool_assignment(section_text: str, key: str) -> Optional[bool]:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(true|false)\s*$", section_text)
    if not match:
        return None
    return match.group(1) == "true"


def _extract_python_constant(text: str, name: str) -> Optional[float]:
    match = re.search(
        rf"(?m)^\s*{re.escape(name)}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$", text
    )
    if not match:
        return None
    return float(match.group(1))


def _require_phrase(text: str, phrase: str, failures: List[str], label: str) -> None:
    if phrase not in text:
        failures.append(f"{label}: missing required phrase: {phrase}")


def verify_governance(
    *,
    pyproject_path: Path,
    adversarial_gate_path: Path,
    test_sop_path: Path,
    survivor_allowlist_path: Path,
) -> List[str]:
    failures: List[str] = []

    pyproject_text = _read_text(pyproject_path)
    report_section = _extract_toml_section(pyproject_text, "tool.coverage.report")
    if report_section is None:
        failures.append("pyproject: missing [tool.coverage.report] section")
    else:
        fail_under = _extract_float_assignment(report_section, "fail_under")
        if fail_under is None:
            failures.append("pyproject: missing coverage fail_under")
        elif fail_under < MIN_COVERAGE_FAIL_UNDER:
            failures.append(
                "pyproject: coverage fail_under "
                f"{fail_under} below minimum baseline {MIN_COVERAGE_FAIL_UNDER}"
            )

        show_missing = _extract_bool_assignment(report_section, "show_missing")
        if show_missing is not True:
            failures.append("pyproject: coverage show_missing must be true")

        skip_covered = _extract_bool_assignment(report_section, "skip_covered")
        if skip_covered is not True:
            failures.append("pyproject: coverage skip_covered must be true")

    gate_text = _read_text(adversarial_gate_path)
    smoke_threshold = _extract_python_constant(gate_text, "SMOKE_MUTATION_THRESHOLD")
    if smoke_threshold != SMOKE_MUTATION_THRESHOLD:
        failures.append(
            "adversarial gate: smoke mutation threshold drifted "
            f"(expected {SMOKE_MUTATION_THRESHOLD}, got {smoke_threshold})"
        )

    extended_threshold = _extract_python_constant(
        gate_text, "EXTENDED_MUTATION_THRESHOLD"
    )
    if extended_threshold != EXTENDED_MUTATION_THRESHOLD:
        failures.append(
            "adversarial gate: extended mutation threshold drifted "
            f"(expected {EXTENDED_MUTATION_THRESHOLD}, got {extended_threshold})"
        )

    test_sop_text = _read_text(test_sop_path)
    _require_phrase(
        test_sop_text,
        "R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)",
        failures,
        "tests/TEST_SOP.md",
    )
    _require_phrase(
        test_sop_text,
        "global score threshold (`>= 80%` unless explicitly overridden)",
        failures,
        "tests/TEST_SOP.md",
    )
    _require_phrase(
        test_sop_text,
        "coverage governance check (`scripts/verify_quality_governance.py`)",
        failures,
        "tests/TEST_SOP.md",
    )

    if not survivor_allowlist_path.is_file():
        failures.append(
            "mutation governance: missing tests/mutation_survivor_allowlist.json"
        )
    else:
        try:
            payload = json.loads(_read_text(survivor_allowlist_path))
        except json.JSONDecodeError as exc:
            failures.append(
                f"mutation governance: invalid survivor allowlist JSON: {exc}"
            )
        else:
            if not isinstance(payload, dict) or not isinstance(
                payload.get("entries", []), list
            ):
                failures.append(
                    "mutation governance: survivor allowlist must be an object with an entries list"
                )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify repository coverage and mutation governance baselines."
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml",
    )
    parser.add_argument(
        "--adversarial-gate",
        default="scripts/run_adversarial_gate.py",
        help="Path to the adversarial gate runner",
    )
    parser.add_argument(
        "--test-sop",
        default="tests/TEST_SOP.md",
        help="Path to the main test SOP",
    )
    parser.add_argument(
        "--mutation-survivor-allowlist",
        default="tests/mutation_survivor_allowlist.json",
        help="Path to the mutation survivor allowlist JSON",
    )
    args = parser.parse_args()

    failures = verify_governance(
        pyproject_path=Path(args.pyproject),
        adversarial_gate_path=Path(args.adversarial_gate),
        test_sop_path=Path(args.test_sop),
        survivor_allowlist_path=Path(args.mutation_survivor_allowlist),
    )
    if failures:
        for failure in failures:
            print(f"GOVERNANCE-FAIL: {failure}")
        return 1

    print(
        "GOVERNANCE-PASS: coverage fail_under/show_missing/skip_covered and "
        "mutation thresholds are aligned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
