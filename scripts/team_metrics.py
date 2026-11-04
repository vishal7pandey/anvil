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
from datetime import datetime
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


def _dt(value: str) -> datetime | None:
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


def comms_metrics(root: Path) -> dict[str, list[float]]:
    """Per-agent response minutes to action-required messages.

    A message in <B>/Inbox|Archived `from: A` `to: B` with `action-required: yes` is answered by a
    file in <A>/Inbox|Archived `from: B` whose `thread:` matches the request's thread (or its own
    filename stem) and whose date is after the request's.
    """
    msgs: list[dict[str, Any]] = []
    for agent in _agents(root):
        for folder in ("Inbox", "Archived"):
            d = root / agent / folder
            if not d.is_dir():
                continue
            for f in d.glob("*.md"):
                fm = front(f.read_text(encoding="utf-8", errors="replace"))
                msgs.append({"agent": agent, "file": f.name, **fm})

    # index: (owner folder, sender) -> earliest reply after a given time, by thread
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for m in msgs:
        if m.get("thread"):
            by_thread.setdefault(m["thread"], []).append(m)

    waits: dict[str, list[float]] = {}
    for req in msgs:
        if req.get("action-required") != "yes":
            continue
        req_dt = _dt(req.get("date", ""))
        if req_dt is None:
            continue
        sender = req.get("from", "")
        targets = [t.strip(" []'\"") for t in req.get("to", "").split(",") if t.strip(" []'\"")]
        # replies land in the sender's folder, from one of the targets, sharing the thread
        candidates = [r for r in by_thread.get(req["file"].replace(".md", ""), [])
                      + by_thread.get(Path(req["file"]).stem, [])
                      if r.get("from", "").strip(" []'\"") in targets]
        if not candidates:  # fall back: any later message in the sender's folder from a target
            candidates = [r for r in msgs if r.get("agent") == sender
                          and r.get("from", "").strip(" []'\"") in targets
                          and (_dt(r.get("date", "")) or datetime.min) > req_dt]
        if not candidates:
            continue
        ans = min((_dt(c.get("date", "")) or datetime.max) for c in candidates)
        if ans > req_dt:
            for t in targets:
                waits.setdefault(t, []).append((ans - req_dt).total_seconds() / 60)
    return waits


def _gh(*args: str) -> Any:
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def github_metrics(repo: str, limit: int = 40) -> list[dict[str, Any]]:
    """Per-PR timings via gh api. Kept thin on purpose: the number is for a standup, not a dashboard."""
    prs = _gh("pr", "list", "--repo", repo, "--state", "all", "--limit", str(limit),
              "--json", "number,title,createdAt,mergedAt")
    rows: list[dict[str, Any]] = []
    for pr in prs:
        n = pr["number"]
        try:
            reviews = _gh("api", f"repos/{repo}/pulls/{n}/reviews")
            commits = _gh("api", f"repos/{repo}/pulls/{n}/commits")
            checks = _gh("api", f"repos/{repo}/commits/{prs_head(repo, n)}/check-runs").get("check_runs", [])
        except Exception:  # noqa: BLE001 - a half-readable PR shouldn't sink the whole report
            reviews, commits, checks = [], [], []
        first_review = next((r["submitted_at"] for r in sorted(reviews, key=lambda r: r["submitted_at"])), None)
        approve = next((r["submitted_at"] for r in sorted(reviews, key=lambda r: r["submitted_at"])
                        if r.get("state") == "APPROVED"), None)
        rows.append({"pr": n, "title": pr.get("title", "")[:60],
                     "opened": pr.get("createdAt"),
                     "first_review": first_review, "approve": approve,
                     "merged": pr.get("mergedAt"),
                     "ci_rounds": len(checks), "commits": len(commits)})
    return rows


def prs_head(repo: str, number: int) -> str:
    pr = _gh("pr", "view", str(number), "--repo", repo, "--json", "headRefOid")
    return pr["headRefOid"]


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "-"
    return value.replace("T", " ")[:16]


def _hours(from_dt: str | None, to_dt: str | None) -> str:
    if not from_dt or not to_dt:
        return "-"
    try:
        a = datetime.fromisoformat(from_dt.replace("Z", "+00:00"))
        b = datetime.fromisoformat(to_dt.replace("Z", "+00:00"))
    except ValueError:
        return "-"
    return f"{(b - a).total_seconds() / 3600:.1f}h"


def render(gh_rows: list[dict[str, Any]], waits: dict[str, list[float]]) -> str:
    lines = ["# Team flow metrics", ""]
    lines += ["## Pull requests (open -> first review -> approve -> merge)", "",
              "| PR | Open->review | Review->approve | Approve->merge | CI runs | Commits |",
              "|---|---|---|---|---|---|"]
    for r in gh_rows:
        lines.append(f"| #{r['pr']} {r['title']} | {_hours(r['opened'], r['first_review'])} | "
                     f"{_hours(r['first_review'], r['approve'])} | {_hours(r['approve'], r['merged'])} | "
                     f"{r['ci_rounds']} | {r['commits']} |")
    lines += ["", "## Comms: median answer time to action-required messages", "",
              "| Agent | n | median min | max min |", "|---|---|---|---|"]
    for agent, vals in sorted(waits.items()):
        lines.append(f"| {agent} | {len(vals)} | {statistics.median(vals):.0f} | {max(vals):.0f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    repo = "vishal7pandey/anvil"
    rows = github_metrics(repo)
    waits = comms_metrics(COMMS)
    _OUT.mkdir(parents=True, exist_ok=True)
    out = _OUT / f"{datetime.now():%Y-%m-%d}.md"
    out.write_text(render(rows, waits), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
