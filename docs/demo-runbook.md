# Anvil Demonstration Runbook: Offline Team Laptop Path

> **Offline Target:** Disconnected team laptops (Friday 11-06 demo).
> - **What is offline:** GitHub (`github.com`) and Atlassian Cloud (`*.atlassian.net`) are unreachable. Standalone local stack + local file ticketing + pre-embedded KB + Docker compilation sandbox.
> - **What is online:** LLM provider access through LiteLLM gateway (`127.0.0.1:4000`). The model requires internet egress to reach upstream APIs; credentials live in Windows Credential Manager target `forge`.  
> **Format:** Checklist-driven runbook for presenters before and during the demonstration.

---

## 1. Before Entering the Room (T-15m Setup Checklist)

Complete these steps on the laptop while setting up the presenter display:

### 1.1 Stack & Containers
- [ ] **Launch Local Stack:**
  ```powershell
  .\scripts\up.ps1
  ```
  *Check:* `anvil-postgres` healthy on port `5433` and `anvil-gateway` healthy on port `4000`.
- [ ] **Verify / Import Docker Sandbox Image:**
  ```powershell
  docker image inspect anvil-sandbox-rtems:7.1 > $null
  if ($LASTEXITCODE -ne 0) {
      .\scripts\import-sandbox-image.ps1
  }
  ```
  *Check:* Image `anvil-sandbox-rtems:7.1` present locally. Omitting `-ImagePath` automatically locates `anvil-sandbox-rtems-7.1.tar` under `data\sandbox\` or `private\handover\`.
- [ ] **Import Pre-Embedded Knowledge Base Snapshot:**
  ```powershell
  uv run --directory backend python -m anvil.kb import-snapshot --from ..\data\kb
  ```
  *Check:* Sidecar vectors verified and imported into `kb_chunks` table without requiring live gateway embeddings (document snapshot hash matches sidecar `snapshot_sha256`).

### 1.2 API & UI Daemons
- [ ] **Start Control API Daemon (Terminal 1):**
  ```powershell
  .\scripts\start-api.ps1 -Force
  ```
  *Check:* API listening on `http://127.0.0.1:8000`. Fresh process spawned from latest `main`. Automatically runs sandbox reaper on start. Log active at `api-8000.log`.
- [ ] **Start Frontend Web UI (Terminal 2):**
  ```powershell
  cd frontend
  npm run dev
  ```
  *Check:* Vite dev server listening at `http://localhost:5173`. Open in presenter browser.

### 1.3 Preflight & Local Ticket Staging
- [ ] **Run Preflight Verification:**
  ```powershell
  .\scripts\preflight.ps1 -Presenter offline
  ```
  *Criteria:* Reports 'PREFLIGHT RESULT: ALL CHECKS PASSED (6 passed, 2 skipped (offline))' (sandbox image, signed Python runtime, API port 8000, local stack health, and container hygiene; Jira baseline and submodule checks skip cleanly in offline mode).
- [ ] **Stage the Target Ticket in `todo/`:**
  Copy the verified ticket from handover or cases template to `ANVIL_TICKETS_DIR\todo\RTEMS-1.json`:
  ```powershell
  Copy-Item "private\handover\tickets\todo\RTEMS-1.json" "C:\work\tickets\todo\RTEMS-1.json" -Force
  ```
  *(If `private\handover` is not yet populated on the laptop, copy from `cases/rtems-5776.ticket.md`)*
  - **Autonomous vs. Gated Mode:**
    - Standard autonomous run: `"labels": ["anvil", "posix", "tc1"]`
    - Interactive human-gated run: `"labels": ["anvil", "posix", "tc1", "anvil-interactive"]` (triggers outward tool pauses).
  - **Codebase Path Rule:**
    Ensure ticket description specifies: `Code base: branch tc1/5776-base of C:\work\rtems`. (Enclose paths containing spaces in double quotes: e.g. `Code base: "C:\work\My Projects\rtems"`).
  > [!NOTE]
  > If pointing to a plain unversioned folder (no `.git`), Anvil generates an ephemeral single-commit snapshot on the fly. For large codebases like RTEMS (~10k+ files), this initial stat snapshot takes ~111 seconds (~1.8m) on Windows NTFS. From a Git checkout, intake starts in 1–2 seconds.

---

## 2. The Run (Talk Tracks & Screen Tour)

