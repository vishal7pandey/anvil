---
name: investigate
description: Investigate an incident or symptom against the knowledge base (kb_search), web guidance (web_search) and the code - rank hypotheses with citations, discriminate look-alike post-mortems, derive conclusions from raw facts, reproduce what is reproducible in the sandbox, then file a bug / write findings back to RTKB and memory. Use for incident investigation, diagnosis without a code fix, and knowledge-base questions.
---

# Investigate (knowledge base incident investigation)

## When to use
A ticket reports ambiguous symptoms (timeouts, dropped buffers, degraded timing, weird status
codes) and asks "why", not "fix". See `docs/scenarios/tc4.md` for the TC4 scenario.

## Ground rules
- Cite everything: every hypothesis and every claim needs a source (KB chunk, doc URL,
  file:line, command output). A hypothesis with no citation is an idea, not a finding.
- The KB contains look-alike post-mortems on purpose. Before adopting one, check the current
  facts *disprove* it or it is only a neighbour of the truth.
- Prefer deriving from raw facts over reciting a summary: compute the numbers (queue depth x
  burst rate, period vs. actual execution) instead of quoting a conclusion.
- `web_search` only for outside facts (spec semantics, errata). Domain blocklists apply; never
  search for the withheld answer.
- An SME reviews your top hypothesis before you file a bug. Their rejections are signal:
  record them in `/memories/investigate/lessons.md` and re-rank.

## Steps (keep them as your write_todos plan)
1. **Restate the symptom set.** Error codes, timings, which indicators are *absent*
   (no watchdog trip, no leak, stack check clean). Negative evidence narrows the space.
2. **Search the KB.** `kb_search` per symptom: error codes, manager names, similar incidents.
   Fetch the top hits with page IDs, not just excerpts.
3. **Search code.** In the sandbox, find where each status is produced
   (`grep -rn RTEMS_TOO_MANY cpukit/`), the queue/Task creation, and the error path.
4. **Rank hypotheses.** A table: hypothesis / supporting citations / contradicting facts /
   confidence. Explicitly reject look-alikes with the contradicting evidence quoted.
5. **Reproduce if possible.** If a hypothesis predicts an observable effect, run the check or
   a small test in the sandbox and quote the output.
6. **SME gate.** Present the top hypothesis + the evidence trail. On rejection, write the
   rejection reason to `/memories/investigate/lessons.md`, re-rank and continue the loop.
7. **Deliverables.** Jira bug (`jira_create`, project RTEMS, labels `tc4`, never `anvil`) for
   the confirmed root cause; findings page to RTKB via `confluence_write`; a short KB
   write-back (post-mortem / memory entry) so the next run retrieves it.

## When to stop and hand off
Call `handoff` when: the evidence cannot discriminate the remaining hypotheses with the inputs
available; reproduction needs hardware or timing you cannot simulate; or the SME has rejected
twice without new information.
