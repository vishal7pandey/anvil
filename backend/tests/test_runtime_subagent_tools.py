"""Security regression: subagents must not reach outward host tools (the approval gate is on the
main agent only). Inspects the compiled subagent graphs behind the `task` tool."""

from unittest.mock import MagicMock

from anvil.runtime.agent import OUTWARD_TOOLS, RunConfig, build_agent
from anvil.runtime.sandbox import DockerSandbox


def _tool(name):
    def f(x: str) -> str:
        return x
    f.__name__ = name
    f.__doc__ = f"{name} stub"
    return f


def _tools_of(graph):
    names = set()
    for node in graph.nodes.values():
        bound = getattr(node, "bound", None)
        names |= set(getattr(bound, "tools_by_name", {}) or {})
    return names


def test_no_subagent_can_reach_outward_tools():
    outward = [_tool(n) for n in OUTWARD_TOOLS]
    g = build_agent(RunConfig(run_id="x", repo="r", mode="interactive"), DockerSandbox(MagicMock()), tools=outward)
    main_tools = _tools_of(g)
    assert set(OUTWARD_TOOLS) <= main_tools                     # main agent has them (behind interrupt_on)
    task = g.nodes["tools"].bound.tools_by_name["task"]
    fn = getattr(task, "func", None) or getattr(task, "coroutine", None)
    subagents = {}
    for cell in fn.__closure__ or ():
        if isinstance(cell.cell_contents, dict):
            for name, spec in cell.cell_contents.items():
                runnable = spec.get("runnable") if isinstance(spec, dict) else spec
                if hasattr(runnable, "nodes"):
                    subagents[name] = _tools_of(runnable)
    assert {"investigator", "builder", "reviewer", "general-purpose"} <= set(subagents)
    for name, tools in subagents.items():
        assert not (tools & set(OUTWARD_TOOLS)), f"{name} can reach {tools & set(OUTWARD_TOOLS)}"
        assert "handoff" not in tools and "execute" in tools
