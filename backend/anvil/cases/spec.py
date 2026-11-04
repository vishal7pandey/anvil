"""Bug-replay eval cases (ANVIL-38): one TOML file per case, nothing case-specific in code.

A case turns an upstream fix into a task the factory can be measured on:

* **base**: the repo as it was just before the fix (parent of the first fix commit), plus the
  factory's own infrastructure files (CI, pre-commit) overlaid from an infra ref;
* **ticket**: the symptoms as a reporter would write them; never the fix;
* **withheld**: everything that would hand the agent the answer (the upstream issues, merge
  requests and fix commits). The KB leak guard uses the union over all cases;
* **as_of**: when the fix landed. Knowledge created after it is "from the future" for this case;
* **scoring**: what to compare the agent's diff with;
* **acceptance**: checks the result must pass before a run counts as succeeded (ANVIL-44). Each
  names a script in ``anvil/cases/checks/`` and its arguments; the host uploads the script into the
  sandbox after the agent has finished and runs it there (see ``anvil.cases.acceptance``).

Case files live in ``<repo>/cases/*.toml`` (``ANVIL_CASES_DIR`` overrides). They are host-side
only: the sandbox gets skills, never cases.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CASES_DIR = Path(__file__).resolve().parents[3] / "cases"
CHECKS_DIR = Path(__file__).resolve().parent / "checks"


class CaseError(ValueError):
    pass


@dataclass(frozen=True)
class Overlay:
    ref: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    name: str
    script: str                     # file name in anvil/cases/checks/
    args: tuple[str, ...] = ()      # "{base_sha}" is replaced with the case's pinned base commit

    def argv(self, case: "Case") -> list[str]:
        # Commits, not ref names: the agent can move refs in its sandbox (devin's review of #60).
        return [a.replace("{base_sha}", case.base_sha or "") for a in self.args]


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    repo: str                       # mirror the agent works on, "owner/name"
    fix_commits: tuple[str, ...]    # upstream fix, oldest first; base = parent of the first
    base_branch: str
    as_of: str
    ticket_project: str
    ticket_summary: str
    ticket_body: str
    ticket_key: str | None = None
    ticket_labels: tuple[str, ...] = ()
    base_sha: str | None = None     # pin, when the base already exists
    overlay: Overlay | None = None
    upstream_url: str | None = None
    upstream_project: str | None = None
    withheld_issues: tuple[str, ...] = ()
    withheld_mrs: tuple[str, ...] = ()
    withheld_commits: tuple[str, ...] = ()
    target_files: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()
    tolerance: int = 3
    acceptance: tuple[Check, ...] = ()
    required_checks: tuple[str, ...] = ()   # CI checks that gate the PR; others are advisory
    web_allow: tuple[str, ...] = ()         # [web] allow: trusted web_search sources (ANVIL-64); () = any
    build: dict[str, str] = field(default_factory=dict)  # [build]: the BSP's configure/build/test recipe
    path: Path | None = field(default=None, compare=False)

    @property
    def ticket_text(self) -> str:
        """The ticket body with the code-base line the Jira intake needs (ANVIL-24)."""
        line = f"Code base: branch `{self.base_branch}` of github.com/{self.repo}."
        return self.ticket_body if line in self.ticket_body else f"{self.ticket_body.rstrip()}\n\n{line}\n"


def _strs(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, (str, int)) for v in value):
        raise CaseError(f"{where}: expected a list of strings")
    return tuple(str(v) for v in value)


def _build(value: object) -> dict[str, str]:
    """[build] is a table of step name -> shell command (ANVIL-90). Empty or missing is fine."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                             for k, v in value.items()):
        raise CaseError("build must be a table of step names to command strings")
    return dict(value)


def _checks(value: object, where: str) -> tuple[Check, ...]:
    if not isinstance(value, list):
        raise CaseError(f"{where}: acceptance.checks must be a list of tables")
    checks = []
    for item in value:
        if not isinstance(item, dict) or "name" not in item or "script" not in item:
            raise CaseError(f"{where}: every acceptance check needs name and script")
        if Path(item["script"]).name != item["script"] or not (CHECKS_DIR / item["script"]).is_file():
            raise CaseError(f"{where}: acceptance script {item['script']} not found in {CHECKS_DIR.name}/")
        checks.append(Check(name=item["name"], script=item["script"], args=_strs(item.get("args"), where)))
    return tuple(checks)


