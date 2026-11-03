from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from anvil.runtime.agent import approval_policy
from anvil.runtime.events import EventMapper


def types_of(events):
    return [e["type"] for e in events]


def test_token_delta_from_messages_mode():
    m = EventMapper("r1")
    ev = m.map(((), "messages", (AIMessageChunk(content="Hel"), {})))
    assert ev[0]["type"] == "message.delta" and ev[0]["data"]["text"] == "Hel"
    assert ev[0]["seq"] == 1 and ev[0]["run_id"] == "r1" and ev[0]["agent"] == "main"


def test_execute_call_and_result_become_tool_and_sandbox_events():
    m = EventMapper("r1")
    call = AIMessage(content="", tool_calls=[{"id": "c1", "name": "execute", "args": {"command": "./waf"}}],
                     usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                     response_metadata={"model_name": "anvil-main"})
    ev = m.map(((), "updates", {"model": {"messages": [call]}}))
    assert types_of(ev) == ["tool.call", "usage"]
    assert ev[1]["data"] == {"model_alias": "anvil-main", "input_tokens": 10, "output_tokens": 5}

    result = ToolMessage(content="build ok\n", tool_call_id="c1", name="execute", artifact={"exit_code": 0})
    ev = m.map(((), "updates", {"tools": {"messages": [result]}}))
    assert types_of(ev) == ["tool.result", "sandbox.exec"]
    assert ev[1]["data"]["cmd"] == "./waf" and ev[1]["data"]["exit_code"] == 0
    assert [e["seq"] for e in ev] == [3, 4]


def test_plan_subagent_and_interrupt():
    m = EventMapper("r1")
    ev = m.map(((), "updates", {"tools": {"todos": [{"content": "reproduce", "status": "in_progress"}]}}))
    assert ev[0]["type"] == "plan" and ev[0]["data"]["todos"][0]["status"] == "in_progress"

    task = AIMessage(content="", tool_calls=[{"id": "t1", "name": "task",
                                              "args": {"subagent_type": "builder", "description": "build erc32"}}])
    ev = m.map(((), "updates", {"model": {"messages": [task]}}))
    assert types_of(ev) == ["tool.call", "subagent"] and ev[1]["data"]["phase"] == "start"

    intr = SimpleNamespace(id="i1", value={"action_requests": [{"name": "git_push", "args": {"branch": "anvil/x"},
                                                                "description": "push"}]})
    ev = m.map((("tools:abc",), "updates", {"__interrupt__": [intr]}))
    assert types_of(ev) == ["approval.request", "run.status"]
    assert ev[0]["data"]["approval_id"] == "i1" and ev[0]["agent"] == "subagent"
    assert ev[1]["data"]["status"] == "waiting_approval"


def test_approval_policy_merge_is_human_unless_agent_policy():
    assert approval_policy("interactive", "human")["git_push"] is True
    auto = approval_policy("autonomous", "human")
    assert auto["git_push"] is False and auto["github_merge"] is True
    assert approval_policy("autonomous", "agent")["github_merge"] is False
    assert approval_policy("autonomous", "agent-after-approval")["github_merge"] is True
