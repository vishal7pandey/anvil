"""Live smoke test of the runtime: real model via the gateway, real sandbox, contract events on stdout.

    uv run python scripts/smoke_runtime.py                       # quick, python:3.12-slim
    uv run python scripts/smoke_runtime.py --image anvil-sandbox-rtems:7 --user agent --uid 1001 \
        --task "Clone https://github.com/vishal7pandey/rtems into /workspace/rtems (depth 1), build the sparc/erc32 BSP with samples and run hello.exe on the simulator. Report the result."

Needs the local stack (scripts/up.ps1). Writes events as JSON lines; final line is a summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

from langgraph.checkpoint.memory import InMemorySaver

from anvil.runtime.agent import RunConfig, build_agent
from anvil.runtime.events import EventMapper
from anvil.runtime.sandbox import DockerSandbox, SandboxSpec

QUICK_TASK = ("In /workspace write fib.py that prints the first 10 Fibonacci numbers, run it with python3, "
              "and report the output. Plan with write_todos first.")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="python:3.12-slim")
    ap.add_argument("--user", default="root")
    ap.add_argument("--uid", type=int, default=0)
    ap.add_argument("--task", default=QUICK_TASK)
    ap.add_argument("--keep", action="store_true", help="keep the container afterwards")
    args = ap.parse_args()

    run_id = f"smoke-{uuid.uuid4().hex[:8]}"
    sandbox = DockerSandbox.start(SandboxSpec(image=args.image, name=f"claude-{run_id}", user=args.user,
                                              uid=args.uid, labels={"anvil.run_id": run_id}))
    config = RunConfig(run_id=run_id, repo="smoke", mode="autonomous")
    agent = build_agent(config, sandbox, checkpointer=InMemorySaver())
    mapper = EventMapper(run_id)
    counts: dict[str, int] = {}
    started = time.monotonic()
    try:
        async for item in agent.astream({"messages": [{"role": "user", "content": args.task}]},
                                        config={"configurable": {"thread_id": run_id}, "recursion_limit": 150},
                                        stream_mode=["messages", "updates"], subgraphs=True):
            for ev in mapper.map(item):
                counts[ev["type"]] = counts.get(ev["type"], 0) + 1
                if ev["type"] != "message.delta":
                    print(json.dumps(ev, default=str)[:600], flush=True)
    finally:
        if not args.keep:
            sandbox.stop()
    print(json.dumps({"summary": {"run_id": run_id, "seconds": round(time.monotonic() - started, 1),
                                  "events": counts}}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
