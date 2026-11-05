"""ANVIL-32: run-bound outward tools and the host-side outcome check."""

from types import SimpleNamespace

import pytest

from anvil.tools import github, run_scope

RUN = {"run_id": "r1", "repo": "vishal7pandey/rtems", "base_ref": "tc1/5776-base", "jira_key": "RTEMS-1"}


class Sandbox:
    def __init__(self, branch="anvil/rtems-1", changed="cpukit/posix/src/mutexinit.c"):
        self.branch, self.changed = branch, changed

    def execute(self, cmd, timeout=None):
        out = self.branch if "rev-parse" in cmd else self.changed
        return SimpleNamespace(output=out + "\n", exit_code=0)


@pytest.fixture
def gh(monkeypatch):
    state = {"prs": [], "checks": {"state": "success", "failing": []}}
    monkeypatch.setattr(github, "_gh", lambda method, path, **kw: state["prs"])
    monkeypatch.setattr(github, "github_checks", lambda repo, pr, wait_seconds=0, **kw: state["checks"])
    monkeypatch.setattr(run_scope, "case_problems", lambda run, sb: [])  # case checks: test_acceptance.py
    return state


def pr(base="tc1/5776-base"):
    return {"number": 10, "base": {"ref": base}}


def test_verified_when_pr_targets_base_and_ci_is_green(gh):
    gh["prs"] = [pr()]
    assert run_scope.verify_outcome(RUN, Sandbox()) == []


@pytest.mark.parametrize("prs,checks,expect", [
    ([pr("main")], {"state": "success"}, "targets `main`"),                 # rehearsal 6d9317d3
    ([pr()], {"state": "none", "failing": []}, "CI on PR #10 is `none`"),   # no CI ran
    ([pr()], {"state": "failure", "failing": ["build-test"]}, "build-test"),
    ([], {}, "no pull request was opened"),                                 # code changed, no PR
])
def test_not_verified(gh, prs, checks, expect):
    gh["prs"], gh["checks"] = prs, checks
    problems = run_scope.verify_outcome(RUN, Sandbox())
    assert problems and any(expect in p for p in problems)


def test_nothing_to_verify_when_nothing_changed(gh):
    assert run_scope.verify_outcome(RUN, Sandbox(changed="")) == []  # e.g. an investigation run


def test_run_tools_have_no_repo_base_or_ticket_parameters(monkeypatch):
    import inspect

    monkeypatch.setattr(run_scope.git_push_module, "make_git_push", lambda sb: lambda repo, branch: {})
    tools = run_scope.run_tools(RUN, Sandbox())
    for tool in tools:
        if tool.__module__ == "anvil.tools.run_scope":
            assert not {"repo", "base", "key"} & set(inspect.signature(tool).parameters), tool.__name__
    assert "jira_comment" not in [t.__name__ for t in run_scope.run_tools({**RUN, "jira_key": None}, Sandbox())]


# -- ANVIL-99: verification fails closed and looks at the right PR -------------------------------------------

class GitFails:
    """git cannot be read (corrupt repo, missing origin ref, broken sandbox): used to read as 'nothing changed'."""

    def execute(self, cmd, timeout=None):
        return SimpleNamespace(output="fatal: bad revision 'origin/x'\n", exit_code=128)


class Heads(Sandbox):
    SHA = "a" * 40

    def execute(self, cmd, timeout=None):
        if "rev-parse HEAD" in cmd:
            return SimpleNamespace(output=self.SHA + "\n", exit_code=0)
        return super().execute(cmd, timeout)


def pr_at(sha, state="open"):
    return {"number": 10, "state": state, "base": {"ref": "tc1/5776-base"}, "head": {"sha": sha}}


def test_a_failed_git_command_is_a_failed_verification_not_nothing_changed(gh):
    gh["prs"] = []
    problems = run_scope.verify_outcome(RUN, GitFails())
    assert problems and all("could not verify the run" in p and "git exit 128" in p for p in problems)


def test_a_pr_that_does_not_contain_the_runs_last_commit_is_flagged(gh):
    gh["prs"] = [pr_at("b" * 40)]
    problems = run_scope.verify_outcome(RUN, Heads())
    assert any("later work was never pushed" in p and "bbbbbbb" in p and "aaaaaaa" in p for p in problems)


def test_the_open_pr_with_the_runs_head_decides_not_a_stale_closed_one(gh):
    gh["prs"] = [pr_at("c" * 40, state="closed"), pr_at(Heads.SHA, state="open")]   # state=all lists both
    assert run_scope.verify_outcome(RUN, Heads()) == []
