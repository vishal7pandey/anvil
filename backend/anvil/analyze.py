"""Where did a run's time go? (ANVIL-83)

    python -m anvil.analyze <run_id> [--json]          # from the API database (ANVIL_DATABASE_URL)
    python -m anvil.analyze --events run.json [--json]  # from an exported event list

LangGraph reports a node when it finishes, and the API stores events as they stream, so the run is a
sequence of *updates* (bursts of events with the same moment). Each update is given the time since
the previous one and a kind:

- `model`: the update carries the model's output (assistant text, tool calls, usage);
- `tools:<name>`: it carries tool results (a step with several tools splits its time between them);
- `approval wait`: the time after an approval request until the run moved again;
- `setup`: start → run record (sandbox + checkout); `finish`: last agent step → terminal status
  (verify, export, handoff).

So the parts always add up to the wall time, and it works on runs recorded before the explicit
timings existed. Newer runs also carry exact command times (`sandbox.exec.duration_s`) and model
step latency (`usage.latency_ms`), which the command table prefers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Iterable

BURST_S = 0.5          # events of one update are persisted within this window
IDLE_GAP_S = 120.0     # an update that took longer than this is listed as a long step
TERMINAL = {"succeeded", "failed", "cancelled", "handoff"}


def _ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _updates(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for ev in events:
        if ev["type"] == "message.delta":  # streamed during a model call, not an update of its own
            continue
        if groups and (_ts(ev["ts"]) - _ts(groups[-1][0]["ts"])).total_seconds() <= BURST_S:
            groups[-1].append(ev)
        else:
            groups.append([ev])
    return groups


def _kind(group: list[dict[str, Any]]) -> str:
    types = {e["type"] for e in group}
    if "run.record" in types:
        return "setup"
    if "tool.result" in types:
        return "tools"
    if types & {"usage", "tool.call", "plan"} or any(
            e["type"] == "message" and (e.get("data") or {}).get("role") == "assistant" for e in group):
        return "model"
    if any(e["type"] == "run.status" and (e.get("data") or {}).get("status") in TERMINAL for e in group) \
            or types & {"artifact"}:
        return "finish"
    return "other"


def analyze(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    evs = sorted(events, key=lambda e: e["seq"])
    if not evs:
        return {"events": 0, "wall_s": 0.0}
    start, end = _ts(evs[0]["ts"]), _ts(evs[-1]["ts"])
    wall = (end - start).total_seconds()
    groups = _updates(evs)

    by_kind: dict[str, float] = defaultdict(float)
    by_tool: dict[str, float] = defaultdict(float)
    tool_calls: Counter[str] = Counter()
    long_steps: list[dict[str, Any]] = []
    group_time: dict[int, float] = {}
    prev_end, prev_group = start, None
    pending: dict[str, str] = {}  # call_id -> tool name, for calls not answered yet
    for group in groups:
        took = max(0.0, (_ts(group[0]["ts"]) - prev_end).total_seconds())
        kind = _kind(group)
        if prev_group is not None and any(e["type"] == "approval.request" for e in prev_group):
            kind = "approval wait"
        elif kind in ("finish", "other") and pending:
            # The run ended (or moved on) while a tool was still running: that time is the tool's
            # (TC3 run f9181d40 spent 134 s in a kb_search that never returned).
            kind = "tools"
        label = kind
        if kind == "tools":
            names = [(e.get("data") or {}).get("name") or "?" for e in group if e["type"] == "tool.result"]
            if not names:
                names = [f"{n} (no result)" for n in pending.values()]
                pending.clear()
            for name in names:
                by_tool[name] += took / len(names)
                tool_calls[name] += 1
        by_kind[label] += took
        for e in group:
            group_time[e["seq"]] = took
            d = e.get("data") or {}
            if e["type"] == "tool.call" and d.get("call_id"):
                pending[d["call_id"]] = d.get("name") or "?"
            elif e["type"] == "tool.result":
                pending.pop(d.get("call_id"), None)
        if took >= IDLE_GAP_S:
            long_steps.append({"at_seq": group[0]["seq"], "kind": label, "seconds": round(took, 1)})
        prev_end, prev_group = _ts(group[-1]["ts"]), group

    commands = []
    for e in evs:
        if e["type"] != "sandbox.exec":
            continue
        d = e.get("data") or {}
        exact = d.get("duration_s")
        commands.append({"seq": e["seq"], "cmd": (d.get("cmd") or "")[:160], "exit_code": d.get("exit_code"),
                         "seconds": round(float(exact), 1) if exact is not None else round(group_time.get(e["seq"], 0.0), 1),
                         "exact": exact is not None, "timed_out": bool(d.get("timed_out"))})
    repeated = Counter(c["cmd"] for c in commands)

    tokens: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})
    model_ms = 0
    for e in evs:
        if e["type"] == "usage":
            d = e.get("data") or {}
            t = tokens[d.get("model_alias") or "?"]
            t["calls"] += 1
            t["input"] += int(d.get("input_tokens") or 0)
            t["output"] += int(d.get("output_tokens") or 0)
            model_ms += int(d.get("latency_ms") or 0)

    status = next((e["data"].get("status") for e in reversed(evs)
                   if e["type"] == "run.status" and (e.get("data") or {}).get("status") in TERMINAL), None)
    return {
        "events": len(evs),
        "status": status,
        "wall_s": round(wall, 1),
        "by_kind_s": {k: round(v, 1) for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])},
        "tools_s": {k: round(v, 1) for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1])},
        "tool_calls": dict(tool_calls.most_common()),
        "slowest_commands": sorted(commands, key=lambda c: -c["seconds"])[:10],
        "repeated_commands": {c: n for c, n in repeated.most_common() if n > 1},
        "long_steps": long_steps,
        "tokens": dict(tokens),
        "model_latency_recorded_s": round(model_ms / 1000, 1),
        "compactions": sum(1 for e in evs if e["type"] == "context.compacted"),   # ANVIL-93
    }


def to_markdown(run_id: str, r: dict[str, Any]) -> str:
    if not r.get("events"):
        return f"# Run {run_id}\n\nNo events."
    pct = lambda s: f"{(100 * s / r['wall_s']):.0f}%" if r["wall_s"] else "-"  # noqa: E731
    lines = [f"# Run {run_id}: {r['wall_s'] / 60:.1f} min, {r['status'] or 'not finished'}, {r['events']} events", "",
             "| Where | Seconds | Share |", "|---|---:|---:|"]
    lines += [f"| {k} | {v} | {pct(v)} |" for k, v in r["by_kind_s"].items()]
    if r["tools_s"]:
        lines += ["", "| Tool | Seconds | Calls |", "|---|---:|---:|"]
        lines += [f"| {k} | {v} | {r['tool_calls'].get(k, 0)} |" for k, v in r["tools_s"].items()]
    if r["slowest_commands"]:
        lines += ["", "| Slowest commands | Seconds | Exit |", "|---|---:|---:|"]
        lines += [f"| `{c['cmd']}` | {c['seconds']}{'' if c['exact'] else '~'}{' (timed out)' if c['timed_out'] else ''} "
                  f"| {c['exit_code']} |" for c in r["slowest_commands"]]
    if r["repeated_commands"]:
        lines += ["", "Repeated commands: " + "; ".join(f"`{c[:80]}` x{n}" for c, n in r["repeated_commands"].items())]
    if r["long_steps"]:
        lines += ["", "Steps over 2 min: " + ", ".join(f"{s['kind']} {s['seconds']}s (seq {s['at_seq']})"
                                                       for s in r["long_steps"])]
    if r["tokens"]:
        lines += ["", "Tokens: " + "; ".join(f"{m}: {t['calls']} calls, {t['input']} in / {t['output']} out"
                                             for m, t in r["tokens"].items())]
    if r.get("compactions"):
        lines += ["", f"Context compactions: {r['compactions']} (older messages were summarized to stay in the window)."]
    lines += ["", "`~` = estimated from event times (no exact timing recorded for that command)."]
    return "\n".join(lines)


def load_events(run_id: str, database_url: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from anvil.api.models import RunEvent

    url = database_url or os.environ.get("ANVIL_DATABASE_URL") or "postgresql+psycopg://anvil:anvil@localhost:5433/anvil"
    with Session(create_engine(url)) as db:
        rows = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)).all()
        return [{"seq": r.seq, "ts": r.ts, "type": r.type, "agent": r.agent, "data": r.data} for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m anvil.analyze", description=__doc__.split("\n")[0])
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--events", help="a JSON file holding the run's event list instead of the database")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not (a.run_id or a.events):
        ap.error("give a run id or --events")
    events = json.load(open(a.events, encoding="utf-8")) if a.events else load_events(a.run_id)
    report = analyze(events)
    print(json.dumps(report, indent=2, default=str) if a.json else to_markdown(a.run_id or a.events, report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
