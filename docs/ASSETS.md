# ASSETS — everything Anvil needs to run, and where it lives

Owner's item 4: "everything required to run is in one codebase." An asset is either **in git**
(clone and it is there) or **out-of-band** (big or secret, lives beside the repo — never committed).
For every asset below: where it lives, its size/SHA when out-of-band, and how to **regenerate** it
from the repo. Anything required that cannot be reproduced is a **defect** (listed at the bottom).

`scripts/verify-assets.ps1` checks what's checkable (files present, sha256 against
`handover/MANIFEST.json`, image tag, ports free).

## In git — clone and it works

| Asset | Path | Regenerate |
|-------|------|-----------|
| Backend + runtime | `backend/` (uv project) | `uv sync` |
| Frontend | `frontend/` | `npm ci` |
| Sandbox image source | `sandbox/rtems/Dockerfile` (+ `Dockerfile.leon3`, `sbom.sh`, `smoke.sh`) | build (below) |
| Compose + gateway | `deploy/compose.yaml`, `deploy/initdb/` | `up.ps1` / `docker compose` |
| LiteLLM gateway config | `gateway/` | — |
| Cases + ticket specs | `cases/*.toml` + `*.ticket.md` (rtems-5776, rtems-5806, rtems-lwip-2.2.1) | — |
| Acceptance check scripts | `backend/anvil/cases/checks/` (uploaded to `/opt/anvil/checks/`) | — |
| Backlog (offline Jira copy) | `backlog/` (`index.json`, `STATUS.md`, `ANVIL-*.json`, `RTEMS-*.json`) | `backend/scripts/export_trackers.py` |
| KB snapshot (text) | `data/kb/kb-snapshot.jsonl.gz` + `manifest.json` (committed; sha256 `971c6b64…`) | `export-snapshot` |
| Docs | `docs/` (SETUP, demo-runbook, dry-run-checklist, ASSETS) | — |
| Setup scripts | `scripts/` (start-api, preflight, up, export/import-sandbox-image) | — |
| Migrations | `backend/migrations/` | `alembic upgrade head` |

## Out-of-band — beside the repo, never committed (all gitignored)

| Asset | Path / tag | Size | Regenerate from the repo |
|-------|-----------|------|--------------------------|
| KB vector sidecar | `data/kb/kb-snapshot-vectors.jsonl.gz` + `…vectors.manifest.json` | **88 MB** | `export-snapshot --with-vectors`, or re-embed the committed text snapshot (query embeddings only need the gateway — under a dollar) |
| Sandbox image tar | `data/sandbox/anvil-sandbox-rtems-7.1.tar` (+ `.sha256`) | **1.6 GB** | `docker build sandbox/rtems/` (1–2 h, network for the toolchain), or import the verified tar (sha256 `CA94D60C…`, image id `bc17ec29fb13`) |
| TC1 base | the bare, blobless clone under the repos dir (`backend/anvil/cases/base.py` fetches refs/heads/* from the upstream mirror) | **143 MB** | `cases/base.py` rebuild — needs network to the mirror |
| `.env` | repo root | — | copy `.env.example`; `ANVIL_DEMO_TOKEN` required, Atlassian only for online |
| `frontend/.env` | `frontend/` | — | copy `frontend/.env.example` (or leave unset for the Vite proxy) |
| Local tickets | `ANVIL_TICKETS_DIR` (e.g. `tickets/`) | — | created by intake; seed from `backlog/RTEMS/` |
| Local knowledge | `kb/local/` | — | created by `confluence_write` on a local run |

## Defects (required but NOT reproducible from the repo)

- **None found.** Every required asset either lives in git or regenerates from it (source builds,
  script exports, mirror fetches). The two big binaries (image tar, vector sidecar) are out-of-band
  by size only — both rebuild from in-repo sources. The KB text snapshot is committed, so a fully
  offline run needs only the two gitignored binaries beside the repo.
