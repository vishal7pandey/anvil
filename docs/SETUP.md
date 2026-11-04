# Anvil Setup Guide: Disconnected Laptop Bootstrap ("Zip to First Run")

> **Target Audience:** Team members running Anvil locally on isolated enterprise laptops.  
> **Connectivity:** Disconnected from enterprise SaaS. No GitHub (`github.com`), no Jira/Confluence Cloud (`*.atlassian.net`). Outbound access to the model provider (e.g. OpenAI) routes through the local LiteLLM gateway proxy (`http://127.0.0.1:4000`), which requires provider API credentials configured in `.env` (or via Windows Credential Manager target `forge`).

---

## 1. Overview & Architecture

For offline demonstrations and disconnected environments, Anvil operates completely within local boundaries:

* **Codebase Source:** Points directly to an absolute local git checkout (`C:\work\rtems`) rather than a remote GitHub mirror.
* **Ticket Store:** Evaluates file-based tickets in JSON format (`todo/`, `in-progress/`, `done/`) via the local tracker (`ANVIL_TICKETS_DIR`) rather than Cloud Jira.
* **Knowledge Base:** Ingests pre-embedded vector snapshots locally without remote documentation calls.
* **Artifact Delivery:** Produces standard git patch files (`.patch`) and git bundles (`.bundle`) in `private/runs/<run_id>/` on the host filesystem.

---

## 2. Prerequisites

Ensure your laptop meets the following baseline requirements before unzipping:

| Component | Requirement | Verification Command |
|---|---|---|
| **OS** | Windows 11 / WSL2, macOS, or Ubuntu 22.04+ | `cmd.exe /c ver` or `uname -a` |
| **Docker** | Docker Desktop or Docker Engine (Min 7 GB RAM allocated) | `docker info` |
| **Python** | Python 3.12 or 3.13 (CPython signed on Windows) | `uv run python --version` |
| **Node.js** | Node.js 20+ and `npm` | `npm --version` |
| **Package Manager**| `uv` package and environment manager | `uv --version` |

> [!NOTE]
> On Windows machines with Microsoft Smart App Control, standalone binary wrappers like `pytest.exe` may be blocked. Always run Python utilities via `uv run python -m <tool>`.

---

## 3. Package Structure & Distribution Assets

The distribution bundle consists of git-tracked code alongside separate out-of-band data files:

### Repository Files (The Distribution Zip)
```text
anvil/
├── backend/                  # FastAPI control backend & Deep Agents runtime
├── frontend/                 # Vite/React enterprise web interface
├── cases/                    # Evaluation cases (cases/rtems-5776.toml for TC1)
├── docs/                     # Architecture, specifications, and runbooks
├── scripts/                  # Automation scripts (preflight, up, import, reset)
├── data/kb/                  # Committed Knowledge Base snapshot text & manifest
│   ├── kb-snapshot.jsonl.gz
│   └── manifest.json
└── .env.example              # Baseline configuration template
```

### Out-of-Band Assets (Placed into `data/`)
Due to file size and offline requirements, these assets are distributed separately and placed into `data/`:
1. **Docker Sandbox Image Archive (`data/sandbox/`)**:
   - `anvil-sandbox-rtems-7.1.tar`
   - `anvil-sandbox-rtems-7.1.tar.sha256`
   - `anvil-sandbox-rtems-7.1.tar.manifest.json`
2. **Knowledge Base Vectors Sidecar (`data/kb/`)**:
   - `kb-snapshot-vectors.jsonl.gz`
   - `kb-snapshot-vectors.manifest.json`
3. **Local Target Source Code**:
   - A local clone or archive of `vishal7pandey/rtems` placed at e.g. `C:\work\rtems` with branch `tc1/5776-base`.

---

## 4. Step-by-Step Installation

### Step 1: Configure Environment Variables

Copy `.env.example` to `.env` in the repository root:

```powershell
Copy-Item .env.example .env
```

Configure `.env` with the following variables:

```ini
# Models and Local LiteLLM Gateway
ANVIL_MAIN_MODEL=openai/gpt-5
ANVIL_FAST_MODEL=openai/gpt-5-mini
LITELLM_MASTER_KEY=sk-anvil-local
POSTGRES_PASSWORD=anvil

# Local Ticket Tracker (Offline Mode - ANVIL-56)
ANVIL_TICKETS_DIR=C:\work\tickets

# Optional Demo Auth Token (leave empty for unauthenticated local demo)
ANVIL_DEMO_TOKEN=

# Host Database & Gateway defaults
ANVIL_DATABASE_URL=postgresql+psycopg://anvil:anvil@localhost:5433/anvil
ANVIL_KB_DATABASE_URL=postgresql+psycopg://anvil:anvil@localhost:5433/anvil
ANVIL_GATEWAY_URL=http://localhost:4000

# Outbound LLM Provider Key (for LiteLLM proxy; omit if stored in Credential Manager)
OPENAI_API_KEY=
```

