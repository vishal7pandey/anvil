"""Generic dry run: a Jira ticket through the real executor, no API and no outward tools.

The agent gets the ticket text (fetched live with `jira_get` on the host), a single-branch
checkout and an offline sandbox (the leak controls of ANVIL-22). No host tools are given, so it
cannot push, comment or write Confluence; this measures whether the agent + skills do the work,
not the outward plumbing. The skill set in `backend/anvil/skills/` is uploaded by the executor,
so the agent picks whichever skill applies.

    uv run python scripts/dry_run.py --ticket RTEMS-3 --repo vishal7pandey/rtems --base main --out <dir>
    uv run python scripts/dry_run.py --ticket RTEMS-1 --repo vishal7pandey/rtems --base tc1/5776-base \
        --out <dir> --score-files cpukit/posix/src/mutexinit.c testsuites/psxtests/psx05/init.c
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from anvil.runtime.executor import DeepAgentExecutor, repo_path
from anvil.tools.jira import jira_get


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket", required=True, help="Jira key; title + description fetched via jira_get")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--image", default="anvil-sandbox-rtems:7.1")
    ap.add_argument("--db", default="postgresql://anvil:anvil@localhost:5433/anvil_devin")
    ap.add_argument("--score-files", nargs="*", default=None,
                    help="Optional upstream-answer file paths; host-side scoring only, the agent never sees them")
    ap.add_argument("--keep", action="store_true", help="Keep sandbox container at exit for inspection")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    score_files = set(args.score_files or [])

    issue = jira_get(args.ticket)
    ticket = f"{args.ticket}: {issue['summary']}\n\n{issue['description']}"

    run_id = str(uuid.uuid4())
    run = {"run_id": run_id, "session_id": f"dry-{run_id[:8]}", "repo": args.repo,
           "base_ref": args.base, "jira_key": args.ticket, "mode": "autonomous",
           "merge_policy": "human", "network": "offline", "trigger": "dry-run"}
    ex = DeepAgentExecutor(image=args.image, db_url=args.db)
    started = time.monotonic()
    counts: dict[str, int] = {}
    status = None
    with (out / "events.jsonl").open("w", encoding="utf-8") as log:
        async for ev in ex.start(run, ticket):
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

    from anvil.tools.run_scope import case_problems

    acceptance = case_problems(run, sandbox)
    touched = set(files)
    score = {
        "run_id": run_id, "session": run["session_id"], "ticket": args.ticket,
        "final_status": status, "seconds": round(elapsed), "event_counts": counts,
        "files_touched": sorted(touched),
        "score_files_hit": sorted(touched & score_files),
        "extra_files": sorted(touched - score_files),
        "reviewer_score": None,  # filled in by a human reviewer
        # Same host-side acceptance as real runs (ANVIL-44): "succeeded" is the agent's word, this is ours.
        "acceptance_problems": acceptance,
        "verified": status == "succeeded" and not acceptance,
    }
    (out / "score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
    print(json.dumps(score, indent=2))
    if args.keep:
        print(f"sandbox kept for inspection: {sandbox.id}")
    else:
        sandbox.stop(remove=True)
        print(f"sandbox removed: {sandbox.id}")
    return 0 if score["verified"] else 1


if __name__ == "__main__":
    # psycopg's async driver can't use Windows' default ProactorEventLoop.
    raise SystemExit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
