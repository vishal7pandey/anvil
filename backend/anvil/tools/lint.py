"""lint_changed: the one way an agent runs pre-commit (ANVIL-74).

The main prompt used to order `pre-commit run --all-files`, and the sandbox refused exactly that
(ANVIL-41: whole-tree hooks over RTEMS outlast the run budget). The agent was told to do something
and then punished for it. Now the prompt and the skills name this tool, which works out the changed
files itself, so the agent never has to build that list by hand or reach for `--all-files`.

Changed = files that differ from the merge-base with the run's base branch, in commits, the index
or the working tree (added, copied, modified or renamed; deletions have nothing to lint). New files
count once they are `git add`ed; untracked build leftovers (config.ini, build/) never do, the same
rule `verify_outcome` uses.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable

MAX_OUTPUT = 6000
NO_CONFIG = "__ANVIL_NO_PRECOMMIT_CONFIG__"
NO_FILES = "__ANVIL_NO_CHANGED_FILES__"
FILES_START, FILES_END = "__ANVIL_FILES__", "__ANVIL_END_FILES__"


def lint_command(path: str, base: str, exclude: list[str] | None = None) -> str:
    """Shell for the sandbox: list the changed files, then run the hooks on exactly those."""
    p, b = shlex.quote(path), shlex.quote(f"origin/{base}")
    prefixes = [x for x in (e.strip().removeprefix("./") for e in exclude or []) if x]
    # NUL-separated everywhere (ANVIL-100 #23): git -z + grep -z + sort -z + xargs -0 survive any
    # filename; the `--` before the file list keeps a file named like an option (e.g. --foo) from
    # being parsed as one.
    drop = (f" | grep -vz -E {shlex.quote('^(' + '|'.join(re.escape(x) for x in prefixes) + ')')}"
            if prefixes else "")
    return (
        f"cd {p} || exit 2; "
        f"if [ ! -f .pre-commit-config.yaml ]; then echo {NO_CONFIG}; exit 0; fi; "
        f"mb=$(git merge-base HEAD {b}) || exit 2; "
        f"git diff -z --name-only --diff-filter=ACMR \"$mb\"{drop} | sort -zu > /tmp/anvil-lint-files; "
        f"if [ ! -s /tmp/anvil-lint-files ]; then echo {NO_FILES}; exit 0; fi; "
        f"echo {FILES_START}; tr '\\0' '\\n' < /tmp/anvil-lint-files; echo {FILES_END}; "
        f"xargs -0 -a /tmp/anvil-lint-files pre-commit run --files --"
    )


def parse(output: str, exit_code: int) -> dict[str, Any]:
    if NO_CONFIG in output:
        return {"ok": True, "files": [], "note": "no .pre-commit-config.yaml in this repo: nothing to run"}
    if NO_FILES in output:
        return {"ok": True, "files": [], "note": "no changed files against the base branch: nothing to lint"}
    files: list[str] = []
    if FILES_START in output and FILES_END in output:
        block = output.split(FILES_START, 1)[1].split(FILES_END, 1)[0]
        files = [f for f in block.splitlines() if f.strip()]
        output = output.split(FILES_END, 1)[1]
    result: dict[str, Any] = {"ok": exit_code == 0, "files": files, "exit_code": exit_code,
                              "output": output.strip()[-MAX_OUTPUT:]}
    if exit_code != 0 and "files were modified by this hook" in output:
        result["note"] = ("hooks rewrote some files (formatting): review `git diff`, keep the changes you agree "
                          "with, and call lint_changed again")
    return result


def make_lint_changed(run: dict[str, Any], sandbox: Any) -> Callable[..., dict[str, Any]]:
    base = run.get("base_ref") or "main"

    def lint_changed(exclude: list[str] | None = None) -> dict[str, Any]:
        """Run the repo's pre-commit hooks on the files you changed against the base branch.

        Use this before committing instead of calling pre-commit yourself. `git add` new files first
        so they are included. `exclude`: path prefixes to leave out, e.g. ["lwip/"] for an imported
        upstream tree that keeps upstream formatting. Returns ok, the files checked, and the hook
        output when something failed.
        """
        from anvil.runtime.executor import repo_path  # at call time, like verify_outcome

        res = sandbox.execute(lint_command(repo_path(run["repo"]), base, exclude), timeout=900)
        return parse(res.output or "", res.exit_code if res.exit_code is not None else 1)

    return lint_changed
