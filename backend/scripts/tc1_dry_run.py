"""TC1 dry run: RTEMS-1 through the real executor, no API and no outward tools.

The agent gets the ticket text, a single-branch checkout of the base branch and an offline sandbox
(the leak controls of ANVIL-22). It cannot push or open a PR here: this run answers one question,
"does it find the root cause and write a passing regression test?". At the end the script saves
the event log and the agent's diff, and compares the files it touched with the upstream fix.

    uv run python scripts/tc1_dry_run.py --out <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from anvil.runtime.executor import DeepAgentExecutor, repo_path

# Upstream fix a62e60b5 (rtems #5776). Only file paths here: the harness runs on the host, the
# agent never sees this file.
UPSTREAM_FILES = {"cpukit/posix/src/mutexinit.c", "testsuites/psxtests/psx05/init.c"}
ROOT_CAUSE_FILE = "cpukit/posix/src/mutexinit.c"

TICKET = """RTEMS-1: Mutex reports EBUSY / crashes after a failed pthread_mutex_init with an invalid priority ceiling

## Observed

After `pthread_mutex_init()` fails with `EINVAL` for a priority-ceiling mutex whose ceiling is out of
range, the same mutex object is still accepted by later calls: `pthread_mutex_trylock()` and
`pthread_mutex_destroy()` return `EBUSY` instead of `EINVAL`, and `pthread_mutex_lock()` ends in a fatal
data abort inside the thread queue code. With a zero-filled object the lock instead succeeds and the
caller ends up at priority 0.

## Steps to reproduce

```c
pthread_mutexattr_t attr;
pthread_mutex_t     mtx;

memset( &mtx, 0xa5, sizeof( mtx ) );
pthread_mutexattr_init( &attr );
pthread_mutexattr_setprotocol( &attr, PTHREAD_PRIO_PROTECT );
pthread_mutexattr_setprioceiling( &attr, sched_get_priority_max( SCHED_FIFO ) + 1 );

pthread_mutex_init( &mtx, &attr ); /* EINVAL, as expected */
pthread_mutex_trylock( &mtx );     /* expected EINVAL, got EBUSY */
pthread_mutex_destroy( &mtx );     /* expected EINVAL, got EBUSY */
```

## Expected

A failed `pthread_mutex_init()` leaves `*mutex` unmodified, and later calls reject the object with `EINVAL`.

## Environment

Uniprocessor, RTEMS_DEBUG off. Reproduce on `sparc/erc32` (SIS).

## Done when

Root cause identified, fix on a branch, a regression test in the POSIX test suite passes on the
simulator. (Dry run: commit locally, do not push.)
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--image", default="anvil-sandbox-rtems:7")
    ap.add_argument("--db", default="postgresql://anvil:anvil@localhost:5433/anvil_claude")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    run_id = str(uuid.uuid4())
    run = {"run_id": run_id, "session_id": f"tc1-{run_id[:8]}", "repo": "vishal7pandey/rtems",
           "base_ref": "tc1/5776-base", "jira_key": "RTEMS-1", "mode": "autonomous",
           "merge_policy": "human", "network": "offline", "trigger": "tc1-dry-run"}
    ex = DeepAgentExecutor(image=args.image, db_url=args.db)
    started = time.monotonic()
    counts: dict[str, int] = {}
    status = None
    with (out / "events.jsonl").open("w", encoding="utf-8") as log:
        async for ev in ex.start(run, TICKET):
            log.write(json.dumps(ev, default=str) + "\n")
            log.flush()
            counts[ev["type"]] = counts.get(ev["type"], 0) + 1
            if ev["type"] == "run.status":
                status = ev["data"]["status"]
                print(f"[{time.monotonic() - started:6.0f}s] status {status}", flush=True)
            elif ev["type"] == "tool.call":
                print(f"[{time.monotonic() - started:6.0f}s] {ev['data'].get('name')}", flush=True)
    await ex.aclose()
    elapsed = time.monotonic() - started

    sandbox = ex.sandbox_for(run)
    path = repo_path(run["repo"])
    base = f"origin/{run['base_ref']}"
    files = sandbox.execute(f"cd {path} && git diff --name-only {base}", timeout=60).output.split()
    diff = sandbox.execute(f"cd {path} && git diff {base}", timeout=60).output
    (out / "agent.diff").write_text(diff, encoding="utf-8")

    touched = set(files)
    score = {
        "run_id": run_id, "session": run["session_id"], "final_status": status,
        "seconds": round(elapsed), "event_counts": counts,
        "files_touched": sorted(touched),
        "upstream_files_hit": sorted(touched & UPSTREAM_FILES),
        "root_cause_file_hit": ROOT_CAUSE_FILE in touched,
        "extra_files": sorted(touched - UPSTREAM_FILES),
        "root_cause_score_0_2": None,  # filled in by a human reviewer
    }
    (out / "score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
    print(json.dumps(score, indent=2))
    print(f"sandbox kept for inspection: {sandbox.id}")
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    # psycopg's async driver can't use Windows' default ProactorEventLoop.
    raise SystemExit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
