# Clean-Laptop Dry Run Evidence Checklist (15:00 Rehearsal)

> **Purpose:** Internal operational scorecard for the 15:00–18:00 dry run rehearsal.  
> Ensures every ticket pending live acceptance evidence (ANVIL-63, ANVIL-88, ANVIL-89, ANVIL-73, ANVIL-24, ANVIL-32) captures its required proof in a single unified run on a clean offline laptop.  
> **Rule:** Record observed values live into the table blanks. Do not commit dry run test artifacts, screen recordings, or unversioned caches into git.

---

## 1. Dry Run Evidence Matrix

| # | Ticket | Step / Stage | Exact Command | What to Capture (Fill In Live) | Pass Condition |
|---|---|---|---|---|---|
| **1** | **ANVIL-63** | Unzip & Host Setup | Follow `docs/SETUP.md` step-by-step from clean zip | - Unzip timestamp: `____________`<br>- First run start: `____________`<br>- Wall time elapsed: `______ min`<br>- Doc discrepancies/omissions: `____________________` | Clean setup with no external internet calls to GitHub or Atlassian; all documented steps work without missing prereqs. |
| **2** | **ANVIL-88** | Handover Artifact Integrity | Check archive SHA256 hashes against manifests | - Zip SHA256: `____________________`<br>- Sandbox TAR SHA256: `____________________`<br>- Bundle SHA256: `____________________`<br>- Sidecar Vectors SHA256: `____________________` | All SHA256 hashes match manifests exactly; zero checksum mismatches. |
| **3** | **ANVIL-88** | Sandbox Image Load | `.\scripts\import-sandbox-image.ps1` | - Image inspect ID: `____________________`<br>- Tag: `anvil-sandbox-rtems:7.1` | Exit code 0; image `anvil-sandbox-rtems:7.1` registered locally in Docker. |
| **4** | **ANVIL-88** | KB Snapshot Import (Zero Gateway Calls) | Stop gateway to prove offline sidecar import:<br>`docker stop anvil-gateway`<br>`uv run --directory backend python -m anvil.kb import-snapshot --from ..\data\kb`<br>`docker start anvil-gateway`<br>*(Alternative: verify `LiteLLM_SpendLogs` count for `anvil-embed` delta is 0)* | - Imported chunks count: `______`<br>- Gateway stopped during import: `[ ] YES`<br>- Gateway embedding calls: `______` *(Must be 0)*<br>- Snapshot SHA256 match: `[ ] YES` | Exit code 0; vectors imported from sidecar with gateway stopped; zero embedding calls made to LiteLLM. |
| **5** | **ANVIL-63** | Preflight Verification | `.\scripts\preflight.ps1 -Presenter offline` | - Overall result: `[ ] PASS (6 passed, 2 skipped (offline)) / [ ] FAIL`<br>- Failing check (if any): `____________________` | All offline checks pass cleanly (sandbox image, signed Python, ports 8000/5433/4000, container hygiene; Jira baseline and submodules skipped). |
| **6** | **ANVIL-89** | Plain Folder Creation & Intake | Create plain folder from handover bundle:<br>`git clone private\handover\rtems-tc1-5776-base.bundle "C:\work\Test Space\rtems plain"`<br>`git -C "C:\work\Test Space\rtems plain" checkout tc1/5776-base`<br>`Remove-Item -Recurse -Force "C:\work\Test Space\rtems plain\.git"`<br>*(Note: `git clone` prints `warning: remote HEAD refers to nonexistent ref` as expected for a single-branch bundle; checkout then succeeds)* | - Initial stat snapshot time: `______ s` *(Baseline: ~111 s on NTFS)*<br>- Ephemeral snapshot commit: `____________________` | Ephemeral git commit created without errors; path containing spaces handled correctly. |
| **7** | **ANVIL-73** | Presenter Live Intake & Reopen Flow | Stage `RTEMS-1.json` with label `anvil-interactive` in `todo/`. Once intake moves ticket to `in-progress/`, open/refresh UI at `http://localhost:5173` and select the session in SessionList. | - Timeline replayed with no duplicate events: `[ ] PASS`<br>- Live SSE stream attached and continues: `[ ] PASS`<br>- Approval card appears within ~3 s without reload: `[ ] PASS` | All 3 pass cleanly: timeline rendered from start without duplicate `seq`s, live SSE stream attaches seamlessly, and approval card renders via polling within ~3 s. |
| **8** | **ANVIL-24**<br>**ANVIL-32** | Interactive Gate: Rejection & Critique | When first outward tool pauses (`waiting_approval`), enter critique in comment box and click **Reject** | - Tool paused: `____________________`<br>- Card displayed args: `____________________`<br>- Rejection critique entered: `____________________`<br>- Agent response to rejection: `____________________`<br>- Second approval card appeared: `[ ] YES` *(Auto-refreshed via ANVIL-96 polling)* | Execution halts at gate; reject passes feedback back to agent; agent reflects, modifies fix, and re-submits with a second approval card within ~3 s. |
| **9** | **ANVIL-24**<br>**ANVIL-32** | Interactive Gate: Nominal Approval | Review second approval card and click **Approve** | - Approval timestamp: `____________`<br>- Ticket `comments[]` appended: `[ ] YES`<br>- Final run status: `[ ] succeeded` | Run transitions `waiting_approval -> running -> succeeded`; comment persists to ticket JSON; patch and bundle exported. |
| **10** | **ANVIL-89** | Token Consumption & Bounded Output | `uv run --directory backend python -m anvil.analyze <run_id>` | - `ANVIL_MAIN_MODEL` input tokens: `____________`<br>- `ANVIL_FAST_MODEL` input tokens: `____________`<br>- Total input tokens: `____________`<br>- Total output tokens: `____________`<br>- Total run wall time: `______ min`<br>- Truncation loops observed: `[ ] NONE` | Total input tokens **< 5.15M** (rehearsal 2 baseline); no `[output truncated]` infinite looping; run completes end-to-end. |
| **11** | **Demo Prep** | Screen Recording & Deliverables | Inspect `private/runs/<run_id>/` and laptop recording | - `.patch` generated: `[ ] YES`<br>- `.bundle` generated: `[ ] YES`<br>- Screen recording captured: `[ ] YES` *(Path on host: `____________________`, outside git)* | Valid git `.patch` and `.bundle` generated; full high-res video saved locally for emergency fallback. |
| **12** | **Hygiene** | Ephemeral Container Reaper | Run container reset and check active Docker containers | - Containers before run: `______`<br>- Containers after reset: `______`<br>- `.\scripts\reset-demo.ps1 -Containers` status: `[ ] PASS` | Zero orphaned `anvil-sbx-*` ephemeral containers remaining on host. |

---

## 2. Evidence Sign-Off Log

When the dry run completes, copy this summary block into the Jira acceptance comments for **ANVIL-63**, **ANVIL-88**, **ANVIL-89**, and **ANVIL-73**:

```text
[antigravity] Dry Run Acceptance Evidence (2026-11-05 Rehearsal):
- Setup wall time (unzip to first run start): ___ minutes (ANVIL-63)
- Handover package integrity: All 4 archive SHA256 hashes verified (ANVIL-88)
- KB Snapshot Import: Imported with 0 gateway embedding calls (gateway stopped during import) (ANVIL-88)
- Intake & Session Reopen: Timeline replayed without duplicates, live stream attached, approval card polled (ANVIL-73)
- Token consumption: ___ input tokens (ANVIL-89; target < 5.15M baseline)
- Run wall time: ___ minutes
- Interactive gate: Tested 1 Reject (critique & retry) + 1 Approve (succeeded) (ANVIL-24, ANVIL-32)
- Container hygiene: Stale containers pruned cleanly (0 leaks)
- Fallback video: Recorded to local host path (outside git)
```