- **Target Ticket:** `RTEMS-1` (TC1: Mutex priority ceiling defect).
- **Intake Trigger:** The local polling intake detects `RTEMS-1.json` in `todo/`, moves it to `in-progress/RTEMS-1.json`, and starts session `local:RTEMS-1`.
- **Timing Reality & Presenter Strategy:**
  > [!IMPORTANT]
  > **Real TC1 runs take 20 to 45 minutes** (cross-compilation, Waf configuration, reproduction, and SIS simulation inside Docker). Do NOT attempt to wait silently on stage for an end-to-end run to finish live.
  > - **Primary Strategy:** Launch the live run to demonstrate ticket intake and the first ~2 minutes of autonomous code exploration. Then, switch to a browser tab showing a **pre-staged finished run** (session replay) to tour the plan, diff, and verification outputs without stalling the room.
  > - **Alternative Strategy:** Pre-run TC1 immediately before the session; walk through the live timeline stream and jump straight to the approval gate and analysis.

### 2.1 What Each Screen / Panel Shows
| UI Panel | What Evaluators See | Presenter Notes |
|---|---|---|
| **Chat / Run Stream (Center)** | Live tool calls (`kb_search`, `bash`, `waf`), thoughts, and simulation runs (`sis`). | Streams SSE events directly from backend without page refresh. |
| **Plan Panel (Right)** | Dynamic checklist generated and updated by the agent. | Shows status of reproduction, source editing, Waf compilation, and test execution. |
| **Diff Tab (Top Right)** | Live unified diff of modified code (`cpukit/posix/src/mutexinit.c`). | Shows exact syntax edits applied inside the container sandbox. |
| **Cost Meter (Top Right)** | Displays **"pending"** while spend logs lag. | Explain: LiteLLM spend rows flush asynchronously to Postgres; meter displays "pending" rather than synthetic estimates or 503 errors. Once flushed, displays actual USD spend. |

### 2.2 Talk Tracks
- **Initial Code Intake & KB Search (First ~2 Minutes):**
  - *What's happening:* The agent inspects `tc1/5776-base`, reviews the defect description, locates mutex initialization code, and queries the offline Knowledge Base.
  - *Talk track:*
    > *"Notice that during the first two minutes, the model is reading the codebase and searching our offline engineering knowledge base using dense vector search. The upstream fix commits from late 2026 are strictly withheld by our historical time guard. The agent must deduce the defect independently."*
- **Reproduction & SIS Simulation:**
  - *What's happening:* The agent reproduces the defect on the SPARC/ERC32 simulator (`sis`), observing that `pthread_mutex_trylock()` returns `EBUSY` instead of `EINVAL`.
  - *Talk track:*
    > *"Notice the plan panel on the right. Anvil formulates a verification plan, cross-compiles for SPARC using RTEMS Waf inside the Docker sandbox, and reproduces the defect under the SIS architectural simulator without requiring physical hardware."*
- **Patch Application & Regression Verification:**
  - *What's happening:* The agent modifies `cpukit/posix/src/mutexinit.c`, rebuilds `sparc/erc32`, and executes regression tests on the simulator.
  - *Talk track:*
    > *"Expect the agent to determine that `pthread_mutex_init` does not validate priority ceiling attributes before writing object state. Once modified, it re-compiles and re-runs the test suite on SIS: verification tests pass cleanly."*

---

## 3. Human Governance & The Approval Moment

> [!NOTE]
> **Observed live in local verification (ANVIL-24 / ANVIL-32).**
> Re-confirm exact timing during the 15:00 clean-laptop dry run rehearsal.

### 3.1 How Gating Operates in Code
- **Activation:** A run is only gated if the ticket includes the label `"anvil-interactive"`. Without it, `approval_policy("autonomous", ...)` gates nothing and execution proceeds autonomously.
- **Offline Gated Tools:** In offline runs, remote GitHub tools (`git_push`, `github_open_pr`, `github_merge`) are withheld. The gated tools are the local `OUTWARD_TOOLS`:
  - `jira_comment` (posting findings/status to the ticket)
  - `jira_set_labels` (updating ticket tags)
  - `jira_transition` (moving ticket to In Progress or Done)
  - `publish_evidence` (writing local knowledge pages)
- **Approval Card UI:** When the agent invokes an outward tool, execution pauses in state `waiting_approval`.
  - In the right-hand Approvals panel and global "Approvals inbox" page (badge count on nav button), the card displays:
    - Approval ID and tag (`agent`)
    - Tool name and arguments: `Tool execution requires approval / Tool: jira_comment / Args: {'markdown': '...'}`
    - Session line: `RTEMS-1: ... · C:\work\rtems · waiting_approval`
    - "Comment (optional)" input box
    - **Approve** and **Reject** buttons.
- **Deliverables Generation:** Deliverables (`.patch` and `.bundle`) are exported automatically via `export_local` upon successful verification, *not* as a side-effect of clicking approve.

