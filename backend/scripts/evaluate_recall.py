"""Recall@8 benchmark evaluation for Anvil Knowledge Base.

Runs 10 domain-specific queries against the RTEMS documentation corpus in the KB,
measures whether the ground-truth document is retrieved within the top 8 hits,
and formats the results as a Markdown table.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from anvil.kb.config import KBSettings
from anvil.kb.store import KBStore


@dataclass(frozen=True)
class BenchmarkQuery:
    query: str
    expected_path_fragment: str
    topic: str


BENCHMARK_SUITE: list[BenchmarkQuery] = [
    BenchmarkQuery(
        query="Rate Monotonic period deadline scheduling",
        expected_path_fragment="c-user/rate-monotonic",
        topic="Rate Monotonic Manager",
    ),
    BenchmarkQuery(
        query="POSIX Mutex Priority Ceiling protocol",
        expected_path_fragment="posix-users/mutex",
        topic="POSIX Mutex",
    ),
    BenchmarkQuery(
        query="Barrier Synchronization rtems_barrier_create",
        expected_path_fragment="c-user/barrier",
        topic="Barrier Manager",
    ),
    BenchmarkQuery(
        query="Message Queue rtems_message_queue_receive timeout",
        expected_path_fragment="c-user/message",
        topic="Message Queue Manager",
    ),
    BenchmarkQuery(
        query="Interrupt Manager ISR processing rtems_interrupt_catch",
        expected_path_fragment="c-user/interrupt",
        topic="Interrupt Manager",
    ),
    BenchmarkQuery(
        query="Event Flags rtems_event_send rtems_event_receive",
        expected_path_fragment="c-user/event",
        topic="Event Manager",
    ),
    BenchmarkQuery(
        query="Counting Semaphores priority inheritance mutex",
        expected_path_fragment="c-user/semaphore",
        topic="Semaphore Manager",
    ),
    BenchmarkQuery(
        query="Fatal Error Manager rtems_fatal source code",
        expected_path_fragment="c-user/fatal-error",
        topic="Fatal Error Manager",
    ),
    BenchmarkQuery(
        query="Dual Ported Memory Manager rtems_port_create",
        expected_path_fragment="c-user/dual-ported-memory",
        topic="Dual-Ported Memory Manager",
    ),
    BenchmarkQuery(
        query="Task Manager rtems_task_create rtems_task_start",
        expected_path_fragment="c-user/task",
        topic="Task Manager",
    ),
]


def run_eval(k: int = 8) -> float:
    settings = KBSettings()
    hits_found = 0
    results: list[dict[str, object]] = []

    with KBStore(settings) as store:
        stats = store.stats()
        print(f"KB Stats before evaluation: {stats}")

        for item in BENCHMARK_SUITE:
            search_results = store.search(item.query, k=k)
            found_rank: int | None = None
            found_title: str = ""
            found_id: str = ""
            top_score: float = 0.0

            for rank, hit in enumerate(search_results, start=1):
                if item.expected_path_fragment in hit.external_id:
                    found_rank = rank
                    found_title = hit.title
                    found_id = hit.external_id
                    top_score = hit.score
                    break

            if found_rank is not None:
                hits_found += 1
                hit_str = f"Hit (rank {found_rank})"
            else:
                hit_str = "Miss"
                if search_results:
                    top_score = search_results[0].score
                    found_title = search_results[0].title
                    found_id = search_results[0].external_id

            results.append({
                "topic": item.topic,
                "query": item.query,
                "expected": item.expected_path_fragment,
                "hit": hit_str,
                "rank": found_rank if found_rank else "-",
                "score": f"{top_score:.4f}",
                "top_match": found_id,
            })

    recall_at_k = hits_found / len(BENCHMARK_SUITE)

    # Print markdown table
    print("\n### Recall@8 Evaluation Results\n")
    print(f"**Corpus Stats**: {stats['documents']} documents, {stats['chunks']} chunks")
    print(f"**Overall Recall@8**: {recall_at_k:.2%} ({hits_found}/{len(BENCHMARK_SUITE)})\n")
    print("| # | Topic | Query | Expected Target | Rank | Top Match | Score | Status |")
    print("|---|---|---|---|---|---|---|---|")
    for idx, r in enumerate(results, start=1):
        status_icon = "PASS" if r["hit"].startswith("Hit") else "FAIL"
        print(f"| {idx} | {r['topic']} | `{r['query']}` | `{r['expected']}` | {r['rank']} | `{r['top_match']}` | {r['score']} | {status_icon} |")

    return recall_at_k


if __name__ == "__main__":
    recall = run_eval(k=8)
    if recall < 0.8:
        print(f"\nERROR: Recall@8 {recall:.2%} is below required 80% threshold!", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"\nSUCCESS: Recall@8 {recall:.2%} meets requirement >= 80%.")
        sys.exit(0)

