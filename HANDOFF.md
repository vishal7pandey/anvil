# Handoff: start here

This repository is a complete, offline copy of the Anvil project as of **2026-11-05**. Until now the
project lived in GitHub (code), Jira (backlog) and Confluence (status and specs). You may not have
access to any of those, so everything you need is in this folder:

**Start with [`docs/SETUP.md`](docs/SETUP.md)** — it walks a clean machine to a first run, including
the fully offline path (no GitHub, no Jira): point a ticket at a local folder on disk and let the
local JSON ticket tracker stand in for Jira.

| You'd normally look in | Look here instead |
|---|---|
| Jira projects ANVIL (the work) and RTEMS (the demo tickets) | [`backlog/`](backlog/README.md): one JSON file per issue, with its full description and comment history |
| Confluence space ANVIL (charter, status page) | [`docs/confluence/ANVIL/`](docs/confluence/ANVIL/) |
| Confluence space RTKB (the knowledge base pages for the TC4 scenario) | [`docs/confluence/RTKB/`](docs/confluence/RTKB/) |
| The knowledge-base database (RTEMS GitLab issues and MRs with comments, RTEMS docs, RTKB pages) | [`data/kb/`](data/kb/): a 5.5 MB snapshot you load with one command (below) |
| Bug-replay test cases (a real upstream bug, the code before its fix, the ticket) | [`cases/`](cases/) |

## What Anvil is, in one paragraph

Anvil is an AI agent that takes a ticket, works on real embedded C code (RTEMS, an open-source
real-time OS) inside a Docker sandbox with the real SPARC cross-compiler and simulator, writes the fix
and a regression test, runs the tests on the simulator, opens a pull request, and reacts to CI. When
it gets stuck or runs out of budget, it stops, saves its work on a branch and explains why. Read
[README.md](README.md), then [docs/01-vision.md](docs/01-vision.md) and
[docs/02-architecture.md](docs/02-architecture.md).

## Where it stands (2026-11-05)

| Milestone | State |
|---|---|
| M0 Foundations (mirrors, sandbox image, local stack) | Done |
| M1 Walking skeleton (UI -> API -> agent -> sandbox) | Nearly done. Jira intake starts a real run; a re-run through intake and the approve/reject/cancel check remain (ANVIL-24, ANVIL-19) |
| M2 TC1: fix a real bug end to end | Works online. 3 of 3 runs found the same root cause as the real upstream fix |
| Offline demo path | Built this week: local folder source (no GitHub), local JSON tickets (no Jira), KB snapshot with vectors (no re-embed), sandbox image as a file. The whole chain is proven by `backend/tests/test_offline_chain.py` (ticket -> patch in ~4 min, no LLM); a clean-laptop dry run is the remaining check |
| M3 TC2 (upgrade lwIP) + TC3 (EU CRA review) | TC3 interactive intake exists (`anvil-interactive` label). TC2 was at risk on patch-loss; unverified lately |
| M4 TC4 (incident investigation from the KB) + rehearsal | KB complete; TC4 acceptance passes 3/3 |

The latest status page with tables is
[docs/confluence/ANVIL/anvil-status-and-plan-to-the-2026-11-10-demo.md](docs/confluence/ANVIL/anvil-status-and-plan-to-the-2026-11-10-demo.md).

