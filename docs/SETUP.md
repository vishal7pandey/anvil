# Anvil Setup Guide: Zero-Network Bootstrap ("Zip to First Run")

> **Target Audience:** Team members running Anvil locally on isolated enterprise laptops.  
> **Connectivity:** 100% Offline. No internet access, no GitHub (`github.com`), and no Jira cloud (`vishal7pandey.atlassian.net`) required.

---

## 1. Overview & Architecture

For offline demonstrations and disconnected environments, Anvil operates completely within local boundaries:

* **Codebase Source:** Points directly to an absolute local git checkout (e.g. `C:\work\rtems`) rather than a remote GitHub mirror.
* **Ticket Store:** Evaluates file-based tickets in JSON format (`todo/`, `in-progress/`, `done/`) via the local tracker rather than Cloud Jira.
* **Knowledge Base:** Ingests pre-embedded vector snapshots locally without external API calls.
* **Artifact Delivery:** Produces standard git patch files (`.patch`) and git bundles (`.bundle`) in `private/runs/<run_id>/` on the host filesystem.

---

## 2. Prerequisites

Ensure your laptop meets the following baseline requirements before unzipping:

| Component | Requirement | Verification Command |
|---|---|---|
| **OS** | Windows 11 / WSL2, macOS, or Ubuntu 22.04+ | `cmd.exe /c ver` or `uname -a` |
| **Docker** | Docker Desktop or Docker Engine (Min 7 GB RAM allocated) | `docker info` |
| **Python** | Python 3.12 or 3.13 (CPython signed on Windows) | `uv run python --version` |
| **Node.js** | Node.js 20+ and `pnpm` | `pnpm --version` |
| **Package Manager**| `uv` package and environment manager | `uv --version` |

> [!NOTE]
> On Windows machines with Microsoft Smart App Control, standalone binary wrappers like `pytest.exe` may be blocked. Always run Python utilities via the `uv run python -m <tool>` syntax.

---

## 3. Package Contents (The Distribution Zip)

When unpacking the offline distribution bundle (`anvil-offline-dist.zip`), verify that the following core assets are present:

```text
anvil/
├── backend/                  # FastMCP/FastAPI backend & DeepAgents runtime
├── frontend/                 # Vite/React enterprise web interface
├── data/
│   ├── sandbox/              # Offline Docker container images
│   │   ├── anvil-sandbox-rtems-7.1.tar
│   │   ├── anvil-sandbox-rtems-7.1.tar.sha256
│   │   └── anvil-sandbox-rtems-7.1.tar.manifest.json
│   └── kb/                   # Pre-crawled and pre-embedded knowledge base
│       ├── kb-snapshot-20261104.jsonl.gz
│       └── kb-snapshot-vectors.manifest.json
├── docs/                     # Architecture, specifications, and runbooks
├── scripts/                  # Automation scripts (preflight, up, import, reset)
└── .env.example              # Baseline offline configuration template
```

In addition, ensure you have a local working copy of the target repository (for TC1: RTEMS on branch `5776-base` at e.g. `C:\work\rtems`).

---

## 4. Step-by-Step Installation

### Step 1: Configure Environment Variables

Copy `.env.example` to `.env` in the repository root:

```powershell
Copy-Item .env.example .env
```

Ensure the offline configuration flags are set:

```ini
# Database & Gateway
DATABASE_URL=postgresql+psycopg://anvil:anvil@localhost:5433/anvil
KB_DATABASE_URL=postgresql+psycopg://anvil:anvil@localhost:5433/anvil
LITELLM_BASE_URL=http://localhost:4000
OPENAI_API_BASE=http://localhost:4000
OPENAI_API_KEY=sk-local-dev-gateway

# Local Ticket Tracker (Offline Mode)
ANVIL_TICKETS_DIR=C:\Dev\projects\anvil\tickets

# Security / Demo Tokens
ANVIL_DEMO_TOKEN=anvil-demo-token-2023-11-30
VITE_DEMO_TOKEN=anvil-demo-token-2023-11-30

# Do NOT configure ATLASSIAN_* or GITHUB_* tokens for offline execution
```

---

### Step 2: Import the Docker Sandbox Image

Import the pre-built RTEMS compilation sandbox image (`anvil-sandbox-rtems:7.1`) into your Docker daemon using the integrity verification script:

```powershell
.\scripts\import-sandbox-image.ps1 -ImagePath data\sandbox\anvil-sandbox-rtems-7.1.tar
```