def load_case(path: Path) -> Case:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CaseError(f"{path.name}: {exc}") from exc
    try:
        base, ticket = raw["base"], raw["ticket"]
        upstream, withheld = raw.get("upstream", {}), raw.get("withheld", {})
        scoring = raw.get("scoring", {})
        body = ticket.get("body")
        if body is None and "body_file" in ticket:
            body = (path.parent / ticket["body_file"]).read_text(encoding="utf-8")
        if not body:
            raise CaseError(f"{path.name}: ticket.body or ticket.body_file is required")
        overlay = None
        if "overlay" in base:
            overlay = Overlay(ref=base["overlay"]["ref"], paths=_strs(base["overlay"]["paths"], "base.overlay.paths"))
        fix = _strs(upstream.get("fix_commits"), "upstream.fix_commits")
        if not fix:
            raise CaseError(f"{path.name}: upstream.fix_commits is required")
        case = Case(
            id=raw["id"], kind=raw.get("kind", "defect-fix"), repo=raw["repo"], fix_commits=fix,
            base_branch=base["branch"], base_sha=base.get("sha"), overlay=overlay, as_of=str(raw["as_of"]),
            ticket_project=ticket["project"], ticket_key=ticket.get("key"), ticket_summary=ticket["summary"],
            ticket_body=body, ticket_labels=_strs(ticket.get("labels"), "ticket.labels"),
            upstream_url=upstream.get("url"), upstream_project=upstream.get("project"),
            # The upstream issue and fix are always withheld, whether or not the file repeats them.
            withheld_issues=tuple(dict.fromkeys(_strs(upstream.get("issues"), "upstream.issues")
                                                + _strs(withheld.get("issues"), "withheld.issues"))),
            withheld_mrs=tuple(dict.fromkeys(_strs(upstream.get("mrs"), "upstream.mrs")
                                             + _strs(withheld.get("mrs"), "withheld.mrs"))),
            withheld_commits=tuple(dict.fromkeys(fix + _strs(withheld.get("commits"), "withheld.commits"))),
            target_files=_strs(scoring.get("target_files"), "scoring.target_files"),
            test_files=_strs(scoring.get("test_files"), "scoring.test_files"),
            tolerance=int(scoring.get("tolerance", 3)),
            acceptance=_checks(raw.get("acceptance", {}).get("checks", []), path.name),
            required_checks=_strs(raw.get("ci", {}).get("required_checks"), "ci.required_checks"),
            web_allow=_strs(raw.get("web", {}).get("allow"), "web.allow"),
            build=_build(raw.get("build")), path=path,
        )
        if not case.base_sha and any("{base_sha}" in a for c in case.acceptance for a in c.args):
            raise CaseError(f"{path.name}: acceptance uses {{base_sha}} but base.sha is not pinned")
    except KeyError as exc:
        raise CaseError(f"{path.name}: missing required key {exc}") from exc
    if case.id != path.stem:
        raise CaseError(f"{path.name}: id '{case.id}' must match the file name")
    return case


def cases_dir() -> Path:
    return Path(os.environ.get("ANVIL_CASES_DIR") or DEFAULT_CASES_DIR)


def load_cases(directory: Path | None = None) -> list[Case]:
    directory = directory or cases_dir()
    if not directory.is_dir():
        return []
    return [load_case(p) for p in sorted(directory.glob("*.toml"))]


def get_case(case_id: str, directory: Path | None = None) -> Case:
    for case in load_cases(directory):
        if case.id == case_id:
            return case
    raise CaseError(f"no case '{case_id}' in {directory or cases_dir()}")


@dataclass(frozen=True)
class Withheld:
    issues: tuple[str, ...]
    mrs: tuple[str, ...]
    commits: tuple[str, ...]


def withheld_union(cases: Iterable[Case]) -> Withheld:
    """Everything any case withholds: the KB must not hold it, whichever case runs."""
    issues: dict[str, None] = {}
    mrs: dict[str, None] = {}
    commits: dict[str, None] = {}
    for case in cases:
        issues.update(dict.fromkeys(case.withheld_issues))
        mrs.update(dict.fromkeys(case.withheld_mrs))
        commits.update(dict.fromkeys(case.withheld_commits))
    return Withheld(tuple(issues), tuple(mrs), tuple(commits))


def case_for_run(run: dict, directory: Path | None = None) -> Case | None:
    """The case a run replays, resolved host-side: ``run["case_id"]`` when the runner set it, else the
    case whose ticket key is the run's Jira key. The model never chooses it."""
    case_id, jira_key = run.get("case_id"), run.get("jira_key")
    for case in load_cases(directory):
        if (case_id and case.id == case_id) or (jira_key and case.ticket_key == jira_key):
            return case
    return None
