"""Team flow metrics for the shared-laptop setup (ANVIL-86), read-only.

Two numbers the owner asked for:
- GitHub: per-PR time open -> first review -> approve -> merge, CI check rounds, commits.
- Comms: per-agent median time to answer an `action-required: yes` message (the message's `date:`
  front matter to the replying file's `date:` in the sender's folder — reply files carry the
  original `thread:`).

Writes private/metrics/<YYYY-MM-DD>.md. No writes outside that folder. Tests use a fixture dir and
a stubbed gh — no network.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMS = Path(r"C:\Dev\comms")
_OUT = REPO_ROOT / "private" / "metrics"

_FRONT = re.compile(r"^([a-zA-Z_-]+):\s*(.*)$", re.M)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def front(text: str) -> dict[str, str]:
    """The `key: value` front-matter lines of a comms message."""
    out: dict[str, str] = {}
    for m in _FRONT.finditer(text):
        k, v = m.group(1).strip().lower(), m.group(2).strip()
        if k in ("from", "to", "date", "action-required", "thread", "subject"):
            out[k] = v
    return out


def msg_time(path: Path, date_str: str = "") -> datetime | None:
    """When a comms message was really sent: the file's birth time (survives the Archive move).
    The `date:` front-matter line is the fallback only — agents write it late (review on #104)."""
    try:
        return datetime.fromtimestamp(path.stat().st_birthtime)
    except OSError:
        pass
    return _dt_parse(date_str)


def _dt_parse(value: str) -> datetime | None:
    """Parse a `date:` front-matter value ('2026-01-01 10:00' or '2026-01-01')."""
    m = DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        return datetime.strptime(value.strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d")
        except ValueError:
            return None


def _agents(root: Path) -> list[str]:
    return [d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))
            and d.name not in ("locks",)]


def comms_metrics(root: Path, since: datetime | None = None,
                  until: datetime | None = None) -> dict[str, Any]:
    """Per-agent response minutes to action-required messages, plus which pairing rule matched.

    A message in <B>/Inbox|Archived `from: A` `to: B` with `action-required: yes` is answered by a
    file in <A>/Inbox|Archived `from: B` whose `thread:` matches the request's thread (or its own
    filename stem) and whose date is after the request's. Requests inside [since, until] count.
    Returns {"waits": {agent: [minutes]}, "rules": {"thread": n, "fallback": n, "unanswered": n}} —
    the fallback understates the wait, so the report says when it was used.
    """
    msgs: list[dict[str, Any]] = []
    for agent in _agents(root):
        for folder in ("Inbox", "Archived"):
            d = root / agent / folder
            if not d.is_dir():
                continue
            for f in d.glob("*.md"):
                fm = front(f.read_text(encoding="utf-8", errors="replace"))
                msgs.append({"agent": agent, "file": f.name, "path": f, **fm})

    # index: (owner folder, sender) -> earliest reply after a given time, by thread
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for m in msgs:
        if m.get("thread"):
            by_thread.setdefault(m["thread"], []).append(m)

    waits: dict[str, list[float]] = {}
    rules = {"thread": 0, "fallback": 0, "unanswered": 0}
    for req in msgs:
        if req.get("action-required") != "yes":
            continue
        req_dt = msg_time(req["path"], req.get("date", ""))
        if req_dt is None or (since and req_dt < since) or (until and req_dt >= until):
            continue
        sender = req.get("from", "")
        targets = [t.strip(" []'\"") for t in req.get("to", "").split(",") if t.strip(" []'\"")]
        # replies land in the sender's folder, from one of the targets; a reply's `thread:` names the
        # request by its thread field or its filename stem
        keys = {req.get("thread", ""), Path(req["file"]).stem, req["file"].replace(".md", "")} - {""}
        candidates = [r for k in keys for r in by_thread.get(k, [])
                      if r.get("from", "").strip(" []'\"") in targets]
        rule = "thread"
        if not candidates:  # fall back: any later message in the sender's folder from a target
            candidates = [r for r in msgs if r.get("agent") == sender
                          and r.get("from", "").strip(" []'\"") in targets
                          and (msg_time(r["path"], r.get("date", "")) or datetime.min) > req_dt]
            rule = "fallback"
        if not candidates:
            rules["unanswered"] += 1
            continue
        rules[rule] += 1
        ans = min((msg_time(c["path"], c.get("date", "")) or datetime.max) for c in candidates)
        if ans > req_dt:
            for t in targets:
                waits.setdefault(t, []).append((ans - req_dt).total_seconds() / 60)
    return {"waits": waits, "rules": rules}


