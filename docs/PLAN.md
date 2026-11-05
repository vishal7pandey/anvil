# Anvil: plan of record

Snapshot of **2026-11-05, 11:30 IST** (main after #132, plus this file). Written so that work can resume on another
machine **without Jira and without the old laptop**. The full backlog is in [`backlog/`](../backlog/): one JSON
file per ticket, with comments, plus [`backlog/STATUS.md`](../backlog/STATUS.md) (one table per epic). Refresh it with
`uv run python scripts/export_trackers.py` (needs Jira) or `--status-md` (offline). Jira stays the live board when it's
reachable; if they disagree, Jira wins and this file is re-exported.

## 1. Where things stand

| Capability | State | Evidence |
|---|---|---|
| **Offline TC1, end to end** (local ticket → sandbox → fix + test on the simulator → patch export) | **works, 1 run** | run `e115a19f`, 11-05: **succeeded in 22.4 min**, no GitHub/Jira. The fix matches the known root cause (`the_mutex->flags` is written only after every attribute check passes), with a new `psxmutexinit01` simulator test. ANVIL-88 and ANVIL-89 are closed on it. |
| Online TC1 (Jira intake → PR → CI) | works, not clean | 10 earlier runs: 6 handoffs, 3 failed, and 1 "succeeded" with its PR against `main`, so no CI ran. All the causes are fixed (#57, #61, #65). ANVIL-32 still wants 3 clean runs. |
| TC2 (lwIP 2.1 → 2.2.1 upgrade) | partial | Run 8 (11-04, outside the API) passed the acceptance check 245/245 after #63. The API runs 7e81c6a2 and 7772117c failed or handed off. |
| TC3 (CRA review) | unproven | Both 11-04 runs died in setup (GitLab outage → #64; KB database → #67). No completed run. |
| TC4 (KB) | retrieval works | 3/3 acceptance queries at rank 1 (ANVIL-32 comment, 11-04). |
| Local project in any shape, local JSON tickets, approval gate, session reopen, KB write-back, security P0s | works | [`docs/STATE-AT-FREEZE.md`](STATE-AT-FREEZE.md) has the evidence per row. |

## 2. Decisions in force

- **There is no scope freeze.** The 11-05 hand-over to the juniors was a pause to get things in order. Development
  continues, and "post-demo" labels do not mean "wait".
- **The demo path is offline, on the team's laptops**: a local folder or archive plus local JSON tickets plus the
  sandbox image file plus the KB snapshot. The online GitHub/Jira path is kept, but it's secondary.
- **Build first; end-to-end runs only when they prove something.** Unit tests plus quick real-container tests are the
  default proof. Every real run gets `python -m anvil.analyze <run_id>`, and its numbers go on the ticket.
- **SonarCloud is advisory.** Each case's `[ci] required_checks` (`build-test`, `CodeQL (C/C++)`) decides; GitHub
  branch protection overrides that when it's set.
- **Merge rule:** one ticket per PR, each with a regression test. The named reviewer's APPROVE must come after the
  last commit, with CI green and no open change request. Report numbers, not adjectives, and verify claims against
  GitHub, not messages.
- **Fixes go in the host or the tools, not in prompt rules.** When the agent misbehaves, change what it can do (a tool,
  a refusal, a check) rather than adding "please don't" text. Examples: ANVIL-74 `lint_changed`, ANVIL-103, ANVIL-105.
- **The answer-leak controls are non-negotiable**: a single-branch base, an offline sandbox after checkout, and the
  KB time guard plus the withheld-fix guard. Anything that weakens them gets a change request (see #126).

## 3. What the run data says (11-05 cross-run analysis, details on ANVIL-62)

`anvil.analyze` was run over all 14 real case runs.

- **Tools take 50-85% of wall time.** Builds and tests (`execute`) are only 4-10 min of that; the rest is file
  operations (about 2.5 s each) and subagents.
- **ANVIL-89 worked.** Subagent input went from 5.15M to 0.41M tokens per TC1 run, about 6K per call instead of 26K.
  Total input went from 6.42M to 1.87M.
- **The main agent's context is now the cost:** 19-27K input tokens per call. That's ANVIL-106.
- **63 wasted round-trips over 10 runs:** the agent passes `execute` timeouts in milliseconds, and the tool refuses
  them. That's ANVIL-103, the cheapest win.
- **The build recipe reaches the agent but isn't followed;** it guesses configure variants. That's ANVIL-105: the
  recipe becomes tools.
- **Gaps from the offline run:** the local ticket gets no completion comment (ANVIL-104), and new files copy a
  third party's copyright line (ANVIL-107).

## 4. Team and queues (in order)

Four agents. All share one GitHub and one Jira account, so they sign every comment `(name)` and never write `@agent`.
Reviewer in brackets.

**claude** (PM/architect) [devin]
1. ANVIL-103: execute timeout units, plus exact command durations on `sandbox.exec`.
2. ANVIL-105: the case's build recipe as `configure` / `build` / `test_one` tools.
3. ANVIL-106: the main agent's context: compact earlier, keep subagent returns short.
4. ANVIL-107: copyright headers.
5. ANVIL-32: 2 more clean TC1 runs (offline), measuring the levers with `anvil.analyze`.
6. ANVIL-50: TC3 re-run, then ANVIL-65 (offline SAST/SCA scanners), then ANVIL-21 (TC2 primary path).
7. ANVIL-62 (umbrella) closes when two levers show a measured cut.

**devin** [claude]
1. ANVIL-100 (audit P1), the checklist on the ticket:
   - #123 needs main merged in;
   - #124 and #126 have change requests.

   Merged so far: #6, #7, #9, #11, #12, #13, #20, #21+#23, #22, #38.
2. ANVIL-104: the local tracker writes a completion comment and moves the file per `merge_policy`.
3. ANVIL-102: ruff in CI, blocking on changed files, with a ratchet baseline for the existing 218 findings.
4. ANVIL-82: the `vendor_reimport` three-way-merge tool for TC2 (proven by unit test first). Then ANVIL-66, the
   version registry.

**antigravity** [devin]
1. ANVIL-85: OpenTelemetry traces (run → model → tool → sandbox command), with a local offline viewer.
2. ANVIL-67: faster KB import (index after the bulk load, batching) and incremental refresh. Baseline: 367 s.
3. ANVIL-69: configurable embedding dimension.

**cline** [antigravity]: needs the owner to open a session.
1. ANVIL-70: route a ticket to its repo from declared metadata.
2. ANVIL-100 #48: the frontend catches a rejected `sendMessage`.
3. ANVIL-45: mailing-list loader for the KB.

## 5. Open PRs at this snapshot

| PR | Ticket | State |
|---|---|---|
| #123 | ANVIL-100 #8: startup recovery of orphaned runs | approved; conflicts with #121, needs main merged in |
| #124 | ANVIL-100 #17: `github_checks` and "no checks" | changes requested: a grace period before returning `none` |
| #126 | ANVIL-100 #24: `kb.config` without cases | changes requested: KB-writing paths must refuse while the leak guard is empty |

## 6. Waiting on the owner (also on ANVIL-48)

1. **ANVIL-82:** OK to show TC2 with a tool doing the mechanical three-way merge, as long as the demo says so?
2. **ANVIL-94:** the 3 questions on generative UI (design page: `docs/confluence/ANVIL/design-generative-ui-over-ag-ui-in-the-anvil-chat.md`).
3. **ANVIL-53:** go or no-go on the workspace redesign.
4. Open a **cline** session.
5. Housekeeping:
   - revoke the SonarCloud user token, and delete the unused "Sonar way - no coverage" gate;
   - GHCR push of the 7.1 image;
   - Atlassian token rotation;
   - RTEMS Discord access.

## 7. Resuming on a new machine (ANVIL-109)

1. Clone the repo, then follow [`docs/SETUP.md`](SETUP.md). `.env` comes from `.env.example`; it is never copied
   through git.
2. **Not in git, copy these from the old laptop's `private/handover/`** (or regenerate them, see
   [`docs/ASSETS.md`](ASSETS.md)):
   - `anvil-sandbox-rtems-7.1.tar` (1.58 GB, sha256 `ca94d60c…`): load it with `scripts/import-sandbox-image.ps1`;
   - `kb-snapshot-vectors.jsonl.gz` and its manifest (88 MB): put both in `data/kb/`, then
     `import-snapshot --from data/kb`;
   - `rtems-tc1-5776-base.bundle` and `tickets/todo/RTEMS-1.json`: the offline TC1 input (README.txt in that folder).
3. Run `scripts/verify-assets.ps1`, then one offline TC1 run (the ticket's `Code base:` path must match where you
   clone the bundle).
4. **Left behind on purpose:**
   - the Postgres run history (the numbers are in section 3 and on ANVIL-62/88/89);
   - the agent mailboxes in `C:\Dev\comms` (this file replaces them as the source of truth);
   - old worktrees.
5. Several APIs on one machine: give each its own port and database
   (`start-api.ps1 -Port 8010 -DatabaseUrl …/anvil_<agent>`), and point the UI at one with
   `ANVIL_API_PROXY=http://127.0.0.1:8010 npm run dev` (ANVIL-108).
