"""The TC1 leak guard.

The TC1 eval asks the agent to fix an upstream bug whose merged fix is withheld.
If the upstream issue thread (or its fix discussion, MR, or fix commit) is in the
knowledge base, retrieval can hand the agent the answer. Two independent guards
keep it out:

* ingestion skips denied issues and drops chunks mentioning denied issues, MRs or fix commits;
* search filters them again at query time (by document ID and content), so a row that slipped
  in through a re-import or a manual load still cannot be returned.

The second guard is deliberately redundant: this is the one thing in the KB that
must not fail quietly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from .config import (
    TC1_EXCLUDED_COMMIT_PREFIXES,
    TC1_EXCLUDED_ISSUE_IDS,
    TC1_EXCLUDED_MR_IDS,
)

GITLAB_ISSUE_SOURCE = "gitlab-issue"
GITLAB_MR_SOURCE = "gitlab-mr"


def _build_leak_pattern(
    issue_ids: Sequence[str],
    mr_ids: Sequence[str],
    commit_prefixes: Sequence[str],
) -> re.Pattern[str]:
    patterns: list[str] = []
    for issue in issue_ids:
        # Match #5776, issue 5776, issues/5776, issue #5776
        patterns.append(rf"#\b{re.escape(issue)}\b")
        patterns.append(rf"(?i:\bissues?[\/#\s]+{re.escape(issue)}\b)")
    for mr in mr_ids:
        # Match !1493, MR 1493, merge_requests/1493, MR !1493
        patterns.append(rf"!\b{re.escape(mr)}\b")
        patterns.append(rf"(?i:\b(?:merge_requests?|mr)[\/#\s]+!?{re.escape(mr)}\b)")
    for prefix in commit_prefixes:
        # Match fix commit prefixes e.g. a62e60b5
        patterns.append(rf"(?i:\b{re.escape(prefix)})")
    if not patterns:
        return re.compile(r"(?!x)x")  # match nothing
    return re.compile("|".join(f"(?:{p})" for p in patterns))


@dataclass(frozen=True)
class ExclusionGuard:
    issue_ids: frozenset[str] = frozenset(TC1_EXCLUDED_ISSUE_IDS)
    mr_ids: frozenset[str] = frozenset(TC1_EXCLUDED_MR_IDS)
    commit_prefixes: tuple[str, ...] = TC1_EXCLUDED_COMMIT_PREFIXES
    _pattern: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        pattern = _build_leak_pattern(
            sorted(self.issue_ids),
            sorted(self.mr_ids),
            self.commit_prefixes,
        )
        object.__setattr__(self, "_pattern", pattern)

    def is_excluded(self, source: str, external_id: str) -> bool:
        """Return True when an entire document must not be ingested or returned."""
        if source == GITLAB_ISSUE_SOURCE:
            return _normalise_issue_id(external_id) in self.issue_ids
        if source == GITLAB_MR_SOURCE:
            return _normalise_mr_id(external_id) in self.mr_ids
        return False

    @property
    def is_empty(self) -> bool:
        """No withheld ids at all — a deployment without eval cases (ANVIL-100 #24 round 2)."""
        return not (self.issue_ids or self.mr_ids or self.commit_prefixes)

    def allows(self, source: str, external_id: str) -> bool:
        return not self.is_excluded(source, external_id)

    def is_content_excluded(self, text: str) -> bool:
        """Return True when text contains references to denied issues, MRs or fix commits."""
        return bool(self._pattern.search(text))

    def allows_content(self, text: str) -> bool:
        return not self.is_content_excluded(text)


def _normalise_issue_id(external_id: str) -> str:
    # GitLab iids arrive as ints, strings, "#5776" and project-scoped "rtems/rtos/rtems#5776".
    # The project is ignored: a withheld number is withheld in every project (safe side).
    return str(external_id).strip().rsplit("#", 1)[-1]


def _normalise_mr_id(external_id: str) -> str:
    # "rtems/rtos/rtems!1493", "!1493" or "1493".
    return str(external_id).strip().rsplit("!", 1)[-1]
