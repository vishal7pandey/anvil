"""Evaluate TC4 acceptance criteria against the live knowledge base.

Asserts that each of the 3 scenario questions from docs/scenarios/tc4.md
retrieves its designated synthetic Confluence page at Rank 1 or 2.

Usage:
    cd backend
    uv run python scripts/evaluate_tc4_acceptance.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from anvil.kb.config import KBSettings
from anvil.kb.store import KBStore


@dataclass(frozen=True)
class TC4Query:
    id: str
    description: str
    query: str
    expected_title_fragment: str
    expected_page_id: str


TC4_QUERIES: list[TC4Query] = [
    TC4Query(
        id="Q1-QUEUE-SIZING",
        description="Queue Sizing & Burst Telemetry Specifications",
        query="telemetry queue buffer specifications capacity burst arrival rate",
        expected_title_fragment="Payload Telemetry Flow and Queue Specifications",
        expected_page_id="28868609",
    ),
    TC4Query(
        id="Q2-LOOKALIKE-INCIDENT",
        description="Look-alike Discrimination (2025 Mutex Priority Inversion vs 2026 Queue Overflow)",
        query="2025 payload guidance timing overrun incident root cause RTEMS_TIMEOUT",
        expected_title_fragment="Post-Mortem: 2025 Payload Guidance Timing Overrun Incident",
        expected_page_id="28901377",
    ),
    TC4Query(
        id="Q3-TRIAGE-RUNBOOK",
        description="Real-Time Directive Failure & Deadline Overrun Triage",
        query="concurrent RTEMS_TOO_MANY and rate monotonic period overrun triage steps",
        expected_title_fragment="Runbook: Real-Time Deadline Overrun & Directive Failure Triage",
        expected_page_id="28934145",
    ),
]


def run_evaluation() -> int:
    settings = KBSettings()
    print("=" * 70)
    print("ANVIL TC4 ACCEPTANCE EVALUATION: CONFLUENCE SYNTHETIC KB RETRIEVAL")
    print(f"Database: {settings.database_url}")
    print("=" * 70)

    all_passed = True

    with KBStore(settings) as store:
        stats = store.stats()
        print(f"KB Total Documents: {stats['documents']}, Total Chunks: {stats['chunks']}\n")

        for idx, item in enumerate(TC4_QUERIES, start=1):
            print(f"Query {idx} [{item.id}]: {item.description}")
            print(f"  Input: \"{item.query}\"")
            print(f"  Expected: \"{item.expected_title_fragment}\" (Page ID: {item.expected_page_id})")

            hits = store.search(item.query, k=5)
            if not hits:
                print("  Result: FAIL (No hits returned)")
                all_passed = False
                continue

            target_rank = None
            for rank, hit in enumerate(hits, start=1):
                if (
                    item.expected_page_id == hit.external_id
                    or item.expected_title_fragment.lower() in hit.title.lower()
                ):
                    target_rank = rank
                    target_hit = hit
                    break

            if target_rank is not None and target_rank <= 2:
                print(
                    f"  Result: PASS (Found at Rank {target_rank}/{len(hits)}, "
                    f"Score: {target_hit.score:.6f}, Title: '{target_hit.title}')"
                )
            elif target_rank is not None:
                print(
                    f"  Result: FAIL (Target found but at Rank {target_rank} > 2, "
                    f"Score: {target_hit.score:.6f}, Title: '{target_hit.title}')"
                )
                all_passed = False
            else:
                top_titles = [f"#{r}: {h.title} ({h.source})" for r, h in enumerate(hits[:3], 1)]
                print(f"  Result: FAIL (Target not in top {len(hits)} hits. Top hits: {top_titles})")
                all_passed = False
            print()

    print("=" * 70)
    if all_passed:
        print("OVERALL EVALUATION: 3/3 PASS (100.0% TC4 Acceptance Criteria Met)")
        print("=" * 70)
        return 0
    else:
        print("OVERALL EVALUATION: FAIL (Some criteria were not met)")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(run_evaluation())
