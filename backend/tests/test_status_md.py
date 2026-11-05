"""ANVIL-92 round 2: the STATUS.md merged column must resolve the merged PR/SHA from comments.

Regresses the literal-backspace regex bug (the heredoc wrote a 0x08 byte where \b belonged,
so every row showed "—").
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_trackers import write_status_md  # noqa: E402


def _issue(key, comments):
    return {"key": key, "summary": "s", "type": "Task", "status": "Done", "priority": "Medium",
            "labels": [], "epic": "E", "epic_summary": "WS-X Epic", "comments": comments}


def test_merged_column_resolves_pr_and_sha_from_comments(tmp_path):
    backlog = tmp_path / "backlog" / "ANVIL"
    backlog.mkdir(parents=True)
    # ANVIL-91's real comment: "#94 merged" -> pr ref; ANVIL-79's: "Merged as c3b9182 (PR #99..."
    # -> merged...#99 matches the PR rule before the sha rule.
    (backlog / "ANVIL-91.json").write_text(json.dumps(_issue(
        "ANVIL-91", [{"created": "2026-11-04", "body": "[claude] Done: PR #94 merged (9e68e01)."}])))
    (backlog / "ANVIL-79.json").write_text(json.dumps(_issue(
        "ANVIL-79", [{"created": "2026-11-04", "body": "[claude] Done. Merged as c3b9182 (PR #99)."}])))

    assert write_status_md(tmp_path) == 2
    rows = {line.split("|")[1].strip(): line.split("|")[5].strip()
            for line in (tmp_path / "backlog" / "STATUS.md").read_text().splitlines()
            if line.startswith("| ANVIL")}
    assert rows["ANVIL-91"] == "#94"
    assert rows["ANVIL-79"] == "#99"


def test_merged_column_takes_the_newest_comment(tmp_path):
    backlog = tmp_path / "backlog" / "ANVIL"
    backlog.mkdir(parents=True)
    (backlog / "ANVIL-1.json").write_text(json.dumps(_issue("ANVIL-1", [
        {"created": "2026-11-01", "body": "merged #10 first attempt"},
        {"created": "2026-11-03", "body": "merged #12 final merge"}])))

    write_status_md(tmp_path)
    row = [line for line in (tmp_path / "backlog" / "STATUS.md").read_text().splitlines()
           if line.startswith("| ANVIL-1")][0]
    assert row.split("|")[5].strip() == "#12"   # newest comment wins