def _gh(*args: str) -> Any:
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


_REVIEW_RE = re.compile(r"^REVIEW \((\w+)\):", re.M)


def _pr_times(repo: str, n: int) -> tuple[str | None, str | None]:
    """First 'REVIEW (x):' comment and first 'REVIEW (x): APPROVE' comment times.

    The team reviews as PR comments on one shared account — pulls/{n}/reviews is empty (review on #104)."""
    comments = _gh("api", f"repos/{repo}/issues/{n}/comments")
    reviews = [c for c in comments if _REVIEW_RE.search(c.get("body") or "")]
    first = reviews[0]["created_at"] if reviews else None
    approve = next((c["created_at"] for c in reviews if "APPROVE" in _REVIEW_RE.search(c["body"]).group(0).upper()
                    or "APPROVE" in (c.get("body") or "").split("\n")[0].upper()), None)
    return first, approve


_AGENT_RE = re.compile(r"^(?:Agent:\s*|\[)(\w+)", re.M)


def _pr_agent(body: str, branch: str) -> str:
    """Who built the PR: 'Agent: devin' or '[claude]' first line of the body, else the branch prefix."""
    m = _AGENT_RE.search(body or "")
    if m:
        return m.group(1).lower()
    return branch.split("/", 1)[0].lower() if "/" in branch else "?"


def github_metrics(repo: str, limit: int = 40, since: datetime | None = None,
                   until: datetime | None = None) -> list[dict[str, Any]]:
    """Per-PR timings via gh api. Kept thin on purpose: the number is for a standup, not a dashboard."""
    prs = _gh("pr", "list", "--repo", repo, "--state", "all", "--limit", str(limit),
              "--json", "number,title,body,createdAt,mergedAt,headRefName")
    rows: list[dict[str, Any]] = []
    for pr in prs:
        opened = _dt_parse((pr.get("createdAt") or "")[:16].replace("T", " "))
        if opened and ((since and opened < since) or (until and opened >= until)):
            continue
        n = pr["number"]
        try:
            first_review, approve = _pr_times(repo, n)
            commits = _gh("api", f"repos/{repo}/pulls/{n}/commits")
            # CI rounds = workflow runs on the PR branch, not check-runs on head (always 3).
            runs = _gh("api", f"repos/{repo}/actions/runs?branch={pr['headRefName']}&per_page=50"
                       ).get("workflow_runs", [])
            mine = [r for r in runs if r.get("pull_requests") and any(p.get("number") == n
                    for p in r["pull_requests"])] or runs
            ci = len(mine)
            ci_s = sum((datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
                        - datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))
                        ).total_seconds() for r in mine
                       if r.get("run_started_at") and r.get("updated_at")) / 60
        except Exception as exc:  # noqa: BLE001 - a half-readable PR shouldn't sink the report
            print(f"warn: PR #{n}: {exc}", file=sys.stderr)
            first_review = approve = None
            commits, ci, ci_s = [], 0, 0.0
        rows.append({"pr": n, "title": pr.get("title", "")[:60],
                     "agent": _pr_agent(pr.get("body", ""), pr.get("headRefName", "")),
                     "opened": pr.get("createdAt"),
                     "first_review": first_review, "approve": approve,
                     "merged": pr.get("mergedAt"),
                     "ci_rounds": ci, "ci_minutes": round(ci_s, 1), "commits": len(commits)})
    return rows


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "-"
    return value.replace("T", " ")[:16]


