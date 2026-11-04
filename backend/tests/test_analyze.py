"""ANVIL-83: where a run's time went, from its events; exact step timings in new events."""

import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from anvil.analyze import analyze, main, to_markdown
from anvil.runtime.events import EventMapper

FIXTURE = Path(__file__).resolve().parents[2] / "frontend/src/__tests__/fixtures/tc3-run-f9181d40.json"


def test_real_run_adds_up_and_names_the_slow_tool():
    events = json.loads(FIXTURE.read_text(encoding="utf-8"))
    r = analyze(events)
    assert r["status"] == "failed" and r["events"] == 80
    assert abs(sum(r["by_kind_s"].values()) - r["wall_s"]) <= 0.05 * r["wall_s"]  # parts add up
    # The run died inside kb_search (no result): that 134 s is the tool's, not "finish".
    assert r["tools_s"]["kb_search (no result)"] > 100
    assert next(iter(r["tools_s"])) == "kb_search (no result)"
    assert r["tokens"]["anvil-main"]["calls"] == 23
    md = to_markdown("f9181d40", r)
    assert "| tools |" in md and "kb_search (no result)" in md


def _ev(seq, ts, type_, **data):
    return {"seq": seq, "ts": f"2026-11-04T10:00:{ts:06.3f}+00:00", "type": type_, "agent": "main", "data": data}


def test_exact_command_time_wins_and_approval_wait_is_its_own_bucket():
    events = [
        _ev(1, 0, "run.status", status="running"),
        _ev(2, 5, "run.record"),
        _ev(3, 10, "tool.call", call_id="c1", name="execute", args={"command": "make"}),
        _ev(4, 10.1, "usage", model_alias="anvil-main", input_tokens=10, output_tokens=2, latency_ms=4000),
        _ev(5, 40, "tool.result", call_id="c1", name="execute", ok=True, duration_ms=30000),
        _ev(6, 40.1, "sandbox.exec", cmd="make", exit_code=0, duration_s=29.5),
        _ev(7, 45, "approval.request", action="jira_create"),
        _ev(8, 55, "tool.call", call_id="c2", name="jira_create", args={}),
        _ev(9, 56, "run.status", status="succeeded"),
    ]
    r = analyze(events)
    assert r["by_kind_s"]["setup"] == 5.0 and r["by_kind_s"]["approval wait"] == 10.0
    assert r["slowest_commands"][0] == {"seq": 6, "cmd": "make", "exit_code": 0, "seconds": 29.5,
                                        "exact": True, "timed_out": False}
    assert r["model_latency_recorded_s"] == 4.0


def test_mapper_records_tool_duration_and_model_latency(monkeypatch):
    clock = iter([0.0, 2.0, 2.0, 7.5, 7.5]).__next__
    monkeypatch.setattr("anvil.runtime.events.time.monotonic", clock)
    m = EventMapper("r1")  # t=0
    call = AIMessage(content="", tool_calls=[{"id": "c1", "name": "execute", "args": {"command": "ls"}}],
                     usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6})
    out = m.map(((), "updates", {"model": {"messages": [call]}}))  # model step: 2 s
    usage = next(e for e in out if e["type"] == "usage")
    assert usage["data"]["latency_ms"] == 2000
    res = m.map(((), "updates", {"tools": {"messages": [ToolMessage(content="ok", tool_call_id="c1")]}}))
    assert next(e for e in res if e["type"] == "tool.result")["data"]["duration_ms"] == 5500


def test_sandbox_timing_is_taken_once_per_command():
    from anvil.runtime.sandbox import DockerSandbox

    sb = DockerSandbox.__new__(DockerSandbox)
    sb.exec_log = [{"cmd": "make", "duration_s": 1.0, "timed_out": False},
                   {"cmd": "make", "duration_s": 2.0, "timed_out": True}]
    assert sb.take_timing("make") == {"duration_s": 1.0, "timed_out": False}
    assert sb.take_timing("make") == {"duration_s": 2.0, "timed_out": True}
    assert sb.take_timing("make") is None


def test_cli_reads_an_event_file(capsys):
    assert main(["--events", str(FIXTURE), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 80
