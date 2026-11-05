"""Export the Jira projects and Confluence spaces into the repo, for offline handoff.

The team that picks this project up may not have our Jira, Confluence or GitHub. This script writes
everything they need to read as plain files:

    backlog/<PROJECT>/<KEY>.json        one file per Jira issue (description + comments as Markdown)
    backlog/index.json                  one line per issue: key, type, status, summary, labels, epic
    docs/confluence/<SPACE>/<slug>.md   one file per Confluence page

    uv run python scripts/export_trackers.py --out ..          # from backend/, writes to the repo root

It reads ATLASSIAN_SITE / ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN from the repo .env (never printed).
Re-running overwrites the files, so the snapshot can be refreshed while the trackers still exist.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from anvil.tools._atlassian import request

JIRA_PROJECTS = ("ANVIL", "RTEMS")
CONFLUENCE_SPACES = ("ANVIL", "RTKB")
FIELDS = ["summary", "issuetype", "status", "priority", "labels", "parent", "created", "updated",
          "resolutiondate", "description", "issuelinks", "subtasks"]


# -- Atlassian Document Format -> Markdown ----------------------------------------------------------

def adf_to_md(node: Any) -> str:
    """Convert an ADF document (Jira descriptions/comments, Confluence pages) to Markdown."""
    if node is None:
        return ""
    if isinstance(node, str):  # some fields are already plain text
        return node
    return _blocks(node.get("content") or []).strip() + "\n"


def _blocks(nodes: list[dict], indent: str = "") -> str:
    return "".join(_block(n, indent) for n in nodes)


def _block(n: dict, indent: str) -> str:
    t = n.get("type")
    content = n.get("content") or []
    if t == "paragraph":
        return f"{indent}{_inline(content)}\n\n"
    if t == "heading":
        return f"{'#' * int(n.get('attrs', {}).get('level', 2))} {_inline(content)}\n\n"
    if t in ("bulletList", "orderedList"):
        out = []
        for i, item in enumerate(content, 1):
            marker = f"{i}." if t == "orderedList" else "*"
            body = _blocks(item.get("content") or [], indent + "  ").strip("\n")
            body = body[len(indent) + 2:] if body.startswith(indent + "  ") else body.lstrip()
            out.append(f"{indent}{marker} {body}\n")
        return "".join(out) + ("\n" if not indent else "")
    if t == "codeBlock":
        lang = (n.get("attrs") or {}).get("language") or ""
        return f"```{lang}\n{''.join(c.get('text', '') for c in content)}\n```\n\n"
    if t == "blockquote":
        return "".join(f"> {line}\n" for line in _blocks(content).strip().splitlines()) + "\n"
    if t == "rule":
        return "---\n\n"
    if t in ("panel", "expand", "nestedExpand", "layoutSection", "layoutColumn", "bodiedExtension"):
        title = (n.get("attrs") or {}).get("title")
        return (f"**{title}**\n\n" if title else "") + _blocks(content, indent)
    if t == "table":
        rows = [[_inline_cell(c) for c in row.get("content") or []] for row in content]
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(lines) + "\n\n"
    if t in ("mediaSingle", "mediaGroup"):
        return "_(attachment not exported)_\n\n"
    if content:
        return _blocks(content, indent)
    return _inline([n]) + ("\n\n" if t not in ("text", "hardBreak") else "")


def _inline_cell(cell: dict) -> str:
    return _blocks(cell.get("content") or []).strip().replace("\n\n", "<br>").replace("\n", " ").replace("|", "\\|")


def _inline(nodes: list[dict]) -> str:
    out = []
    for n in nodes:
        t = n.get("type")
        attrs = n.get("attrs") or {}
        if t == "text":
            s = n.get("text", "")
            for m in n.get("marks") or []:
                mt = m.get("type")
                if mt == "code":
                    s = f"`{s}`"
                elif mt == "strong":
                    s = f"**{s}**"
                elif mt == "em":
                    s = f"_{s}_"
                elif mt == "strike":
                    s = f"~~{s}~~"
                elif mt == "link":
                    s = f"[{s}]({(m.get('attrs') or {}).get('href', '')})"
            out.append(s)
        elif t == "hardBreak":
            out.append("  \n")
        elif t in ("inlineCard", "blockCard", "embedCard"):
            out.append(attrs.get("url", ""))
        elif t == "mention":
            out.append(attrs.get("text", "@someone"))
        elif t == "emoji":
            out.append(attrs.get("text", attrs.get("shortName", "")))
        elif t == "status":
            out.append(f"[{attrs.get('text', '')}]")
        elif t == "date":
            out.append(str(attrs.get("timestamp", "")))
        elif n.get("content"):
            out.append(_inline(n["content"]))
    return "".join(out)


# -- Jira ------------------------------------------------------------------------------------------

def jira_issues(project: str) -> list[dict]:
    issues, token = [], None
    while True:
        body: dict[str, Any] = {"jql": f"project = {project} ORDER BY key ASC", "fields": FIELDS, "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        page = request("POST", "/rest/api/3/search/jql", json=body)
        issues += page.get("issues") or []
        token = page.get("nextPageToken")
        if not token or page.get("isLast", True):
            return issues


def jira_comments(key: str) -> list[dict]:
    out, start = [], 0
    while True:
        page = request("GET", f"/rest/api/3/issue/{key}/comment", params={"startAt": start, "maxResults": 100})
        out += page.get("comments") or []
        start += len(page.get("comments") or [])
        if start >= page.get("total", 0) or not page.get("comments"):
            return out


def issue_record(issue: dict) -> dict:
    f = issue["fields"]
    parent = f.get("parent") or {}
    links = []
    for link in f.get("issuelinks") or []:
        other = link.get("outwardIssue") or link.get("inwardIssue") or {}
        direction = link["type"]["outward"] if "outwardIssue" in link else link["type"]["inward"]
        links.append({"relation": direction, "key": other.get("key")})
    return {
        "key": issue["key"],
        "type": (f.get("issuetype") or {}).get("name"),
        "status": (f.get("status") or {}).get("name"),
        "priority": (f.get("priority") or {}).get("name"),
        "summary": f.get("summary"),
        "labels": f.get("labels") or [],
        "epic": parent.get("key"),
        "epic_summary": (parent.get("fields") or {}).get("summary"),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "resolved": f.get("resolutiondate"),
        "links": links,
        "subtasks": [s.get("key") for s in f.get("subtasks") or []],
        "description": adf_to_md(f.get("description")),
        "comments": [
            # Every agent shared one Atlassian account: the author is in the "[name]" prefix of the body.
            {"created": c.get("created"), "body": adf_to_md(c.get("body"))}
            for c in jira_comments(issue["key"])
        ],
    }


# -- Confluence ------------------------------------------------------------------------------------

def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "page"


def confluence_pages(space_key: str) -> list[dict]:
    space = request("GET", "/wiki/api/v2/spaces", params={"keys": space_key})["results"][0]
    pages, path = [], f"/wiki/api/v2/spaces/{space['id']}/pages"
    params: dict[str, Any] | None = {"body-format": "atlas_doc_format", "limit": 50}
    while path:
        page = request("GET", path, params=params)
        pages += page.get("results") or []
        nxt = (page.get("_links") or {}).get("next")
        path, params = (nxt.removeprefix("/wiki") if nxt and nxt.startswith("/wiki") else nxt), None
        if path and not path.startswith("/wiki"):
            path = "/wiki" + path
    return pages


def page_markdown(page: dict, space_key: str) -> str:
    raw = ((page.get("body") or {}).get("atlas_doc_format") or {}).get("value") or "{}"
    body = adf_to_md(json.loads(raw))
    meta = f"<!-- Confluence {space_key} page {page['id']}, version {(page.get('version') or {}).get('number')}, " \
           f"exported for offline use; parent {page.get('parentId')} -->"
    return f"{meta}\n\n# {page['title']}\n\n{body}"


# -- main ------------------------------------------------------------------------------------------


import re as _re


def write_status_md(root: Path) -> int:
    """Regenerate backlog/STATUS.md from the on-disk issue JSONs — no Jira needed (offline).
    One table per epic: key, summary, status, owner label, merged PR/SHA, last comment, handoff scope."""
    issues = []
    for f in sorted((root / "backlog").glob("*/*.json")):
        issues.append(__import__("json").loads(f.read_text(encoding="utf-8")))
    by_epic: dict[str, list] = {}
    for i in issues:
        by_epic.setdefault(i.get("epic_summary") or i.get("epic") or "—", []).append(i)

    def scope(labels):
        for s in ("handoff-in", "post-handoff"):
            if s in labels:
                return s
        return "—"

    def merged_sha(comments, body=""):
        text = " ".join(c.get("body", "") for c in comments) + " " + body
        pr = _re.search(r"#(\d+).*merged|merged.*#(\d+)", text)
        m = _re.search(r"(?:merged|sha|commit)\D{0,20}([0-9a-f]{7,40})", text, _re.I)
        return ("#" + next(g for g in pr.groups() if g) if pr else (m.group(1)[:7] if m else "—"))

    def last_comment(comments):
        d = [c.get("created", "")[:10] for c in comments if c.get("created")]
        return max(d) if d else "—"

    anv = sum(1 for i in issues if i["key"].startswith("ANVIL-"))
    rt = sum(1 for i in issues if i["key"].startswith("RTEMS-"))
    lines = ["# Backlog status — freeze snapshot", "",
             "Regenerate: `uv run python scripts/export_trackers.py --status-md` (offline).",
             f"Counts: {anv} ANVIL + {rt} RTEMS = {len(issues)} (from the on-disk JSON).", ""]
    for epic in sorted(by_epic):
        rows = sorted(by_epic[epic], key=lambda x: int(_re.search(r"\d+", x["key"]).group()))
        lines += [f"## {epic}  ({len(rows)})", "",
                  "| key | summary | status | owner | merged | last comment | handoff scope |",
                  "|-----|---------|--------|-------|--------|--------------|---------------|"]
        for i in rows:
            owner = next((l.replace("agent-", "") for l in i["labels"] if l.startswith("agent-")), "—")
            lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                i["key"], i["summary"][:52].replace("|", "/"), i["status"], owner,
                merged_sha(i.get("comments", []), i.get("summary", "")),
                last_comment(i.get("comments", [])), scope(i["labels"])))
        lines.append("")
    (root / "backlog" / "STATUS.md").write_text("\n".join(lines), encoding="utf-8")
    return len(issues)

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="..", help="repo root (default: parent of backend/)")
    ap.add_argument("--status-md", action="store_true",
                    help="regenerate backlog/STATUS.md from the on-disk JSON only — offline, no Jira fetch")
    args = ap.parse_args()
    root = Path(args.out).resolve()

    if args.status_md:
        print(f"STATUS.md: {write_status_md(root)} issues (offline)")
        return 0

    index = []
    for project in JIRA_PROJECTS:
        folder = root / "backlog" / project
        folder.mkdir(parents=True, exist_ok=True)
        for issue in jira_issues(project):
            rec = issue_record(issue)
            (folder / f"{rec['key']}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n",
                                                        encoding="utf-8", newline="\n")
            index.append({k: rec[k] for k in ("key", "type", "status", "priority", "summary", "labels", "epic")})
        print(f"jira {project}: {sum(1 for i in index if i['key'].startswith(project + '-'))} issues")
    index.sort(key=lambda r: (r["key"].split("-")[0], int(r["key"].split("-")[1])))
    (root / "backlog" / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                                                 encoding="utf-8", newline="\n")

    for space in CONFLUENCE_SPACES:
        folder = root / "docs" / "confluence" / space
        folder.mkdir(parents=True, exist_ok=True)
        pages = confluence_pages(space)
        for page in pages:
            (folder / f"{slug(page['title'])}.md").write_text(page_markdown(page, space), encoding="utf-8",
                                                              newline="\n")
        print(f"confluence {space}: {len(pages)} pages")

    print(f"STATUS.md: {write_status_md(root)} issues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