def _delta_h(from_dt: str | None, to_dt: str | None) -> float | None:
    if not from_dt or not to_dt:
        return None
    try:
        a = datetime.fromisoformat(from_dt.replace("Z", "+00:00"))
        b = datetime.fromisoformat(to_dt.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (b - a).total_seconds() / 3600


def _hours(from_dt: str | None, to_dt: str | None) -> str:
    d = _delta_h(from_dt, to_dt)
    return f"{d:.1f}h" if d is not None else "-"


def _p90(vals: list[float]) -> float:
    if len(vals) < 2:
        return vals[0] if vals else 0.0
    return statistics.quantiles(sorted(vals), n=10)[8]


def render(gh_rows: list[dict[str, Any]], comms: dict[str, Any]) -> str:
    waits = comms.get("waits", comms)          # tolerate the old {agent: [min]} shape
    rules = comms.get("rules", {})
    lines = ["# Team flow metrics", ""]
    lines += ["## Pull requests (open -> first review -> approve -> merge)", "",
              "| PR | Agent | Open->review | Review->approve | Approve->merge | CI runs | CI min | Commits |",
              "|---|---|---|---|---|---|---|---|"]
    for r in gh_rows:
        lines.append(f"| #{r['pr']} {r['title']} | {r.get('agent', '?')} | "
                     f"{_hours(r['opened'], r['first_review'])} | "
                     f"{_hours(r['first_review'], r['approve'])} | {_hours(r['approve'], r['merged'])} | "
                     f"{r['ci_rounds']} | {r.get('ci_minutes', 0)} | {r['commits']} |")

    merged: dict[str, int] = {}
    for r in gh_rows:
        if r.get("merged"):
            merged[r.get("agent", "?")] = merged.get(r.get("agent", "?"), 0) + 1
    lines += ["", "## PRs merged per agent", "", "| Agent | Merged PRs |", "|---|---|"]
    for agent, n in sorted(merged.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {agent} | {n} |")

    phases = [("review queue (open -> first review)", [_delta_h(r["opened"], r["first_review"]) for r in gh_rows]),
              ("rework (first review -> approve)", [_delta_h(r["first_review"], r["approve"]) for r in gh_rows]),
              ("merge wait (approve -> merge)", [_delta_h(r["approve"], r["merged"]) for r in gh_rows]),
              ("total (open -> merge)", [_delta_h(r["opened"], r["merged"]) for r in gh_rows])]
    lines += ["", "## Where the team waits (hours, across PRs)", "", "| Phase | n | median | p90 |", "|---|---|---|---|"]
    worst = ("", 0.0)
    for name, raw in phases:
        vals = [v for v in raw if v is not None]
        if not vals:
            continue
        med = statistics.median(vals)
        lines.append(f"| {name} | {len(vals)} | {med:.1f} | {_p90(vals):.1f} |")
        if med > worst[1] and name != "total (open -> merge)":
            worst = (name, med)
    ci = [r.get("ci_minutes", 0) for r in gh_rows if r.get("ci_minutes")]
    if ci:
        lines.append(f"| CI time per PR (minutes) | {len(ci)} | {statistics.median(ci):.0f} | {_p90(ci):.0f} |")
    if worst[0]:
        lines += ["", f"**The team waits most on: {worst[0]}.**"]

    lines += ["", "## Comms: answer time to action-required messages (minutes)", "",
              "| Agent | n | median | p90 | max |", "|---|---|---|---|---|"]
    for agent, vals in sorted(waits.items()):
        lines.append(f"| {agent} | {len(vals)} | {statistics.median(vals):.0f} | "
                     f"{_p90(vals):.0f} | {max(vals):.0f} |")
    if rules:
        lines += ["", f"Pairing rules: {rules.get('thread', 0)} by thread, "
                      f"{rules.get('fallback', 0)} by fallback (understates the wait), "
                      f"{rules.get('unanswered', 0)} unanswered."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Team flow metrics (ANVIL-86): PR timings + comms answer times")
    p.add_argument("--repo", default="vishal7pandey/anvil")
    p.add_argument("--comms", type=Path, default=COMMS)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--day", help="report for one day (YYYY-MM-DD, local time)")
    p.add_argument("--since", help="ISO datetime; default 24h ago")
    p.add_argument("--until", help="ISO datetime; default now")
    args = p.parse_args(argv)
    if args.day:
        since = datetime.strptime(args.day, "%Y-%m-%d")
        until = since.replace(hour=23, minute=59, second=59)
    else:
        until = _dt_parse(args.until or "") or datetime.now()
        since = _dt_parse(args.since or "") or (until - timedelta(hours=24))
    rows = github_metrics(args.repo, args.limit, since, until)
    comms = comms_metrics(args.comms, since, until)
    _OUT.mkdir(parents=True, exist_ok=True)
    out = _OUT / f"{args.day or f'{until:%Y-%m-%d}'}.md"
    out.write_text(render(rows, comms), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
