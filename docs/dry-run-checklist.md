# Clean-Laptop Dry Run Evidence Checklist (15:00 Rehearsal)

> **Purpose:** Internal operational scorecard for the 15:00–18:00 dry run rehearsal.  
> Ensures every ticket pending live acceptance evidence (ANVIL-63, ANVIL-88, ANVIL-89, ANVIL-24, ANVIL-32) captures its required proof in a single unified run on a clean offline laptop.  
> **Rule:** Record observed values live into the table blanks. Do not commit dry run test artifacts, screen recordings, or unversioned caches into git.

---

## 1. Dry Run Evidence Matrix

| # | Ticket | Step / Stage | Exact Command | What to Capture (Fill In Live) | Pass Condition |
|---|---|---|---|---|---|
| **1** | **ANVIL-63** | Unzip & Host Setup | Follow `docs/SETUP.md` step-by-step from clean zip | - Unzip timestamp: `____________`<br>- First run start: `____________`<br>- Wall time elapsed: `______ min`<br>- Doc discrepancies/omissions: `____________________` | Clean setup with no external internet calls to GitHub or Atlassian; all documented steps work without missing prereqs. |
| **2** | **ANVIL-88** | Handover Artifact Integrity | Check archive SHA256 hashes against manifests | - Zip SHA256: `____________________`<br>- Sandbox TAR SHA256: `____________________`<br>- Bundle SHA256: `____________________`<br>- Sidecar Vectors SHA256: `____________________` | All SHA256 hashes match manifests exactly; zero checksum mismatches. |
| **3** | **ANVIL-88** | Sandbox Image Load | `.\scripts\import-sandbox-image.ps1` | - Image inspect ID: `____________________`<br>- Tag: `anvil-sandbox-rtems:7.1` | Exit code 0; image `anvil-sandbox-rtems:7.1` registered locally in Docker. |
| **4** | **ANVIL-88** | KB Snapshot Import (Zero Gateway Calls) | `uv run --directory backend python -m anvil.kb import-snapshot --from ..\data\kb` | - Imported chunks count: `______`<br>- Gateway embedding calls: `______` *(Must be 0)*<br>- Snapshot SHA256 match: `[ ] YES` | Exit code 0; vectors imported from sidecar; zero embedding calls made to LiteLLM gateway. |
| **5** | **ANVIL-63** | Preflight Verification | `.\scripts\preflight.ps1 -Presenter offline` | - Overall result: `[ ] PASS / [ ] FAIL`<br>- Failing check (if any): `____________________` | All offline checks pass cleanly (sandbox image, signed Python, ports 8000/5433/4000, container hygiene). |
| **6** | **ANVIL-89** | Plain Folder Intake with Space | Stage unversioned RTEMS folder at `C:\work\Test Space\rtems plain` | - Initial stat snapshot time: `______ s` *(Baseline: ~111 s on NTFS)*<br>- Ephemeral snapshot commit: `____________________` | Ephemeral git commit created without errors; path containing spaces handled correctly. |
| **7** | **ANVIL-24**<br>**ANVIL-32** | Interactive Gate: Rejection & Critique | Run ticket with label `anvil-interactive`. Enter critique comment and click **Reject** on first outward tool call | - Tool paused: `____________________`<br>- Card displayed args: `____________________`<br>- Rejection critique entered: `____________________`<br>- Agent response to rejection: `____________________`<br>- Second approval card appeared: `[ ] YES` *(Auto-refreshed via ANVIL-96 polling)* | Execution halts at gate; reject passes feedback back to agent; agent reflects, modifies fix, and re-submits with a second approval card. |
| **8** | **ANVIL-24**<br>**ANVIL-32** | Interactive Gate: Nominal Approval | Review second approval card and click **Approve** | - Approval timestamp: `____________`<br>- Ticket `comments[]` appended: `[ ] YES`<br>- Final run status: `[ ] succeeded` | Run transitions `waiting_approval -> running -> succeeded`; comment persists to ticket JSON; patch and bundle exported. |
| **9** | **ANVIL-89** | Token Consumption & Bounded Output | `uv run --directory backend python -m anvil.analyze <run_id>` | - `ANVIL_MAIN_MODEL` input tokens: `____________`<br>- `ANVIL_FAST_MODEL` input tokens: `____________`<br>- Total input tokens: `____________`<br>- Total output tokens: `____________`<br>- Total run wall time: `______ min`<br>- Truncation loops observed: `[ ] NONE` | Total input tokens **< 5.15M** (rehearsal 2 baseline); no `[output truncated]` infinite looping; run completes end-to-end. |
| **10** | **Demo Prep** | Screen Recording & Deliverables | Inspect `private/runs/<run_id>/` and laptop recording | - `.patch` generated: `[ ] YES`<br>- `.bundle` generated: `[ ] YES`<br>- Screen recording captured: `[ ] YES` *(Path on host: `____________________`, outside git)* | Valid git `.patch` and `.bundle` generated; full high-res video saved locally for emergency fallback. |
| **11** | **Hygiene** | Ephemeral Container Reaper | Run container reset and check active Docker containers | - Containers before run: `______`<br>- Containers after reset: `______`<br>- `.\scripts\reset-demo.ps1 -Containers` status: `[ ] PASS` | Zero orphaned `anvil-sbx-*` ephemeral containers remaining on host. |

---

## 2. Evidence Sign-Off Log

When the dry run completes, copy this summary block into the Jira acceptance comments for **ANVIL-63**, **ANVIL-88**, and **ANVIL-89**:

```text
[antigravity] Dry Run Acceptance Evidence (2026-11-05 Rehearsal):
- Setup wall time (unzip to first run start): ___ minutes (ANVIL-63)
- Handover package integrity: All 4 archive SHA256 hashes verified (ANVIL-88)
- KB Snapshot Import: Imported with 0 gateway embedding calls (ANVIL-88)
- Token consumption: ___ input tokens (ANVIL-89; target < 5.15M baseline)
- Run wall time: ___ minutes
- Interactive gate: Tested 1 Reject (critique & retry) + 1 Approve (succeeded) (ANVIL-24, ANVIL-32)
- Container hygiene: Stale containers pruned cleanly (0 leaks)
- Fallback video: Recorded to local host path (outside git)
```