**Verification Details:**
* The script reads `anvil-sandbox-rtems-7.1.tar.sha256` and fails closed if the hash does not match.
* It verifies that the loaded Docker image matches expected image ID prefix `bc17ec29`.
* It ensures the image is tagged as `anvil-sandbox-rtems:7.1`.

---

### Step 3: Launch Supporting Stack Containers

Start PostgreSQL (with `pgvector`) and the LiteLLM Gateway proxy:

```powershell
.\scripts\up.ps1
```

> [!IMPORTANT]
> The Anvil Control API enforces fail-fast database startup checks (PR #70). If Postgres is not accepting connections at boot time, the API terminates immediately.

---

### Step 4: Import Pre-Embedded Knowledge Base Snapshot

Load the offline knowledge base snapshot with vectors:

```powershell
uv run --directory backend python -m anvil.tools.kb_admin import-snapshot --with-vectors data\kb\kb-snapshot-20261104.jsonl.gz
```

* The vector snapshot includes pre-computed embeddings paired with `kb-snapshot-vectors.manifest.json`.
* Query-time embedding requests at runtime will still route locally through the LiteLLM proxy gateway on port `4000`.

---

### Step 5: Run Preflight Check

Verify that all subsystems, ports, dependencies, and images are properly initialized:

```powershell
.\scripts\preflight.ps1
```

Confirm that Check 1 through Check 6 report `[PASS]`.

---

## 5. Running the Offline Demo (TC1 Walkthrough)

### 1. Stage the Target Ticket

Create or copy the TC1 demonstration ticket into your local ticket tracker's `todo/` directory:

Directory: `C:\Dev\projects\anvil\tickets\todo\RTEMS-1.json`

```json
{
  "key": "RTEMS-1",
  "summary": "posix: Leave mutex object unmodified on pthread_mutex_init() failure",
  "status": "To Do",
  "labels": ["posix", "tc1", "anvil"],
  "description": "Code base: branch 5776-base of C:\\work\\rtems\n\nWhen pthread_mutex_init() fails (e.g. invalid ceiling priority), POSIX requires that the mutex object remain untouched. Currently, internal flags are written before parameter validation completes."
}
```

> [!IMPORTANT]
> 1. **Case Selection:** The ticket `key` must be `"RTEMS-1"`. The intake daemon inspects the key to attach the Knowledge Base historical time guard (preventing future leaks) and to bind automated acceptance criteria.
> 2. **Local Codebase Path:** The description must contain `Code base: branch <base> of <absolute_path>` with **no spaces** in the path.

---

### 2. Launch the Control API and Frontend

In Terminal 1 (API Daemon):
```powershell
.\scripts\start-api.ps1
```

In Terminal 2 (Frontend UI):
```powershell
cd frontend
pnpm install
pnpm dev
```

Open your browser to `http://localhost:5173`.

---

### 3. Execution & Verification Flow

1. **Intake Ingestion:** The background poller scans `tickets/todo/`, discovers `RTEMS-1.json`, transitions the file to `tickets/in-progress/RTEMS-1.json`, and starts the run.
2. **Session Stream:** In the UI, the run stream appears with structured chat items (deltas folded, collapsible actions, unified tool executions).
3. **Sandbox Execution:** The container clones from `C:\work\rtems`, navigates the build tree, applies the fix to `cpukit/posix/src/mutexinit.c`, builds RTEMS via `./waf`, and executes the regression test under the SPARC/ERC32 SIS simulator.
4. **Outcome & Artifact Generation:**
   * Acceptance verification evaluates the SIS simulator test pass output.
   * Host-side evidence is written to `private/runs/<run_id>/<run_id>.patch` and `.bundle`.
   * A completion note referencing the generated patch is appended to `tickets/done/RTEMS-1.json`.
   * The container sandbox is released.

---

## 6. Troubleshooting & FAQs

### Q: `preflight.ps1` fails on GitLab or GitHub submodules?
**A:** Submodule network connectivity checks are optional during fully disconnected runs. Ensure your local git checkout (`C:\work\rtems`) already has all required submodule trees present locally.

### Q: `import-sandbox-image.ps1` reports `SHA-256 mismatch`?
**A:** The tar archive was corrupted during transfer or is incomplete. Re-copy the file along with `anvil-sandbox-rtems-7.1.tar.sha256` and rerun the import script.

### Q: API fails immediately on startup?
**A:** Check `api-8000.err.log`. Ensure PostgreSQL container is running (`docker ps`) and accepting connections on port `5433`. Run `.\scripts\up.ps1` to restore the container stack.