**The board lives in two places now:** `backlog/` is the offline export of Jira (every ANVIL/RTEMS
issue with comments; open work is every file whose `status` isn't `Done`), and the local JSON ticket
tracker — `todo/`, `in-progress/`, `done/` folders under the directory in `ANVIL_TICKETS_DIR` — is
where *new* tickets go when you run the offline path. The intake polls both; a ticket whose `Code
base:` line points at a local path runs fully offline.

## The offline path (no GitHub, no Jira, no Atlassian)

`docs/SETUP.md` §"100% offline" is the full walkthrough. In short:

- Ticket = a JSON file in `$ANVIL_TICKETS_DIR/todo/` labelled `anvil`, ending with
  `Code base: branch <base> of <absolute path>`. The path may be a git working copy (any branch;
  leave the branch out to use the one checked out), a plain folder, or a .zip/.tar.gz of either.
  Put a path with spaces in double quotes. See SETUP.md §5b.
- The intake moves it to `in-progress/`, the sandbox gets a shallow copy of the base branch only
  (your other branches — including one holding a withheld fix — never leave the host), and the
  agent's commits come back as `private/runs/<run_id>/<run_id>.patch` + `.bundle` — apply with
  `git am`. `jira_*` tool calls write to the ticket JSON; GitHub tools are not offered.
- The KB travels as `data/kb/kb-snapshot.jsonl.gz` (committed) plus the vectors sidecar
  `kb-snapshot-vectors.jsonl.gz` + its manifest (shared out-of-band; import skips re-embedding when
  the model + hash pair matches). Only *query* embeddings still need the LiteLLM gateway.
- The sandbox image travels as `anvil-sandbox-rtems-7.1.tar` + sha256 manifest;
  `scripts\import-sandbox-image.ps1` verifies and loads it.

## Known limits

- Local projects: any absolute path (spaces fine) to a git working copy, a plain folder or an
  archive (ANVIL-91). Uncommitted changes in a working copy are reported but not copied. Git
  submodules inside a local project are not copied. The sandbox has RTEMS 7 / SPARC, Python 3 and
  common build tools; other toolchains are not installed.
- `git push`/PR/CI tools only exist for `owner/repo` sources; local runs produce patch + bundle only.
- The ticket key selects the case (`RTEMS-1` binds the TC1 case: KB time-guard + acceptance checks).
- Query-side KB embeddings need the gateway (document vectors are imported, queries are not).
- The sandbox is amd64 Linux; Windows hosts run it through Docker Desktop/WSL2.

## Run it

You need Docker, [uv](https://docs.astral.sh/uv/) and an OpenAI API key. On Windows, see the gotchas below.

```bash
cp .env.example .env            # fill in what you have; the Jira/GitHub values are optional (see below)

# 1. Sandbox image: RTEMS 7 SPARC toolchain + SIS simulator. 1-2 h the first time.
docker build -t anvil-sandbox-rtems:7 sandbox/rtems
docker build -f sandbox/rtems/Dockerfile.leon3 -t anvil-sandbox-rtems:7.1 sandbox/rtems

# 2. Local stack: Postgres + pgvector, and the LiteLLM model gateway.
docker compose -f deploy/compose.yaml up -d --wait

# 3. Backend, and the knowledge base from the snapshot (re-embeds ~14.6k chunks through the gateway;
#    costs well under 1 USD with the default embedding model).
cd backend
uv sync
uv run python -m anvil.kb init-db
uv run python -m anvil.kb import-snapshot
uv run python -m anvil.kb search "spurious watchdog reset" --k 3     # check it works

# 4. Run the agent on the first bug-replay case, without the API, Jira or GitHub:
uv run python scripts/tc1_dry_run.py --out ../private/my-first-run --case rtems-5776 \
    --db postgresql://anvil:anvil@localhost:5433/anvil
```

The dry run clones the public RTEMS mirror (github.com/vishal7pandey/rtems), gives the agent the
ticket from `cases/rtems-5776.ticket.md` and an offline sandbox, and saves the event log, the agent's
diff and a score against the real upstream fix in `--out`. It takes 20-45 minutes. The demo runbook
for the full UI + API flow is [docs/demo-runbook.md](docs/demo-runbook.md).

### What needs our accounts, and what to do without them

| Feature | Needs | Without it |
|---|---|---|
| Jira intake (label a ticket `anvil` and a run starts) | Atlassian site + token | Use `scripts/tc1_dry_run.py` or the UI's "new session" with the ticket text from `cases/` |
| Push a branch, open a PR, read CI (`git_push`, `github_*` tools) | A GitHub token with write access to the mirror | Fork the public mirrors to your own account, set `GITHUB_OWNER` and `GITHUB_TOKEN` in `.env` |
| Jira comments and Confluence evidence | Atlassian | The agent's evidence is still in the run's event log and the UI |
| Re-crawl the KB from GitLab | Nothing: gitlab.rtems.org is public | `uv run python -m anvil.kb ingest-gitlab` (slow: rate-limited to 60 requests/min) |

## Rules you must keep

* **Never let the agent see the answer.** A bug-replay case withholds the upstream fix: the KB
  excludes the case's issue, MR and fix commit; the sandbox clones only the pre-fix base branch and
  goes offline; scoring against the real fix runs on the host only. If you add a case, follow
  [`cases/rtems-5776.toml`](cases/rtems-5776.toml) and write the ticket as symptoms only, never the
  root cause or the expected outcome.
* **Secrets stay in `.env`** (gitignored). Never commit them; the sandbox gets no credentials at all.
* `private/` is gitignored on purpose; keep anything confidential there.

## How we worked, and how you can

We built this in a few days with several AI coding agents working in parallel, coordinating through
Jira, pull requests and a shared inbox folder. You don't need that setup. The useful habits were:

* One small branch per backlog item, with tests, reviewed by someone else before merging.
* Write the decision and the numbers on the backlog item when you decide something, as the comments in
  `backlog/` show. Most of the "why" in this project is in those comments.
* Test leak controls through the real path (ticket -> checkout -> agent), not only in unit tests.

## Running the tests

```bash
cd backend
uv run python -m pytest                # unit + integration suite
uv run python -m pytest tests/test_offline_chain.py   # the end-to-end offline chain (needs Docker + the 7.1 image)
cd ../frontend && npm test
```

Tests that need Docker or the sandbox image skip themselves when those aren't present.

## Gotchas (Windows laptop)

* Smart App Control may block uv's downloaded Python. Use a signed CPython 3.13
  (`uv sync --python <path-to-python.exe>`), then `uv run --no-sync ...`.
* `pytest.exe` may be blocked too: use `uv run python -m pytest`.
* In Git Bash, prefix `docker exec ... /path` with `MSYS_NO_PATHCONV=1`.
* The sandbox image is large (several GB). Build it once and keep the tag; never `docker commit` over it.

## Refreshing this snapshot (only while the original trackers still exist)

```bash
cd backend
uv run python scripts/export_trackers.py --out ..     # backlog/ and docs/confluence/
uv run python -m anvil.kb export-snapshot             # data/kb/
```
