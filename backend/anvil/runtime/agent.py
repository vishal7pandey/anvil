"""Build the Deep Agents graph for one Anvil run."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_openai import ChatOpenAI

from anvil.runtime import prompts
from anvil.runtime.compaction import model_profile, observed_summarization
from anvil.runtime.sandbox import DockerSandbox

Mode = Literal["interactive", "autonomous"]
MergePolicy = Literal["human", "agent-after-approval", "agent"]

# Outward-facing host tools (devin, ANVIL-13). Gated by policy; anything not listed runs freely.
ARTIFACTS_ROOT = "/workspace/.anvil"

OUTWARD_TOOLS = ("git_push", "github_open_pr", "github_merge", "jira_transition", "jira_comment",
                 "jira_set_labels", "jira_create", "confluence_write", "publish_evidence")


@dataclass
class RunConfig:
    run_id: str
    repo: str
    mode: Mode = "interactive"
    merge_policy: MergePolicy = "human"
    gateway_url: str = field(default_factory=lambda: os.environ.get("ANVIL_GATEWAY_URL", "http://127.0.0.1:4000/v1"))
    gateway_key: str = field(default_factory=lambda: os.environ.get("LITELLM_MASTER_KEY", "sk-anvil-local"))
    main_model: str = "anvil-main"
    fast_model: str = "anvil-fast"


def approval_policy(mode: Mode, merge_policy: MergePolicy) -> dict[str, bool]:
    """Which tool calls pause for a human (docs/02-architecture.md "Safety rails").

    Default-deny: only an explicit "autonomous" drops the gates — an unknown mode value pauses
    every outward tool (ANVIL-98), never silently allows them."""
    if mode == "autonomous":
        policy = {name: False for name in OUTWARD_TOOLS}
    else:
        policy = {name: True for name in OUTWARD_TOOLS}
    # Merge: never without a human unless the run says the agent may merge on its own.
    policy["github_merge"] = merge_policy != "agent"
    return policy


def make_handoff_tool(on_handoff: Callable[[dict[str, str]], None]) -> Callable[..., str]:
    def handoff(goal: str, tried: str, evidence: str, hypothesis: str, next_step: str) -> str:
        """Stop and hand the task back to a human engineer.

        Use when you are stuck: the same failure three times, no progress, or the task needs a
        decision you should not make. Your work so far is saved on a WIP branch.

        Args:
            goal: What the task was.
            tried: What you tried, in order.
            evidence: Commands run and their key output.
            hypothesis: Your best current explanation.
            next_step: What a human should try next.
        """
        on_handoff({"goal": goal, "tried": tried, "evidence": evidence,
                    "hypothesis": hypothesis, "next_step": next_step})
        return "Handoff recorded. Stop working on this task now and summarise in one paragraph."

    return handoff


def subagent_specs(fast_model: Any) -> list[dict[str, Any]]:
    return [
        {"name": "investigator", "description": "Finds the root cause of a defect in the code base. Read-only.",
         "system_prompt": prompts.INVESTIGATOR, "tools": []},
        {"name": "builder", "description": "Builds the RTEMS BSP and runs tests on the simulator; reports results.",
         "system_prompt": prompts.BUILDER, "model": fast_model, "tools": []},
        {"name": "reviewer", "description": "Reviews the current git diff like a senior maintainer.",
         "system_prompt": prompts.REVIEWER, "tools": []},
        # deepagents adds a default "general-purpose" subagent that inherits every tool; replace it
        # with one that has only the sandbox (same reason as above).
        {"name": "general-purpose", "description": "General helper for self-contained research or "
         "edits inside the sandbox. Cannot push, open PRs, merge or write to Jira/Confluence.",
         "system_prompt": "You are a careful assistant working inside a Linux sandbox. Do the task and "
                          "report concisely what you did and found.", "tools": []},
    ]


def _model(config: RunConfig, alias: str) -> ChatOpenAI:
    # A profile (ANVIL_CONTEXT_WINDOW) switches compaction to fraction thresholds (ANVIL-93).
    return ChatOpenAI(model=alias, base_url=config.gateway_url, api_key=config.gateway_key, stream_usage=True,
                      extra_body={"metadata": {"spend_logs_metadata": {"run_id": config.run_id}}},
                      profile=model_profile())


def build_agent(config: RunConfig, sandbox: DockerSandbox, *, tools: Sequence[Any] = (),
                on_handoff: Callable[[dict[str, str]], None] | None = None,
                on_compact: Callable[[dict[str, Any]], None] | None = None,
                skills: list[str] | None = None,
                checkpointer: Any = None, store: Any = None) -> Any:
    handoffs: list[dict[str, str]] = []
    handoff = make_handoff_tool(on_handoff or handoffs.append)

    # artifacts_root: deepagents captures large command output under <root>/large_tool_results/.
    # The default "/" isn't writable for the sandbox's non-root user, so every execute failed to capture.
    routes: dict[str, Any] = {}
    if store is not None:
        routes["/memories/"] = StoreBackend(namespace=lambda _rt: ("anvil", "memories", config.repo), store=store)
    backend: Any = CompositeBackend(default=sandbox, routes=routes, artifacts_root=ARTIFACTS_ROOT)

    tool_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
    fast = _model(config, config.fast_model)
    # "tools": [] matters: without it deepagents subagents inherit the main agent's tools, and the
    # approval gate (interrupt_on) lives only on the main agent. A subagent could then push or merge
    # unapproved. Subagents keep shell + filesystem through the sandbox backend's middleware.
    subagents = subagent_specs(fast)

    # No FilesystemPermission here: deepagents rejects path permissions on backends with `execute`
    # (a shell can bypass them). "Never edit .github/workflows" is enforced where it can't be
    # bypassed: git_push refuses such diffs, and the GitHub token has no Workflows permission.
    main = _model(config, config.main_model)
    # Same name as deepagents' default, so it replaces it in place: same compaction, now reported (ANVIL-93).
    compaction = [observed_summarization(main, backend, on_compact)] if on_compact else []
    return create_deep_agent(
        model=main,
        tools=[*tools, handoff],
        # write_todos: deepagents adds it only for its Codex profile; our gateway alias needs it explicitly.
        middleware=[TodoListMiddleware(), *compaction],
        system_prompt=prompts.MAIN,
        skills=skills,
        subagents=subagents,
        backend=backend,
        interrupt_on={name: gate for name, gate in approval_policy(config.mode, config.merge_policy).items()
                      if name in tool_names},
        checkpointer=checkpointer,
        store=store,
        name="anvil",
    )
