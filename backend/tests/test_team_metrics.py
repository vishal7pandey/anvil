"""ANVIL-86: team_metrics parses comms wait times and renders the tables; no network."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "team_metrics.py"
spec = importlib.util.spec_from_file_location("team_metrics", SCRIPT)
tm = importlib.util.module_from_spec(spec)
sys.modules["team_metrics"] = tm
spec.loader.exec_module(tm)


def _msg(folder: Path, name: str, **fm):
    folder.mkdir(parents=True, exist_ok=True)
    text = "---\n" + "".join(f"{k}: {v}\n" for k, v in fm.items()) + "---\nbody\n"
    (folder / name).write_text(text)


def test_comms_metrics_pairs_request_with_reply(tmp_path, monkeypatch):
    # birthtime is real wall-clock creation — control it so the test measures the date: line
    monkeypatch.setattr(tm, "msg_time", lambda path, date: tm._dt_parse(date))
    _msg(tmp_path / "devin" / "Inbox", "2026-01-01-claude-ask.md",
         **{"from": "claude", "to": "[devin]", "date": "2026-01-01 10:00",
            "action-required": "yes", "thread": "t1"})
    _msg(tmp_path / "claude" / "Inbox", "2026-01-01-devin-ans.md",
         **{"from": "devin", "to": "[claude]", "date": "2026-01-01 10:30", "thread": "t1"})
    out = tm.comms_metrics(tmp_path)
    assert out["waits"]["devin"] == [30.0]
    assert out["rules"] == {"thread": 1, "fallback": 0, "unanswered": 0}


def test_render_writes_all_sections(tmp_path):
    rows = [{"pr": 1, "title": "x", "agent": "devin", "opened": "2026-01-01T10:00:00Z",
             "first_review": "2026-01-01T12:00:00Z", "approve": "2026-01-01T13:00:00Z",
             "merged": "2026-01-01T14:00:00Z", "ci_rounds": 2, "ci_minutes": 40.0, "commits": 3}]
    md = tm.render(rows, {"waits": {"devin": [30.0, 90.0]},
                          "rules": {"thread": 1, "fallback": 1, "unanswered": 0}})
    for needle in ("Pull requests", "PRs merged per agent", "Where the team waits",
                   "Comms", "2.0h", "| devin | 1 |", "Pairing rules"):
        assert needle in md


def test_pr_agent_from_body_and_branch():
    assert tm._pr_agent("Agent: devin\nrest", "devin/x") == "devin"
    assert tm._pr_agent("[antigravity] stuff", "fix/y") == "antigravity"
    assert tm._pr_agent("plain body", "claude/z") == "claude"


def test_front_reads_the_fields(tmp_path):
    _msg(tmp_path, "m.md", **{"from": "a", "action-required": "yes"})
    fm = tm.front((tmp_path / "m.md").read_text())
    assert fm["from"] == "a" and fm["action-required"] == "yes"
