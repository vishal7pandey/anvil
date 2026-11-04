"""Local ticket tracker: JSON files in status folders as an alternative to Jira (ANVIL-56).

Team laptops have no Jira. A tracker is a directory (``ANVIL_TICKETS_DIR``) with one folder per
status — ``todo/``, ``in-progress/``, ``done/`` — and one ``<KEY>.json`` per ticket, in the same
schema as ``backlog/*.json``. The folder is the status; a transition moves the file. The ``jira_*``
agent tools route here for runs whose ticket came from this store, so skills and tool names do not
change.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from anvil.tools.errors import ToolError

STATUSES = {"todo": "To Do", "in-progress": "In Progress", "done": "Done"}
_FOLDERS = {v: k for k, v in STATUSES.items()}


def _folder_for(status: str) -> str:
    for name, folder in _FOLDERS.items():
        if status.casefold() in (name.casefold(), folder.casefold()):
            return folder
    raise ToolError(f"'{status}' is not a ticket status. Available: {', '.join(STATUSES.values())}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalTracker:
    """Read and write the ticket JSON files under a tracker directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        for folder in STATUSES:
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "LocalTracker | None":
        directory = os.environ.get("ANVIL_TICKETS_DIR")
        return cls(directory) if directory else None

    # -- file access ------------------------------------------------------

    def _iter(self) -> Iterator[tuple[str, Path, dict[str, Any]]]:
        for folder in STATUSES:
            directory = self.root / folder
            for path in sorted(directory.glob("*.json")):
                try:
                    yield folder, path, json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue

    def _find(self, key: str) -> tuple[str, Path, dict[str, Any]]:
        for folder, path, data in self._iter():
            if data.get("key") == key or path.stem == key:
                return folder, path, data
        raise ToolError(f"ticket {key} not found under {self.root}")

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _summary(self, folder: str, path: Path, data: dict[str, Any], comments: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": data.get("key") or path.stem,
            "url": str(path),
            "summary": data.get("summary"),
            "status": STATUSES[folder],
            "type": data.get("type"),
            "labels": data.get("labels", []),
            "assignee": data.get("assignee"),
            "description": (data.get("description") or "").strip(),
            "repo": data.get("repo"),
        }
        if comments:
            out["comments"] = [
                {"author": c.get("author"), "created": c.get("created"), "body": c.get("body", "").strip()}
                for c in data.get("comments", [])
            ]
        return out

    # -- the jira_* surface -----------------------------------------------

    def get(self, key: str) -> dict[str, Any]:
        return self._summary(*self._find(key), comments=True)

    def search(self, query: str, max_results: int = 20) -> list[dict[str, Any]]:
        """AND-joined 'field = value' clauses over {project, labels, statusCategory, status, key}
        plus 'text ~ "..."' substring search; anything else raises ToolError (fail loud, not wrong)."""
        clauses = [c.strip() for c in re.split(r"\s+AND\s+", query or "", flags=re.I) if c.strip()]
        equals = dict(re.findall(r"(\w+)\s*=\s*\"?([\w /.-]+?)\"?\s*(?=AND|$)", query or "", re.I))
        text_terms = re.findall(r"text\s*~\s*\"([^\"]+)\"", query or "", re.I)
        known = {"project", "labels", "statuscategory", "status", "key", "orderby", "created", "text"}
        if any(re.match(r"(\w+)\s*[!=~]", c) and c.split()[0].rstrip("!=~").casefold() not in known
               for c in clauses):
            raise ToolError(f"local ticket search cannot evaluate '{query}': fields are {sorted(known)}")
        out = []
        for folder, path, data in self._iter():
            summary = self._summary(folder, path, data)
            haystack = f"{summary['key']} {summary['summary'] or ''} {summary['description']}"
            if equals.get("key") and summary["key"] != equals["key"]:
                continue
            if equals.get("labels") and equals["labels"] not in summary["labels"]:
                continue
            if equals.get("statuscategory") and equals["statuscategory"].casefold() != summary["status"].casefold():
                continue
            if equals.get("status") and equals["status"].casefold() != summary["status"].casefold():
                continue
            if equals.get("project") and not summary["key"].startswith(equals["project"] + "-"):
                continue
            if any(term.casefold() not in haystack.casefold() for term in text_terms):
                continue
            out.append(summary)
            if len(out) >= max_results:
                break
        return out

    def candidates(self, max_results: int = 20) -> list[dict[str, Any]]:
        """todo/ tickets labelled `anvil`, oldest first: what the intake poller should start."""
        out = []
        for path in sorted((self.root / "todo").glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # one malformed file must not block the other candidates
            if "anvil" in (data.get("labels") or []):
                out.append(self._summary("todo", path, data))
            if len(out) >= max_results:
                break
        return out

    def comment(self, key: str, markdown: str) -> dict[str, str]:
        folder, path, data = self._find(key)
        data.setdefault("comments", []).append({"created": _now(), "body": markdown})
        data["updated"] = _now()
        self._write(path, data)
        return {"id": f"c{len(data['comments'])}", "url": str(path)}

    def transition(self, key: str, to_status: str) -> dict[str, str]:
        folder, path, data = self._find(key)
        target = _folder_for(to_status)
        name = STATUSES[target]
        if folder == target:
            return {"key": data.get("key") or path.stem, "status": name}
        data["status"] = name
        data["updated"] = _now()
        new_path = self.root / target / path.name
        self._write(new_path, data)
        path.unlink()
        return {"key": data.get("key") or path.stem, "status": name}

    def set_labels(self, key: str, add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
        if not add and not remove:
            raise ToolError("jira_set_labels needs at least one label to add or remove")
        _, path, data = self._find(key)
        labels = [l for l in data.get("labels", []) if l not in (remove or [])]
        labels += [l for l in add or [] if l not in labels]
        data["labels"] = labels
        data["updated"] = _now()
        self._write(path, data)
        return {"key": data.get("key") or path.stem, "added": add or [], "removed": remove or []}

    def create(self, project: str, type: str, summary: str, description: str = "",
               labels: list[str] | None = None) -> dict[str, str]:
        highest = 0
        for _, _, data in self._iter():
            m = re.fullmatch(rf"{re.escape(project)}-(\d+)", str(data.get("key") or ""))
            if m:
                highest = max(highest, int(m.group(1)))
        key = f"{project}-{highest + 1}"
        path = self.root / "todo" / f"{key}.json"
        self._write(path, {
            "key": key, "type": type, "status": "To Do", "summary": summary,
            "description": description, "labels": labels or [], "comments": [],
            "created": _now(), "updated": _now(),
        })
        return {"key": key, "url": str(path)}
