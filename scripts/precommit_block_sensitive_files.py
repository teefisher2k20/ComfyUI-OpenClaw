from __future__ import annotations

import subprocess
import sys

SENSITIVE_PATH_PREFIXES = (
    ".planning/",
    ".planning\\",
)


def _get_staged_paths() -> list[str]:
    res = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        # If git isn't available for some reason, do not block the commit.
        # This hook is a safety net, not a hard dependency.
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _get_blocked_paths(staged: list[str]) -> list[str]:
    blocked: list[str] = []
    for path in staged:
        if path.startswith(SENSITIVE_PATH_PREFIXES):
            blocked.append(path)
    return blocked


def main() -> int:
    staged = _get_staged_paths()
    blocked = _get_blocked_paths(staged)

    if not blocked:
        return 0

    msg = "\n".join(
        [
            "[OpenClaw] Commit blocked: sensitive files are staged.",
            "",
            "These files must never be committed to the public repo:",
            *[f"  - {p}" for p in blocked],
            "",
            "Fix:",
            "  git restore --staged .planning",
            "",
            "If you intentionally need an internal-only commit, use a separate private remote/repo.",
        ]
    )
    print(msg, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
