<!-- Confluence ANVIL page 28639233, version 4, exported for offline use; parent 28475566 -->

# Anvil: status and plan to the 2026-11-10 demo

Living page, maintained by claude (PM). Last update: 2026-11-04 05:30 IST.

## Team (3 active agents, one laptop, one GitHub + Atlassian account)

| Agent | Owns | Open work | Reviews |
|---|---|---|---|
| claude | PM, architect, runtime, API (ANVIL-3); merges approved PRs | PR #51 (ANVIL-41), PR #52 (ANVIL-40), M1 re-run via intake (ANVIL-24/19), TC2 run 6 (ANVIL-21), ANVIL-43/44/47 | devin's and antigravity's PRs |
| devin | Integrations, TC1 eval runs | Review #51 (gates the rehearsals); ANVIL-42 second case; ANVIL-20 evidence | claude's PRs |
| antigravity | Scenarios, QA, runbook | Holds the build lock: 3 recorded TC1 rehearsals (ANVIL-32) after #51; PR #53 start-api (changes requested); review #52 |  |
| cline | Parked | ANVIL-45 mailing lists: claude takes it over if there's no reply by 11-04 12:00 |  |
| ~~codex~~, ~~copilot~~ | Out of credits | Work reassigned |  |

**Merge rule:** claude merges when the assigned reviewer has posted `REVIEW (<name>): APPROVE` after the last commit, CI is green, and there are no open change requests. Force-push is blocked: merge main into branches instead of rebasing.

## Milestones

| Milestone | Exit | Date | State |
|---|---|---|---|
| M0 Foundations | mirrors, trackers, image, local stack | 11-03 | **Done.** Demo image `anvil-sandbox-rtems:7.1` (local only; GHCR push waits on the owner, ANVIL-48) |
| M1 Walking skeleton | UI -> API -> agent -> sandbox streamed | 11-05 | **Nearly done.** Intake starts a real run 52 s after the label. The first M1 run got the fix right but died at `git_push` (fixed in #45). Still to do: the re-run through intake, and approve/reject/cancel on an interactive session (claude's lock slot) |
| M2 TC1 closed loop | ticket -> fix -> tests -> pre-commit -> PR -> CI -> merge; forced handoff | 11-07 | **On track after #51.** 3/3 runs found the upstream root cause (table below). Runs 2-3 handed off on the time budget, for a reason we found and fixed. Handoff path proven on RTEMS-4 (draft PR opened by the handoff) |
| M3 TC2 + TC3 | upgrade PR with SBOM/CVE delta; CRA artefacts | 11-08 | **At risk.** TC2 run 6 is in claude's lock slot. If it hands off, the fallback track (draft PR + CI `sbom` job) becomes the demo path |
| M4 TC4 + rehearsal | KB investigation, metrics, 3 clean TC1 runs | 11-09 | **KB complete:** 7,573 docs / 14,654 chunks (all RTEMS GitLab issues + MRs with comments, docs, synthetic Confluence). TC4 acceptance is 3/3 at rank 1 on the full KB. Rehearsals start after #51 merges |

## TC1 consistency (case rtems-5776, upstream fix a62e60b5, base `tc1/5776-base`)

| Run | Who | Outcome | Time | Files | Root cause (0-2) | Hunks vs upstream (±3) | Tests on erc32/SIS |
|---|---|---|---|---|---|---|---|
| 1 (9b562d72) | claude | succeeded | 18m40s | mutexinit.c, psx05/init.c (= upstream) | 2 | 2/3 | psx05 1/1; psxtests 87/87 |
| 2 (7bc18405) | devin | handoff at budget | 61m36s | mutexinit.c + a new test psxmutexinvalid01 (+ build spec) | 2 | 2/3 | new test passes, psx05 passes; no failing-before-fix evidence; full suite not run |
| 3 (0530b3c2) | devin | handoff at budget | 50m59s | mutexinit.c, psx05/init.c (= upstream) | 2 | 2/3 | psx05 **fails before the fix**, passes after; psxtests 87/87 |

* All three runs defer `the_mutex->flags = flags` until validation has passed. That is the upstream root cause; upstream stores the flags slightly later, after queue init, so 2/3 hunks is the ceiling for this equivalent fix.
* **Why runs 2-3 handed off (ANVIL-41):** each had its fix by minute 12-13. Each then ran `pre-commit run --all-files` over the RTEMS tree (30 min), although the skill says `--files`. Also, the budget was only checked between tool calls. **Decision: keep 45 min.** PR #51 makes the sandbox enforce the budget inside a command and refuse whole-repo pre-commit.
* Sandbox setup takes about 6 min before the first tool call. That's outside the budget but visible on a demo clock: a follow-up to look at.

## Changes since 11-03 21:20

* **Generic bug-replay cases (ANVIL-38, #48/#49):**

  * `cases/<id>.toml` declares the upstream fix, the withheld issues/MRs/commits, the base (`fix^` + infra overlay, built with git plumbing), the ticket and the scoring.
  * The KB leak guard is the union over all cases. No TC1 constants remain in the code.
* **KB is complete (ANVIL-39):** all RTEMS GitLab issues and MRs with comments, via GraphQL. The 6 withheld items of each kind are excluded, and leak scans are clean.
* **Fixes merged:** tool errors are returned to the agent instead of killing the run (#45); the intake re-runs a re-labelled ticket after a finished run (#50).
* **Open PRs:**

  * #51 time budget (review: devin)
  * #52 "push before opening a PR" (review: antigravity)
  * #53 start-api script (changes requested: secrets in a temp file)
* **Discord:** the RTEMS Discord belongs to the RTEMS project. Reading it needs a bot invited by their admins; we don't scrape it with a user token. The KB uses GitLab now, and mailing lists are next (ANVIL-45).

## Build lock queue (`comms/locks/rtems-build.lock`)

antigravity (3 rehearsals, after #51) -> claude (M1 re-run via intake; TC2 run 6).

## Decisions (still valid)

* Harness: LangChain Deep Agents. Gateway: LiteLLM -> OpenAI. One Docker sandbox per session.
* No webhooks: PR and check state are polled. RTEMS tickets get the label `anvil-in-review`.
* `git_push` and `github_merge` refuse `.github/` changes on the host side. In interactive mode, outward tools pause for approval; in autonomous mode, only merge does.
* Answer-leak controls:

  * single-branch clone, and the sandbox goes offline after checkout;
  * the KB excludes the withheld set of every case (a time-based guard is planned in ANVIL-43);
  * scoring against the upstream fix runs host-side only.
* The `anvil` label on an RTEMS ticket starts a real run.
* Run budgets: 45 min, 150 tool calls; the time budget is enforced inside commands since #51.

## Risks

| Risk | Mitigation |
|---|---|
| Rehearsals start before #51 | antigravity holds them until claude says "merged, go" |
| Reviews stall overnight (agents idle) | Owner nudges devin/antigravity; claude merges only with reviewer approval |
| TC2 doesn't converge before 11-10 | Run 6 next; fallback track ready |
| One laptop, one build lock, 10+ stale sandboxes | Strict lock queue; cleanup in ANVIL-47 |
| Scope creep before 11-10 | TC1 must be flawless. TC2-TC4 degrade first |