> [!IMPORTANT]
> Do **NOT** set `ATLASSIAN_*` or `GITHUB_*` credentials in `.env` for offline execution. When `ATLASSIAN_*` is unset, the API runs exclusively in offline local ticketing mode.

---

### Step 2: Import the Docker Sandbox Image

Import the pre-built RTEMS compilation sandbox image (`anvil-sandbox-rtems:7.1`) into your Docker daemon using the integrity verification script:

```powershell
.\scripts\import-sandbox-image.ps1 -ImagePath data\sandbox\anvil-sandbox-rtems-7.1.tar
```

**Verification Details:**
* The script hashes the tarball and checks it against `anvil-sandbox-rtems-7.1.tar.sha256` or the `.manifest.json` sidecar (fails closed on mismatch or missing hash unless `-SkipHashVerify` is passed).
* `docker load` imports the image layers.
* The script verifies that the loaded image ID matches the manifest's `image_id` exactly and confirms the tag `anvil-sandbox-rtems:7.1`.

---

### Step 3: Launch Supporting Stack Containers

Start PostgreSQL 16 (with `pgvector`) and the LiteLLM Gateway proxy:

```powershell
.\scripts\up.ps1
```

> [!IMPORTANT]
> The Anvil Control API enforces fail-fast database startup checks (PR #70). If Postgres is not accepting connections on port `5433` at boot time, the API terminates immediately.

---

### Step 4: Import Pre-Embedded Knowledge Base Snapshot

Load the offline knowledge base snapshot:

```powershell
uv run --directory backend python -m anvil.kb import-snapshot --from ..\data\kb
```

* When `kb-snapshot-vectors.manifest.json` and `kb-snapshot-vectors.jsonl.gz` are present in `data\kb\`, `import-snapshot` loads the precomputed vectors directly, skipping re-embedding.
* If the vectors sidecar is omitted, `import-snapshot` calls the embedder via the LiteLLM gateway (`http://127.0.0.1:4000`) to compute embeddings.
* At runtime, `kb_search` queries are embedded via the gateway while respecting the leak guard.

---

### Step 5: Run Preflight Check

Verify that local subsystems, ports, dependencies, and images are properly initialized:

```powershell
.\scripts\preflight.ps1
```

**Offline Preflight Expectations:**
* **Must Pass [PASS]:**
  - Check 1: Docker Sandbox Image (`anvil-sandbox-rtems:7.1` present and valid ID).
  - Check 2: Python Runtime (CPython signed, unicodedata/requests importable).
  - Check 3: Control API (once running on port :8000).
  - Check 4: Local Stack (containers `anvil-gateway` and `anvil-postgres` healthy).
  - Check 5: Build Lock (free or held by presenter).
  - Check 8: Ephemeral Containers (no leftover `anvil-sbx-*` containers).
* **Ignored for Offline Demos [FAIL / WARN]:**
  - Check 6: Jira Baseline (fails when running without Atlassian Cloud credentials).
  - Check 7: Submodule Reachability (checks external GitLab/GitHub networks; ignored for local sources).

---

## 5. Running the Offline Demo (TC1 Walkthrough)

### 1. Prepare the Target Codebase Branch

In your local checkout of RTEMS (`C:\work\rtems`), ensure the base branch `tc1/5776-base` is created:

```powershell
git -C C:\work\rtems branch tc1/5776-base origin/tc1/5776-base
```

---

### 2. Stage the Target Ticket

Create directory structure for the local tracker:

```powershell
New-Item -ItemType Directory -Force -Path "C:\work\tickets\todo", "C:\work\tickets\in-progress", "C:\work\tickets\done"
```

Create the TC1 ticket file at `C:\work\tickets\todo\RTEMS-1.json`:

```json
{
  "key": "RTEMS-1",
  "type": "Bug",
  "status": "To Do",
  "labels": [
    "anvil",
    "posix",
    "tc1"
  ],
  "summary": "Mutex reports EBUSY / crashes after a failed pthread_mutex_init with an invalid priority ceiling",
  "description": "## Observed\n\nAfter `pthread_mutex_init()` fails with `EINVAL` for a priority-ceiling mutex whose ceiling is out of range, the same mutex object is still accepted by later calls: `pthread_mutex_trylock()` and `pthread_mutex_destroy()` return `EBUSY` instead of `EINVAL`, and `pthread_mutex_lock()` ends in a fatal data abort inside the thread queue code. With a zero-filled object the lock instead succeeds and the caller ends up at priority 0.\n\n## Steps to reproduce\n\n```c\npthread_mutexattr_t attr;\npthread_mutex_t     mtx;\n\nmemset( &mtx, 0xa5, sizeof( mtx ) );\npthread_mutexattr_init( &attr );\npthread_mutexattr_setprotocol( &attr, PTHREAD_PRIO_PROTECT );\npthread_mutexattr_setprioceiling( &attr, sched_get_priority_max( SCHED_FIFO ) + 1 );\n\npthread_mutex_init( &mtx, &attr ); /* EINVAL, as expected */\npthread_mutex_trylock( &mtx );     /* expected EINVAL, got EBUSY */\npthread_mutex_destroy( &mtx );     /* expected EINVAL, got EBUSY */\n```\n\n## Expected\n\nA failed `pthread_mutex_init()` leaves `*mutex` unmodified, and later calls reject the object with `EINVAL`.\n\n## Environment\n\nUniprocessor, RTEMS_DEBUG off. Reproduce on `sparc/erc32` (SIS). Code base: branch tc1/5776-base of C:\\work\\rtems\n\n## Done when\n\nRoot cause identified, fix on a branch, a regression test in the POSIX test suite passes on the simulator, PR open with CI green."
}
```

> [!IMPORTANT]
> 1. **Case Selection:** The ticket `key` must be `"RTEMS-1"`. The intake loop uses this key to enforce the KB historical time guard (blocking answer leaks) and to bind automated acceptance verification.
> 2. **Local Codebase Path:** The description must contain `Code base: branch <base> of <absolute_path>` with **no spaces** in the path.

---

### 3. Launch the Control API and Frontend

In Terminal 1 (API Daemon):
```powershell
.\scripts\start-api.ps1 -Force
```

In Terminal 2 (Frontend UI):
```powershell
cd frontend
npm ci
npm run dev
```

Open your browser to `http://localhost:5173`.

---

### 4. Execution & Verification Flow

1. **Intake Ingestion:** The background intake loop scans `C:\work\tickets\todo\`, detects `RTEMS-1.json`, moves it to `C:\work\tickets\in-progress\RTEMS-1.json`, and schedules the run with trigger `local:RTEMS-1`.
2. **Session Stream:** In the UI, the session run stream begins displaying real-time events, tool executions, and step timing.
3. **Investigation & Repair:**
   * The container clones locally from `C:\work\rtems` on branch `tc1/5776-base`.
   * The agent searches the KB via `kb_search` (with the time guard blocking future fix commits).
   * It reproduces the defect on the SPARC/ERC32 simulator (`sis`).
   * It applies code modifications, recompiles via `./waf`, and executes regression tests.
4. **Outcome & Deliverables:**
   * Acceptance verification checks that the test suite passes on the simulator.
   * Host-side deliverables are saved to `private/runs/<run_id>/<run_id>.patch` and `<run_id>.bundle`.
   * Comments and execution logs are appended to `C:\work\tickets\in-progress\RTEMS-1.json` (the ticket moves to `done/` only when transitioned).
   * If budget is exhausted or the agent gets stuck, handoff records evidence and tags the ticket with `anvil-needs-human`.
   * The container sandbox remains accessible for inspection (`docker exec -it anvil-sbx-* bash`) and is automatically reaped by the ANVIL-47 reaper after the 2-hour grace period.

---

## 6. Troubleshooting & FAQs

### Q: Does local checkout support git submodules?
**A:** Local repository sources (`C:\work\...`) do not support submodules in the current release. TC1 operates self-contained within RTEMS core and does not require submodules. Preflight Check 7 can be safely ignored.

### Q: `preflight.ps1` Check 6 fails with `reset_demo.py check failed`?
**A:** Check 6 verifies cloud Jira baseline states. For an offline local demo using `ANVIL_TICKETS_DIR`, Check 6 is expected to fail or report drift and does not block offline execution.

### Q: `import-sandbox-image.ps1` reports `SHA-256 mismatch`?
**A:** The tar archive was corrupted during copy or is incomplete. Re-copy the file along with `anvil-sandbox-rtems-7.1.tar.sha256` and rerun the import script.

### Q: API fails immediately on startup?
**A:** Check `api-8000.err.log`. Ensure PostgreSQL container is running (`docker ps`) and accepting connections on port `5433`. Run `.\scripts\up.ps1` to restore the container stack.
