"""The TC1 leak guard.

The TC1 eval asks the agent to fix an upstream bug whose merged fix is withheld.
If the upstream issue thread (or its fix discussion) is in the knowledge base,
retrieval can hand the agent the answer. Two independent guards keep it out:

* ingestion skips the denied issues, so they are never embedded;
* search filters them again at query time, so a row that slipped in through a
  re-import or a manual load still cannot be returned.

The second guard is deliberately redundant: this is the one thing in the KB that
must not fail quietly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import TC1_EXCLUDED_ISSUE_IDS

GITLAB_ISSUE_SOURCE = "gitlab-issue"


@dataclass(frozen=True)
class ExclusionGuard:
    issue_ids: frozenset[str] = frozenset(TC1_EXCLUDED_ISSUE_IDS)

    def is_excluded(self, source: str, external_id: str) -> bool:
        """Return True when a document must not be ingested or returned."""
        if source != GITLAB_ISSUE_SOURCE:
            return False
        return _normalise_issue_id(external_id) in self.issue_ids

    def allows(self, source: str, external_id: str) -> bool:
        return not self.is_excluded(source, external_id)


def _normalise_issue_id(external_id: str) -> str:
    # GitLab iids arrive as ints, strings and occasionally "#5776".
    return str(external_id).strip().lstrip("#")
