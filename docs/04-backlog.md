# 04 Backlog, milestones, team

Jira project `ANVIL` tracks building the factory. Jira project `RTEMS` is the *product backlog the
factory works on* (TC1/TC4 tickets live there). Keys below become Jira issues; this file is the
seed, Jira is the record afterwards.

## Team

| Agent | Role | Owns | Why this fit |
|---|---|---|---|
| Claude (Claude Code) | PM, architect, reviewer of every PR | Runtime core (WS-B), skills, demo content, merges after owner OK | Holds the design; reviews keep the contracts honest |
| Codex | Infra engineer | WS-A sandbox + CI, WS-F quality gates | Shell/Docker/Actions-heavy, long builds |
| Devin | Integrations engineer | WS-E Jira/Confluence/GitHub tools, intake, webhooks | Long autonomous tasks against external APIs |
| GitHub Copilot (coding agent) | Backend engineer | WS-C control API | Works from GitHub issues, small well-specified PRs |
| Cline | Frontend engineer | WS-D web UI | Iterates visually in VS Code with a browser |
| OpenCode | Data/eval engineer | WS-G knowledge ingestion + kb_search, TC1 eval harness | Self-contained pipelines |

Rules of engagement are in [AGENTS.md](../AGENTS.md).

## Milestones

| Milestone | Exit criterion | Target |
|---|---|---|
| M0 Foundations | Mirrors pushed; Jira/Confluence exist; sandbox image builds and `anvil-smoke` passes; compose runs Postgres + gateway | Day 1 |
| M1 Walking skeleton | In the UI: new session on `rtems`, ask "build erc32 and run hello", watch plan, tool calls and SIS output stream; run record saved | Day 3 |
| M2 TC1 closed loop | Jira ticket -> PR -> CI (build, test, CodeQL, Sonar) -> agent reacts -> evidence -> human merge; forced stuck run hands off | Day 6 |
| M3 TC2 + TC3 | Upgrade PR with SBOM/CVE delta; CRA artifacts set in Confluence + Jira stories | Day 9 |
| M4 TC4 + rehearsal | KB investigation end to end; metrics page; three clean recorded runs per scenario | Day 12 |

## Workstreams and stories (M0-M2 detailed, M3-M4 coarse)

### WS-A Sandbox (Codex)
* A1 Sandbox image builds; `anvil-smoke` passes (hello on SIS). *Started by Claude, build running.*
* A2 Publish image to GHCR `ghcr.io/vishal7pandey/anvil-sandbox-rtems:7` with digest pinned in config.
* A3 Add leon3 BSP + `rtems-test` config for the posix/psx and sp test subsets; document test
  commands in `sandbox/rtems/README.md` (the agent's skill reads it).
* A4 Add ccache volume so rebuilds in a session take < 2 min.

### WS-B Agent runtime (Claude)
* B1 `DockerSandbox(BaseSandbox)`: execute/upload/download/id over docker SDK; limits; no creds.
* B2 `build_agent(run)`: `create_deep_agent` with gateway model, CompositeBackend
  (`/workspace` sandbox, `/memories` store), Postgres checkpointer, `interrupt_on` policy.
* B3 Subagents: investigator, builder, reviewer, writer, compliance (prompts in `backend/anvil/agents/`).
* B4 Skills: `defect-fix`, `component-upgrade`, `cra-review`, `investigate` (`SKILL.md` each).
* B5 Event adapter: LangGraph stream -> event contract in 02-architecture.
* B6 Stuck detector + `handoff` tool (rules in 02-architecture).
* B7 Budgets: per-run token/USD/wall-time from gateway spend + local counters.

### WS-C Control API (Copilot)
* C1 FastAPI skeleton, Postgres models (users, sessions, runs, events, approvals), Alembic.
* C2 REST + SSE per contract; replay events from `seq`.
* C3 Approvals endpoint resumes the LangGraph interrupt.
* C4 Run record endpoint (reproducibility fields).

### WS-D Web UI (Cline)
* D1 Layout: sessions sidebar, chat, right panel (plan / files / diff / run record).
* D2 New-session dialog: repo, environment (Local / Cloud - disabled until M4), mode, model profile.
* D3 Event rendering per type, collapsible tool calls, terminal block, diff viewer.
* D4 Approvals inbox + inline approve/reject; cost meter.

### WS-E Integrations (Devin)
* E1 Host tools: `jira_get/comment/transition/create`, `confluence_search/get/write`.
* E2 Host tools: `github_open_pr` (patch export from sandbox -> host clone -> push), `github_checks`,
  `github_code_scanning_alerts`, `sonar_issues`.
* E3 Jira intake poller + GitHub webhook (merged -> Done + lesson).
* E4 `web_search` tool (OpenAI web search via the gateway; fallback Tavily if needed).

### WS-F Quality gates (Codex)
* F1 Actions workflow on the `rtems` mirror: build erc32/leon3 in the sandbox image, run test
  subset on SIS, upload logs.
* F2 CodeQL C/C++ (manual build mode) on PRs; Dependabot config where an ecosystem exists
  (`gitsubmodule` on rtems-libbsd, `github-actions` everywhere).
* F3 SonarQube scan on PRs (owner's instance; token as repo secret).
* F4 Syft SBOM (SPDX + CycloneDX) and OSV-Scanner as artifacts; cppcheck + clang-format check.

### WS-G Knowledge + eval (OpenCode)
* G1 Ingest closed RTEMS GitLab issues + rtems-docs into pgvector; `kb_search` tool.
* G2 TC1 eval harness: create `tc1/<issue>-base` branches, Jira tickets from upstream text,
  score agent PR vs. upstream fix (diff overlap + human judgement sheet).
* G3 Seed the Confluence KB space (synthetic notes, runbooks) for TC4.

### Gateway and stack (Claude, M0)
* M0-1 `deploy/compose.yaml`: Postgres 17 + pgvector, LiteLLM with `gateway/config.yaml`.
* M0-2 `.env.example`, `uv` project for backend, Vite app for frontend.

## Order of work

M0 is serial and mine. Then WS-A, B, C, D, E, F, G run in parallel against the contracts in
02-architecture. B1 + C1 + D1 meet in M1. E and F join for M2.
