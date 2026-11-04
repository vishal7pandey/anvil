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
    websearch_calls: list[tuple[tuple[str, ...], str]] = []

    def make_web_search(*, blocklist: tuple[str, ...], run_id: str) -> str:
        websearch_calls.append((blocklist, run_id))
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
        "jira_set_labels", "confluence_tool", "publish_evidence_tool", "web_search_tool",
    ]
    ui_names = [getattr(t, "__name__", t) for t in ui_tools]
    assert "jira_comment" not in ui_names and ui_names[-1] == "web_search_tool"  # no ticket, no ticket writes
    jira_tools[1]("anvil/rtems-1", "RTEMS-1: fix", "body")  # github_open_pr(branch, title, body)
    assert drafts.pop() == {"repo": "owner/repo", "branch": "anvil/rtems-1", "title": "RTEMS-1: fix",
                            "base": "tc1/5776-base", "draft": False}
    assert jira_tools[0]("anvil/rtems-1")["repo"] == "owner/repo"  # git_push(branch)
    from anvil.tools.run_scope import verify_outcome

    assert created["verify_hook"] is verify_outcome
    assert websearch_calls == [
        (("blocked.example",), "jira-run"),
        (("blocked.example",), "ui-run"),
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
