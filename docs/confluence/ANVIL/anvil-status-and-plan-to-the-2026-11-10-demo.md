<!-- Confluence ANVIL page 28639233, version 5, exported for offline use; parent 28475566 -->

# Anvil: status and plan to the 2026-11-10 demo

# Anvil status: 2026-11-05 (night)

_Maintained by claude (PM/architect). Source of truth: GitHub PRs and the ANVIL board (138)._

## Goals

* **Friday 11-06:** the owner's team demos Anvil on their own laptops, **offline** (no GitHub, no Jira).
* **Thursday morning:** the codebase is handed to the owner's juniors. They must be able to point the factory at their own project, in any shape.
* **11-10:** RFP demo (TC1-TC4).

## Done (merged)

* **Offline path:**

  * local folder source, with a patch and a bundle out (#74);
  * local JSON ticket tracker (#72);
  * KB snapshot with vectors (#73);
  * sandbox image as a file, checksummed (#77);
  * SETUP.md (#82).
* **Correctness (from the DFMEA):**

  * graph thread per run (#69);
  * one active run per session (#76);
  * handoff ends the run (#75);
  * user bootstrap race (#70);
  * push lock (#87);
  * intake backoff (#84);
  * CI polling respects cancel and the time budget (#85);
  * lint only the changed files (#71).
* **Observability:**

  * per-step timing and `python -m anvil.analyze` (#81);
  * run-tagged JSON logs plus full command output (#83).
* **UI:**

  * the chat shows a run as a conversation (#68);
  * New Session dialog (#86);
  * cost meter (#88);
  * app icon (#78).
* **Tests:** the offline chain test, local ticket → patch, with no LLM in about 4 min (#80).

## Why runs were slow (rehearsal 2, 48 min, measured with `anvil.analyze`)

* Tools 27 min, model 18 min.
* The builder subagent used **5.15M input tokens**, because each command could put up to 512 KB of output into its context. Fix: #91 bounds this to about 16 KB, with the full output kept on disk. Measured in Thursday's dry run.
* Five guessed `waf configure` variants. Fix: #90, a build recipe in the case.
* About 2.5 s per sandbox file operation (post-Friday lever).

## Open for Thursday

| Item | Owner | State |
|---|---|---|
| ANVIL-91: a local project in any shape (plain folder, any branch, archive, spaces) | claude | PR going up tonight |
| ANVIL-92: junior hand-over (export, HANDOFF.md, verified zip) | devin | started |
| #91: bounded command output | claude | waiting on devin's re-review |
| #90: build recipe | devin | changes requested (prove it runs) |
| #92: stuck-detector loopholes | devin | changes requested |
| #89: sandbox reaper | antigravity | changes requested (ownership) |
| #79: reopen a session | cline | no new commits; deadline 09:30, then devin |
| ANVIL-88: hand-over files | all | TC1 bundle, ticket and KB done; image tar pending (antigravity) |
| Dry run from SETUP.md + one real offline TC1 run | antigravity | 15:00-18:00 |

## Team rules (since the 11-04 retro)

* Every PR has a `Proven by:` line naming a test on the real path.
* Status = GitHub.
* An approval doesn't override an open CHANGES REQUESTED.
* Docs are executed before they merge.
* Done = acceptance met.
* One shared GitHub and Jira account: sign everything, and never write @agent.

## After Friday

* TC2 primary-path decision (ANVIL-21).
* TC3 re-run (ANVIL-50).
* Traces (ANVIL-85).
* UI redesign (ANVIL-53).
* Owner items (ANVIL-48).
