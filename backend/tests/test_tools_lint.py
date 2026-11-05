"""lint_changed (ANVIL-74): the one way the agent runs pre-commit. The command itself was checked
live in the sandbox image (staged new file in, untracked leftover out, excluded tree out)."""

from types import SimpleNamespace

from anvil.runtime import prompts
from anvil.runtime.sandbox import refusal
from anvil.tools.lint import FILES_END, FILES_START, NO_CONFIG, NO_FILES, lint_command, make_lint_changed, parse


class FakeSandbox:
    def __init__(self, output, exit_code=0):
        self.output, self.exit_code, self.commands = output, exit_code, []

    def execute(self, command, timeout=None):
        self.commands.append(command)
        return SimpleNamespace(output=self.output, exit_code=self.exit_code)


RUN = {"run_id": "r1", "repo": "vishal7pandey/rtems", "base_ref": "tc1/5776-base"}


def test_command_lints_changed_files_against_the_run_base_never_all_files():
    cmd = lint_command("/workspace/rtems", "tc1/5776-base")
    assert "git merge-base HEAD origin/tc1/5776-base" in cmd
    assert "--diff-filter=ACMR" in cmd and "pre-commit run --files" in cmd
    assert "--all-files" not in cmd and refusal(cmd) is None


def test_exclude_prefixes_are_escaped_and_dot_slash_tolerant():
    cmd = lint_command("/workspace/rtems-lwip", "main", ["./lwip/", ".github/", " "])
    assert r"grep -vz -E '^(lwip/|\.github/)'" in cmd


def test_parse_reports_files_and_failures():
    out = f"noise\n{FILES_START}\ncpukit/a.c\ntestsuites/b.c\n{FILES_END}\nclang-format....Failed\n"
    res = parse(out, 1)
    assert res["ok"] is False and res["files"] == ["cpukit/a.c", "testsuites/b.c"]
    assert "Failed" in res["output"] and FILES_START not in res["output"]
    rewritten = parse(f"{FILES_START}\na.c\n{FILES_END}\n- files were modified by this hook\n", 1)
    assert "call lint_changed again" in rewritten["note"]


def test_parse_no_config_and_nothing_changed_are_ok():
    assert parse(NO_CONFIG + "\n", 0)["ok"] is True
    assert parse(NO_FILES + "\n", 0) == {"ok": True, "files": [], "note": "no changed files against the base branch: nothing to lint"}


def test_tool_is_bound_to_the_run_repo_and_base():
    sb = FakeSandbox(f"{FILES_START}\ncpukit/a.c\n{FILES_END}\nPassed\n")
    res = make_lint_changed(RUN, sb)(exclude=["lwip/"])
    assert res == {"ok": True, "files": ["cpukit/a.c"], "exit_code": 0, "output": "Passed"}
    assert sb.commands[0].startswith("cd /workspace/rtems ") and "origin/tc1/5776-base" in sb.commands[0]


def test_prompt_names_the_tool_and_never_orders_all_files():
    assert "lint_changed" in prompts.MAIN and "--all-files" not in prompts.MAIN
    assert "lint_changed" in refusal("pre-commit run --all-files")
