"""Run a case's acceptance checks inside the sandbox after the agent has finished (ANVIL-44).

The scripts live in ``anvil/cases/checks/`` on the host. They are uploaded fresh only now, so the
agent never saw them and cannot have edited them, and they run in the repo checkout the agent left.
A check prints one JSON object {"ok": bool, "problems": [...]} and exits 0 when it passes.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

from .spec import CHECKS_DIR, Case

SANDBOX_CHECKS = "/workspace/.anvil/checks/"


def run_acceptance(case: Case, sandbox: Any, repo_dir: str, timeout: int = 600) -> list[str]:
    """Problems found by the case's checks; an empty list means every check passed."""
    if not case.acceptance:
        return []
    scripts = sorted({c.script for c in case.acceptance})
    uploaded = sandbox.upload_files([(SANDBOX_CHECKS + s, (CHECKS_DIR / s).read_bytes()) for s in scripts])
    failed = [r.path for r in uploaded if getattr(r, "error", None)]
    if failed:
        return [f"acceptance checks could not be uploaded: {', '.join(failed)}"]
    problems = []
    for check in case.acceptance:
        cmd = " ".join(shlex.quote(p) for p in ["python3", SANDBOX_CHECKS + check.script, *check.argv(case)])
        res = sandbox.execute(f"cd {shlex.quote(repo_dir)} && {cmd}", timeout=timeout)
        result = _last_json(res.output)
        if res.exit_code == 0 and result.get("ok"):
            continue
        detail = "; ".join(result.get("problems") or []) or res.output.strip()[-400:] or f"exit {res.exit_code}"
        more = result.get("more_problems") or 0
        problems.append(f"acceptance check '{check.name}' failed: {detail}" + (f" (+{more} more)" if more else ""))
    return problems


def _last_json(output: str) -> dict:
    for line in reversed(output.strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}
