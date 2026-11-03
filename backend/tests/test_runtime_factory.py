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
        ) -> None:
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
        {"run_id": "jira-run", "jira_key": "RTEMS-1"},
    )
    ui_tools = created["tools_factory"](sandbox, {"run_id": "ui-run"})
    assert jira_tools[:4] == [
        "jira_tool",
        "github_tool",
        "confluence_tool",
        "publish_evidence_tool",
    ]
    assert callable(jira_tools[4])
    assert jira_tools[5] == "web_search_tool"
    assert ui_tools[:4] == jira_tools[:4]
    assert ui_tools[5] == "web_search_tool"
    assert websearch_calls == [
        (("blocked.example",), "jira-run"),
        ((), "ui-run"),
    ]
    assert created["push_hook"](sandbox, {"repo": "owner/repo"}, "anvil/rtems-1-wip") == (
        "https://example.test/anvil/rtems-1-wip"
    )

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
