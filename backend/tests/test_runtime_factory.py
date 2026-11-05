import asyncio
import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

from anvil.api import runtime_factory
from anvil.api import serve
from anvil.api.config import Settings
from anvil.api.database import create_session_factory
from anvil.api.runtime_factory import create_executor_from_environment


def test_windows_executor_rejects_proactor_event_loop_for_async_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(asyncio, "ProactorEventLoop"):
        pytest.skip("Windows event-loop policies are unavailable")

    proactor_loop = asyncio.ProactorEventLoop()
    monkeypatch.setattr(runtime_factory.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_factory.asyncio,
        "get_running_loop",
        lambda: proactor_loop,
    )

    try:
        with pytest.raises(RuntimeError, match="python -m anvil.api.serve"):
            runtime_factory._validate_async_postgres_event_loop()
    finally:
        proactor_loop.close()


def test_windows_api_loop_factory_uses_selector_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve.sys, "platform", "win32")

    loop = serve.api_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_windows_database_engine_rewrites_localhost_to_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_factory.sys, "platform", "win32")
    engine, _ = create_session_factory(
        "postgresql+psycopg://anvil@localhost:5433/anvil_copilot"
    )
    try:
        assert engine.url.host == "127.0.0.1"
    finally:
        engine.dispose()


def test_executor_is_disabled_when_environment_setting_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANVIL_EXECUTOR", raising=False)

    assert create_executor_from_environment() is None


def test_unknown_executor_name_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANVIL_EXECUTOR", "unknown")

    with pytest.raises(ValueError, match="Unsupported ANVIL_EXECUTOR value"):
        create_executor_from_environment()