### 3.2 Decision Paths at the Gate
* **Path A: Approve (Nominal Flow)**
  1. Review the proposed tool arguments (e.g. comment body or labels) in the UI card.
  2. Click **Approve**.
  3. Execution resumes immediately: the outward tool executes against the local ticket store (the `jira_comment` appends to `comments[]` in `in-progress/RTEMS-1.json`), run status transitions `waiting_approval -> running -> succeeded`, and deliverables (`.patch`, `.bundle`, command logs) persist under `private/runs/<id>/`.
  4. *Talk track:* *"Human governance is built into the workflow. In interactive mode, outward operations pause for engineering confirmation before mutating state."*
* **Path B: Reject with Feedback (Critique Loop)**
  1. Enter feedback in the comment box (e.g., `"not yet — verify priority ceiling bounds before documenting"`).
  2. Click **Reject**.
  3. The agent receives tool rejection output: `{ok: False, output: "User rejected the tool call for `jira_comment` with reason: <feedback>"}`.
  4. The agent reads the reason, reflects in its planning loop, re-inspects code or diffs, adjusts its actions, and re-invokes the outward tool.
  5. A second approval card appears showing the updated arguments (e.g. reflecting the adjusted fix commit).
  > [!TIP]
  > Prior to PR #108 (ANVIL-96), the Approvals panel only fetched on mount/session change (requiring a page reload to see the second card after a reject). With ANVIL-96's background polling active, the retry card updates automatically within 3 seconds.

---

## 4. After the Run (Host Deliverables & Analysis)

### 4.1 Host Deliverables
Verify generated patch and bundle in the runs directory:
```powershell
Get-ChildItem -Path "private\runs" | Select-Object -Last 1
```
* **Git Patch:** `private\runs\<run_id>\<run_id>.patch`
  - In a git working copy: `git am <patch>`
  - In a plain unversioned folder: `git apply <patch>`
* **Git Bundle:** `private\runs\<run_id>\<run_id>.bundle`
  - In git: `git fetch <bundle> <branch>`
* **Ticket Status:** Verify ticket moved to `C:\work\tickets\done\RTEMS-1.json` with execution comments and duration metrics.

### 4.2 Post-Run Analysis CLI
Run the post-run diagnostic CLI:
```powershell
uv run --directory backend python -m anvil.analyze <run_id>
```
*Outputs:*
- Wall-clock step timing and tool execution durations.
- Token consumption per model alias (`ANVIL_MAIN_MODEL`, `ANVIL_FAST_MODEL`).
- Compaction events (verifies if `context.compacted` fired).
- Actual USD spend recorded from LiteLLM PostgreSQL spend logs.

---

## 5. "If X Goes Wrong" (Troubleshooting & Emergency Fallbacks)

Quick triage checklist for live evaluation glitches:

| Problem | Symptoms | Fast Fix (Under 30 Seconds) |
|---|---|---|
| **Gateway Down** | API reports connection refused to `127.0.0.1:4000` | Run `docker restart anvil-gateway`. Check health at `http://127.0.0.1:4000/health/liveliness`. Verify provider API key in Credential Manager target `forge`. |
| **No Embeddings / KB Fails** | `kb_search` returns empty or errors | Check vectors sidecar hash: `(Get-FileHash data\kb\kb-snapshot.jsonl.gz).Hash.ToLower()`. Re-run: `uv run --directory backend python -m anvil.kb import-snapshot --from ..\data\kb`. |
| **Sandbox Won't Start** | Container startup timeout; `anvil-sbx-*` fails to boot | Prune stale containers: `.\scripts\reset-demo.ps1 -Containers`. Check Docker Desktop resource limits (min 7 GB RAM allocated). |
| **Run Stuck > 5 Minutes** | Agent repeats loop or hangs without emitting events | **Do not force-kill.** Anvil's stuck-detector automatically triggers graceful handoff: persists WIP state, tags ticket with `anvil-needs-human`, and writes diagnostic notes. |
| **Catastrophic Host Failure** | Laptop OS crash or critical toolchain failure | **Emergency Fallback:** Screen-record the 15:00 dry run and keep the MP4 on the laptop outside git. Until recorded, fallback is opening the UI to a completed run replay, inspecting `.patch`, and running `python -m anvil.analyze <run_id>`. |

---

## 6. Quick Reset for Next Run

To reset the offline environment for a consecutive demonstration:
```powershell
# 1. Prune ephemeral containers
.\scripts\reset-demo.ps1 -Containers

# 2. Reset ticket back to todo/ (use clean baseline, not modified file)
Copy-Item "private\handover\tickets\todo\RTEMS-1.json" "C:\work\tickets\todo\RTEMS-1.json" -Force
Remove-Item "C:\work\tickets\done\RTEMS-1.json" -ErrorAction SilentlyContinue
Remove-Item "C:\work\tickets\in-progress\RTEMS-1.json" -ErrorAction SilentlyContinue

# 3. Clean working copy branch in local RTEMS
git -C C:\work\rtems checkout -B tc1/5776-base origin/tc1/5776-base
git -C C:\work\rtems clean -fd
```
