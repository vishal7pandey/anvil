from langchain_core.messages import AIMessage, ToolMessage

from deepagents.backends.protocol import ExecuteResponse

from anvil.runtime.stuck import Budgets, StuckDetector, failure_signature

from test_runtime_executor import RUN, OfflineExecutor, collect


def exec_ev(cmd, exit_code, tail="error: x"):
    return {"type": "sandbox.exec", "data": {"cmd": cmd, "exit_code": exit_code, "output_tail": tail}}


def test_failure_signature_ignores_numbers_and_addresses():
    a = failure_signature("./waf -j8", "build ok\nerror: undefined reference at 0x4000 line 12")
    b = failure_signature("./waf  -j8", "other\nerror: undefined reference at 0x9999 line 99")
    c = failure_signature("./waf -j8", "error: something else entirely")
    assert a == b != c


def test_same_failure_three_times_trips():
    d = StuckDetector(Budgets())
    assert d.observe(exec_ev("make", 2)) is None
    assert d.observe(exec_ev("make", 0)) is None          # success doesn't count
    assert d.observe(exec_ev("make", 2)) is None
    reason = d.observe(exec_ev("make", 2))
    assert reason and "failed 3 times" in reason
    assert d.observe({"type": "tool.call", "data": {}}) == reason   # sticky


def test_budgets_and_ci_and_progress():
    d = StuckDetector(Budgets(max_tool_calls=3))
    for _ in range(2):
        assert d.observe({"type": "tool.call", "data": {}}) is None
    assert "step budget" in d.observe({"type": "tool.call", "data": {}})

    d = StuckDetector(Budgets(max_tokens=100))
    assert "token budget" in d.observe({"type": "usage", "data": {"input_tokens": 90, "output_tokens": 20}})

    now = [0.0]
    d = StuckDetector(Budgets(max_seconds=60), clock=lambda: now[0])
    now[0] = 61
    assert "time budget" in d.observe({"type": "message", "data": {}})

    d = StuckDetector(Budgets())
    red = {"type": "tool.result", "data": {"name": "github_checks", "output": "build-test: failure"}}
    assert d.observe(red) is None and d.observe(red) is None
    assert "CI still red" in d.observe(red)

    d = StuckDetector(Budgets(no_progress_tool_calls=3))
    d.note_diff_state("a")
    d.observe({"type": "tool.call", "data": {}})
    d.note_diff_state("b")                                    # progress resets the counter
    for _ in range(2):
        assert d.observe({"type": "tool.call", "data": {}}) is None
    assert "no change" in d.observe({"type": "tool.call", "data": {}})


class FakeSandbox:
    id = "anvil-sbx-test"

    def __init__(self):
        self.commands = []

    def execute(self, command, *, timeout=None):
        self.commands.append(command)
        return ExecuteResponse(output="deadbeef", exit_code=0)


class StuckExecutor(OfflineExecutor):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.sb = FakeSandbox()

    def sandbox_for(self, run):
        return self.sb


def failing_exec_items(n):
    items = []
    for i in range(n):
        call = AIMessage(content="", tool_calls=[{"id": f"c{i}", "name": "execute", "args": {"command": "./waf"}}])
        result = ToolMessage(content="error: psx05.c:12: undefined reference", tool_call_id=f"c{i}",
                             name="execute", artifact={"exit_code": 1})
        items += [((), "updates", {"model": {"messages": [call]}}), ((), "updates", {"tools": {"messages": [result]}})]
    return items


async def test_runtime_forces_handoff_and_calls_hooks():
    from test_runtime_executor import factory_for
    pushed, reported = [], []
    ex = StuckExecutor(agent_factory=factory_for(failing_exec_items(10)),
                       push_hook=lambda sb, run, wip: pushed.append(wip) or f"https://example/{wip}",
                       report_hook=lambda run, h: reported.append(h))
    events = await collect(ex.start(RUN, "fix it"))
    types = [e["type"] for e in events]
    assert events[-1]["data"]["status"] == "handoff"
    assert types.count("sandbox.exec") == 3                 # stopped right after the third identical failure
    assert pushed == ["anvil/rtems-1-wip"]
    assert "WIP handoff" in " ".join(ex.sb.commands) and "git branch -f anvil/rtems-1-wip" in " ".join(ex.sb.commands)
    art = next(e for e in events if e["type"] == "artifact")
    assert art["data"]["url"] == "https://example/anvil/rtems-1-wip"
    assert "failed 3 times" in art["data"]["note"]["hypothesis"]
    h = reported[0]
    assert h["wip_branch"] == "anvil/rtems-1-wip" and h["url"].endswith("anvil/rtems-1-wip")
    assert "undefined reference" in h["note"]["evidence"] and "Sandbox kept" in h["markdown"]


async def test_push_failure_does_not_block_handoff():
    from test_runtime_executor import factory_for

    def bad_push(sb, run, wip):
        raise RuntimeError("no token")

    ex = StuckExecutor(agent_factory=factory_for(failing_exec_items(3)), push_hook=bad_push)
    events = await collect(ex.start(RUN, "x"))
    assert events[-1]["data"]["status"] == "handoff"
    assert any("WIP push failed" in e["data"].get("text", "") for e in events if e["type"] == "message")


def test_same_failure_with_code_changes_in_between_is_iteration_not_stuck():
    d = StuckDetector(Budgets())
    d.note_diff_state("v0")
    for state in ("v1", "v2", "v3"):  # edit, build fails the same way, edit again...
        assert d.observe(exec_ev("make", 2)) is None
        d.note_diff_state(state)
    assert d.observe(exec_ev("make", 2)) is None
    d.observe(exec_ev("make", 2))  # ...but nothing changed between these three: stuck
    assert "failed 3 times" in (d.observe(exec_ev("make", 2)) or "")