def test_deepagent_executor_receives_host_tools_and_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANVIL_EXECUTOR", "deepagent")
    monkeypatch.setattr(runtime_factory.sys, "platform", "win32")
    monkeypatch.setattr(runtime_factory, "_validate_async_postgres_event_loop", lambda: None)
    monkeypatch.setattr(
        runtime_factory,
        "get_settings",
        lambda: Settings(
            database_url="postgresql+psycopg://anvil:test%40password@localhost:5433/anvil_copilot"
        ),
    )
    created: dict[str, Any] = {}
    executor_module = types.ModuleType("anvil.runtime.executor")

    class FakeExecutor:
        def __init__(
            self,
            *,
            db_url: str,
            tools_factory: Callable[..., list[Any]],
            push_hook: Callable[..., str],
            report_hook: Callable[..., None],
            verify_hook: Callable[..., list[str]],
        ) -> None:
            created["verify_hook"] = verify_hook
            created["db_url"] = db_url
            created["tools_factory"] = tools_factory
            created["push_hook"] = push_hook
            created["report_hook"] = report_hook

    executor_module.DeepAgentExecutor = FakeExecutor
    websearch_module = types.ModuleType("anvil.runtime.websearch")
    websearch_module.TC_BLOCKLIST = ("blocked.example",)
    websearch_module.env_allowlist = lambda: ()
    websearch_calls: list[tuple[tuple[str, ...], tuple[str, ...], str]] = []

    def make_web_search(*, blocklist: tuple[str, ...], allowlist: tuple[str, ...], run_id: str) -> str:
        websearch_calls.append((blocklist, allowlist, run_id))
        return "web_search_tool"

    websearch_module.make_web_search = make_web_search
    monkeypatch.setitem(sys.modules, "anvil.runtime.executor", executor_module)
    monkeypatch.setitem(sys.modules, "anvil.runtime.websearch", websearch_module)

    from anvil.tools import confluence, evidence, git_push, github, jira

    monkeypatch.setattr(confluence, "CONFLUENCE_TOOLS", ["confluence_tool"])
    monkeypatch.setattr(evidence, "publish_evidence", "publish_evidence_tool")
    monkeypatch.setattr(evidence, "handoff_note", lambda run, note: "formatted handoff")
    monkeypatch.setattr(git_push, "make_git_push", lambda sandbox: lambda repo, branch: {
        "repo": repo,
        "branch": branch,
        "url": f"https://example.test/{branch}",
    })
    monkeypatch.setattr(github, "GITHUB_TOOLS", ["github_tool"])
    drafts: list[dict[str, Any]] = []

    def github_open_pr(repo, branch, title, body, base="main", draft=False):
        drafts.append({"repo": repo, "branch": branch, "title": title, "base": base, "draft": draft})
        if repo == "owner/broken":
            raise RuntimeError("GitHub said no")
        return {"number": 7, "url": f"https://example.test/pull/7?{branch}"}

    monkeypatch.setattr(github, "github_open_pr", github_open_pr)
    monkeypatch.setattr(jira, "JIRA_TOOLS", ["jira_tool"])
    comments: list[tuple[str, str]] = []
    labels: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(jira, "jira_comment", lambda key, body: comments.append((key, body)))
    monkeypatch.setattr(jira, "jira_set_labels", lambda key, add: labels.append((key, add)))

    executor = create_executor_from_environment()
    assert isinstance(executor, FakeExecutor)
    assert created["db_url"].startswith("postgresql://anvil:")
    assert created["db_url"].endswith("@127.0.0.1:5433/anvil_copilot")

    sandbox = object()
    jira_tools = created["tools_factory"](
        sandbox,
        {"run_id": "jira-run", "jira_key": "RTEMS-1", "repo": "owner/repo", "base_ref": "tc1/5776-base"},
    )
    ui_tools = created["tools_factory"](sandbox, {"run_id": "ui-run", "repo": "owner/repo"})
    names = [getattr(t, "__name__", t) for t in jira_tools]
    # Outward tools are bound to the run (ANVIL-32): no repo/base/ticket parameter for the agent.
    assert names == [
        "git_push", "github_open_pr", "github_checks", "github_code_scanning_alerts", "github_pr_state",
        "github_merge", "jira_get", "jira_search", "jira_create", "jira_comment", "jira_transition",
        "jira_set_labels", "confluence_search", "confluence_get", "confluence_write",
        "publish_evidence", "web_search_tool", "kb_search", "lint_changed",
    ]
    ui_names = [getattr(t, "__name__", t) for t in ui_tools]
    assert "jira_comment" not in ui_names and ui_names[-3:] == ["web_search_tool", "kb_search", "lint_changed"]  # no ticket writes
    jira_tools[1]("anvil/rtems-1", "RTEMS-1: fix", "body")  # github_open_pr(branch, title, body)
    assert drafts.pop() == {"repo": "owner/repo", "branch": "anvil/rtems-1", "title": "RTEMS-1: fix",
                            "base": "tc1/5776-base", "draft": False}
    assert jira_tools[0]("anvil/rtems-1")["repo"] == "owner/repo"  # git_push(branch)
    from anvil.tools.run_scope import verify_outcome

    assert created["verify_hook"] is verify_outcome
    # RTEMS-1 binds the TC1 case, whose [web] allow list applies (ANVIL-64); a caseless run gets the env default.
    from anvil.cases import get_case

    assert websearch_calls == [
        (("blocked.example",), get_case("rtems-5776").web_allow, "jira-run"),
        (("blocked.example",), (), "ui-run"),
    ]
    # The handoff opens a draft PR against the run's base so CI runs on the WIP, and links it.
    run = {"repo": "owner/repo", "jira_key": "RTEMS-2", "run_id": "r-9", "base_ref": "tc2/upgrade-base-leon3"}
    assert created["push_hook"](sandbox, run, "anvil/rtems-2-wip") == (
        "https://example.test/pull/7?anvil/rtems-2-wip"
    )
    assert drafts[-1] == {
        "repo": "owner/repo",
        "branch": "anvil/rtems-2-wip",
        "title": "RTEMS-2: WIP handoff (Anvil run r-9)",
        "base": "tc2/upgrade-base-leon3",
        "draft": True,
    }
    # If the PR can't be opened, the handoff still completes with the branch URL.
    assert created["push_hook"](sandbox, {"repo": "owner/broken"}, "anvil/rtems-1-wip") == (
        "https://example.test/anvil/rtems-1-wip"
    )
    assert drafts[-1]["base"] == "main" and drafts[-1]["title"].startswith("ANVIL: WIP handoff")

    created["report_hook"](
        {"jira_key": "RTEMS-1", "run_id": "run-1"},
        {
            "note": {
                "goal": "fix",
                "tried": "test",
                "evidence": "pass",
                "hypothesis": "cause",
                "next_step": "review",
            },
            "wip_branch": "anvil/rtems-1-wip",
            "url": "https://example.test/anvil/rtems-1-wip",
            "sandbox": "anvil-sbx-session",
            "markdown": "fallback",
        },
    )
    created["report_hook"](
        {"jira_key": "RTEMS-2"},
        {"note": None, "wip_branch": None, "url": None, "sandbox": None, "markdown": "runtime formatted note"},
    )
    created["report_hook"]({}, {"markdown": "no Jira issue"})
    assert comments == [
        (
            "RTEMS-1",
            "formatted handoff\n\n**WIP branch:** `anvil/rtems-1-wip`\n"
            "**WIP branch URL:** https://example.test/anvil/rtems-1-wip\n"
            "**Sandbox:** `anvil-sbx-session`",
        ),
        ("RTEMS-2", "runtime formatted note"),
    ]
    assert labels == [
        ("RTEMS-1", ["anvil-needs-human"]),
        ("RTEMS-2", ["anvil-needs-human"]),
    ]


