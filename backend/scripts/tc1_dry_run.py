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

from anvil.cases import get_case

# The case (ANVIL-38) holds the ticket, base branch, repo and scored files: this script knows no bug.
# It runs on the host; the agent never sees the case file.
DRY_RUN_NOTE = "\n\n(Dry run: commit locally, do not push.)\n"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--case", default="rtems-5776", help="case id from cases/*.toml")
    ap.add_argument("--image", default="anvil-sandbox-rtems:7.1")
    ap.add_argument("--db", default="postgresql://anvil:anvil@127.0.0.1:5433/anvil_claude")
    ap.add_argument("--keep", action="store_true", help="Keep sandbox container at exit for inspection")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    run_id = str(uuid.uuid4())
    case = get_case(args.case)
    upstream_files = set(case.target_files) | set(case.test_files)
    root_cause_files = set(case.target_files)
    ticket = f"{case.ticket_key or case.id}: {case.ticket_summary}\n\n{case.ticket_text}{DRY_RUN_NOTE}"
    run = {"run_id": run_id, "session_id": f"{case.id}-{run_id[:8]}", "repo": case.repo,
           "base_ref": case.base_branch, "jira_key": case.ticket_key, "mode": "autonomous",
           "merge_policy": "human", "network": "offline", "trigger": "tc1-dry-run",
           "case_id": case.id}
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

    from scripts.score_tc1 import score_case_diff

    from anvil.tools.run_scope import case_problems

    acceptance = case_problems(run, sandbox)
    touched = set(files)
    try:
        hunk_overlap = score_case_diff(diff, case)
    except Exception as exc:  # noqa: BLE001
        hunk_overlap = {"error": str(exc)}
    score = {
        "run_id": run_id, "session": run["session_id"], "final_status": status,
        "seconds": round(elapsed), "event_counts": counts,
        "files_touched": sorted(touched),
        "case": case.id,
        "upstream_files_hit": sorted(touched & upstream_files),
        "root_cause_file_hit": bool(touched & root_cause_files),
        "extra_files": sorted(touched - upstream_files),
        "hunk_overlap": hunk_overlap,
        "root_cause_score_0_2": None,  # filled in by a human reviewer
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
