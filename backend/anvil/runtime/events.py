"""Map the LangGraph stream of a Deep Agents run to Anvil's event contract.

Contract: docs/02-architecture.md "Event stream". Feed every item of
`agent.astream(..., stream_mode=["messages", "updates"], subgraphs=True)` to `EventMapper.map`.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

MAX_TOOL_OUTPUT = 4000
MAX_EXEC_TAIL = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


class EventMapper:
    """Stateful: assigns `seq`, remembers which tool call ran which command or subagent."""

    def __init__(self, run_id: str, model_aliases: dict[str, str] | None = None) -> None:
        self.run_id = run_id
        self.seq = 0
        self._calls: dict[str, dict[str, Any]] = {}
        self._aliases = model_aliases or {}
        # Step timing (ANVIL-83): LangGraph reports a node when it finishes, so the time since the
        # previous update is that node's wall time (a model call, or a tools step).
        self._last_update = time.monotonic()
        self._step_ms = 0

    def event(self, type_: str, data: dict[str, Any], agent: str = "main") -> dict[str, Any]:
        self.seq += 1
        return {"run_id": self.run_id, "seq": self.seq, "ts": _now(), "type": type_, "agent": agent, "data": data}

    def map(self, item: tuple) -> list[dict[str, Any]]:
        namespace, mode, chunk = item
        agent = "main" if not namespace else "subagent"
        if mode == "messages":
            return self._map_message_chunk(chunk, agent)
        if mode == "updates":
            return self._map_updates(chunk, agent)
        return []

    # -- messages mode: token deltas ------------------------------------------------------

    def _map_message_chunk(self, chunk: tuple, agent: str) -> list[dict[str, Any]]:
        message, _metadata = chunk
        if isinstance(message, AIMessageChunk):
            text = _text(message.content)
            if text:
                return [self.event("message.delta", {"role": "assistant", "text": text}, agent)]
        return []

    # -- updates mode: completed steps ----------------------------------------------------

    def _map_updates(self, update: dict[str, Any], agent: str) -> list[dict[str, Any]]:
        now = time.monotonic()
        self._step_ms, self._last_update = int((now - self._last_update) * 1000), now
        events: list[dict[str, Any]] = []
        for node, state in update.items():
            if node == "__interrupt__":
                events.extend(self._map_interrupts(state, agent))
                continue
            if not isinstance(state, dict):
                continue
            if "todos" in state and state["todos"] is not None:
                todos = [{"content": t.get("content", ""), "status": t.get("status", "pending")} for t in state["todos"]]
                events.append(self.event("plan", {"todos": todos}, agent))
            messages = state.get("messages") or []
            if not isinstance(messages, list):
                messages = [messages]
            for msg in messages:
                events.extend(self._map_message(msg, agent))
        return events

    def _map_message(self, msg: Any, agent: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if isinstance(msg, AIMessage):
            text = _text(msg.content)
            if text:
                out.append(self.event("message", {"role": "assistant", "text": text}, agent))
            for call in msg.tool_calls or []:
                self._calls[call["id"]] = {"name": call["name"], "args": call.get("args", {}), "t0": time.monotonic()}
                out.append(self.event("tool.call", {"call_id": call["id"], "name": call["name"], "args": call.get("args", {})}, agent))
                if call["name"] == "task":
                    args = call.get("args", {})
                    out.append(self.event("subagent", {"name": args.get("subagent_type", "?"), "phase": "start",
                                                       "summary": str(args.get("description", ""))[:300]}, agent))
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                model = (msg.response_metadata or {}).get("model_name", "")
                out.append(self.event("usage", {"model_alias": self._aliases.get(model, model),
                                                "input_tokens": usage.get("input_tokens", 0),
                                                "output_tokens": usage.get("output_tokens", 0),
                                                "latency_ms": self._step_ms}, agent))
        elif isinstance(msg, ToolMessage):
            call = self._calls.get(msg.tool_call_id, {"name": msg.name or "?", "args": {}})
            output = _text(msg.content)
            ok = msg.status != "error"
            t0 = call.get("t0")
            out.append(self.event("tool.result", {"call_id": msg.tool_call_id, "name": call["name"], "ok": ok,
                                                  "output": output[:MAX_TOOL_OUTPUT],
                                                  # the tools step's wall time; parallel calls share it
                                                  "duration_ms": int((time.monotonic() - t0) * 1000) if t0 else None},
                                  agent))
            if call["name"] == "execute":
                artifact = msg.artifact if isinstance(msg.artifact, dict) else {}
                out.append(self.event("sandbox.exec", {"cmd": call["args"].get("command", ""),
                                                       "exit_code": artifact.get("exit_code"),
                                                       "output_tail": output[-MAX_EXEC_TAIL:]}, agent))
            elif call["name"] == "task":
                out.append(self.event("subagent", {"name": call["args"].get("subagent_type", "?"), "phase": "end",
                                                   "summary": output[:300]}, agent))
        return out

    def _map_interrupts(self, interrupts: Any, agent: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for intr in interrupts or []:
            value = getattr(intr, "value", intr) or {}
            for req in value.get("action_requests", []) if isinstance(value, dict) else []:
                out.append(self.event("approval.request", {
                    "approval_id": getattr(intr, "id", None),
                    "action": req.get("name"),
                    "args": req.get("args", {}),
                    "reason": req.get("description", ""),
                }, agent))
        if out:
            out.append(self.event("run.status", {"status": "waiting_approval"}, agent))
        return out