def test_approval_policy_is_default_deny_for_an_unknown_mode():
    """Anything except 'autonomous' gates every outward tool (ANVIL-98)."""
    from anvil.runtime.agent import approval_policy, OUTWARD_TOOLS

    interactive = approval_policy("interactive", "human")
    assert all(interactive.values())
    gated = [t for t in OUTWARD_TOOLS if t != "github_merge"]   # merge honors merge_policy on its own
    for bad in ("", "autonomouz", "AUTO", "none"):
        policy = approval_policy(bad, "agent")
        # unknown mode: every outward tool pauses, exactly like interactive — never default-allow
        assert all(policy[t] for t in gated), bad
    # the only permissive mode is explicit
    assert not any(approval_policy("autonomous", "agent")[t] for t in gated)


def test_github_merge_is_bound_to_the_runs_pr_and_passes_required(monkeypatch):
    """Merge binds to this run's PR (head=work branch, base=run base) on a FRESH run_tools — it
    works after a resume too, when the tool set is rebuilt (ANVIL-98 review)."""
    from anvil.tools import run_scope, github as gh

    calls: dict = {}
    monkeypatch.setattr(gh, "github_merge",
                        lambda repo, pr, method="squash", required=None, work_branch=None, base=None:
                        calls.update(pr=pr, required=required, work_branch=work_branch, base=base)
                        or {"merged": True})

    run = {"run_id": "r1", "repo": "vishal7pandey/rtems", "base_ref": "tc1/5776-base",
           "jira_key": "RTEMS-1", "trigger": "x"}
    # A FRESH run_tools (as a resume would build): merge still binds, nothing is remembered.
    tools = {t.__name__: t for t in run_scope.run_tools(run, object())}
    tools["github_merge"](42)
    assert calls["work_branch"] == "anvil/rtems-1" and calls["base"] == "tc1/5776-base" \
        and calls["pr"] == 42


def test_upload_files_stays_inside_the_workspace():
    """upload_files rejects '..' and paths outside /workspace (ANVIL-98)."""
    from anvil.runtime.sandbox import DockerSandbox
    bad = [(p, b"x") for p in ("rel.txt", "/workspace/../etc/cron", "/etc/passwd",
                              "/workspace/x/../../o", "/.anvil/checks/x.py")]
    s = object.__new__(DockerSandbox)
    s._container = None          # never reached: every path is rejected before the container call
    out = DockerSandbox.upload_files(s, bad)
    assert all(r.error == "invalid_path" for r in out)
    # what IS allowed
    ok = [(p, b"x") for p in ("/workspace/repo/f.py", "/workspace/.anvil/checks/c.py")]
    class _C:
        def exec_run(self, *a, **k):
            class R: exit_code = 0
            return R()
        def put_archive(self, *a, **k): return True
    s._container = _C()
    s._uid = 0
    assert all(r.error is None for r in DockerSandbox.upload_files(s, ok))


def test_extract_archive_refuses_outside_entries(tmp_path):
    """_extract_archive's is_relative_to stops entries that resolve outside (ANVIL-98)."""
    import zipfile
    from anvil.runtime.source import _extract_archive
    import pytest

    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="refusing archive entry"):
        _extract_archive(z, tmp_path / "out")


def test_github_merge_refuses_a_pr_that_is_not_the_runs(monkeypatch):
    """head/base binding: a PR on another branch or base is refused before anything merges."""
    from anvil.tools import github as gh
    from anvil.tools.errors import ToolError
    import pytest

    monkeypatch.setattr(gh, "_gh", lambda *a, **k:
        {"head": {"ref": "anvil/other"}, "base": {"ref": "main"}} if "pulls/7" in str(a[1]) else {})
    monkeypatch.setattr(gh, "full_name", lambda r: "o/r")
    with pytest.raises(ToolError, match="not this run's"):
        gh.github_merge("o/r", 7, work_branch="anvil/rtems-1", base="main")
    with pytest.raises(ToolError, match="not this run's base"):
        gh.github_merge("o/r", 7, work_branch="anvil/other", base="tc1/5776-base")


def test_extract_archive_refuses_sibling_prefix_entries(tmp_path):
    """is_relative_to stops entries that resolve to a sibling dir sharing the root's prefix
    (the old str.startswith check missed these, ANVIL-98)."""
    import zipfile
    from anvil.runtime.source import _extract_archive
    import pytest

    root = tmp_path / "root"
    # "root/../root-x/escape.txt" — starts with the same string prefix but is OUTSIDE the root
    z = tmp_path / "sib.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../root-x/escape.txt", "x")
    with pytest.raises(ValueError, match="refusing archive entry"):
        _extract_archive(z, root)
