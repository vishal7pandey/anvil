# State at freeze — what works, with evidence

Honest capability table for the scope freeze. `works` = run live with evidence named;
`partial` = runs but with a known limit; `unproven` = never run live. No invented numbers.

## Offline / local path (the demo target)

| Capability | State | Evidence |
|------------|-------|----------|
| Local source — folder / git working copy / archive / path with spaces | **works** | tiny plain-folder + space-path archive staged and ran (ANVIL-91/#94, gate experiment) |
| Local JSON tickets (`ANVIL_TICKETS_DIR`) | **works** | GATE-1/GATE-2 intake→run→comment→artifact end-to-end on file tickets |
| Approval gate (approve / reject / second card) | **works** | GATE-1 approved; GATE-2 rejected→agent revised→re-approved, live in the UI |
| Approvals appear without reload | **works** | ANVIL-96 polling merged (#108); second card showed after the retry |
| Session reopen — latest run's timeline, live reattach | **works** | ANVIL-73 merged (#79); `afterSeq` resumes the stream |
| Local knowledge folder + write-back | **works** | ANVIL-59/60 (#95): pages on disk, indexed for `kb_search`, evidence bound to the ticket |
| KB snapshot import, 0 gateway calls | **works** | 7,573 docs / 14,654 chunks, 367 s, unreachable gateway (precomputed vectors) |
| Sandbox image | **works** | hash `CA94D60C…` verified, image `bc17ec29fb13` loaded; SIS/RTEMS toolchain inside |
| Patch + bundle export (no GitHub) | **works** | `private/runs/<id>/{.patch,.bundle}` produced on local runs |
| Migration + offline start on a clean laptop | **works** | ANVIL-97 (#112): `alembic upgrade head`, offline mode, demo token |
| Verification hardening | **works** | ANVIL-99 (#113): checks run off the agent's shell + root-owned uploads |
| Security P0s | **works** | ANVIL-98 (#115): default-deny gate, bound tools, contained uploads/archives |
| `team_metrics.py` / `anvil.analyze` | **works** | metrics PRs merged (#104/#107); report generated 2026-11-04 |

## Never run live — the honest gaps

- **A full offline TC1 run to completion** — **unproven**. ANVIL-89 is still unmeasured; a whole
  offline TC1 (ticket→sandbox→acceptance→export) has not been taken to the end on this machine.
- **The online GitHub/Jira path** — **partial**. Intake, gate and PR flow proven earlier via real PRs
  and the gate experiment's outward calls; not re-run end-to-end against live GitHub/Jira in the
  current build.
- **TC2** — **partial**. Run 8 (2026-11-04) is a real agent upgrade that meets acceptance
  (245/245 after #63) — an upgrade run, not a clean end-to-end replay; a full TC2 replay in the
  current build is not on record.
- **TC3** — **unproven**. Two runs on 2026-11-04 both failed before finishing (a GitLab outage → #64,
  a KB-database failure → #67); no TC3 run has completed.
- **TC4** — **unproven**. Case spec exists; no run on record in the current build.

## Demo expectations — TC1 to TC4

| Case | What it needs | Show today | Gap |
|------|---------------|-----------|-----|
| TC1 (bug replay) | intake → sandbox → acceptance → patch | the mechanism works end-to-end on a tiny ticket | a **full** offline TC1 to completion is unmeasured (ANVIL-89) |
| TC2 | a case spec + its toolchain | spec exists | full run not on record |
| TC3 | " | " | " |
| TC4 | " | " | " |

## Online path last proven

Real GitHub/Jira: PRs merged and the gate experiment's outward `jira_comment` calls — the mechanism
was exercised, but a **fresh online end-to-end run in the current build is not on record**. The
offline/local path is the verified demo surface.
