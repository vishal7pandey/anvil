# 04 Backlog, milestones, team

Jira project `ANVIL` (board 138) tracks building the factory. Jira project `RTEMS` (board 139) is the *product backlog the
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
| Antigravity | Scenario + QA engineer | WS-H TC2 setup, end-to-end browser tests, demo runbook; WS-G knowledge base (TC4) | Built-in browser agent; can drive and check the UI like a user |

Rules of engagement are in [AGENTS.md](../AGENTS.md).

## Milestones (demo 2026-11-10, may slip 3-4 days)

| Milestone | Exit criterion | Date |
|---|---|---|
| M0 Foundations | Mirrors pushed; Jira/Confluence exist; sandbox image builds and `anvil-smoke` passes; compose runs Postgres + gateway | Tue 11-03 |
| M1 Walking skeleton | In the UI: new session on `rtems`, ask "build erc32 and run hello", watch plan, tool calls and SIS output stream; run record saved | Thu 11-05 |
| M2 TC1 closed loop | Jira ticket -> branch -> fix -> tests -> pre-commit -> commit -> push -> PR -> CI -> agent reacts -> evidence -> merge (by policy); forced stuck run hands off | Sat 11-07 |
| M3 TC2 + TC3 | Same loop, new skills: upgrade PR with SBOM/CVE delta; CRA artefacts in RTKB + Jira stories | Sun 11-08 |
| M4 TC4 + rehearsal | KB investigation end to end; metrics page; three clean recorded runs of TC1 | Mon 11-09 |

**Cut line if we slip:** TC1 must be flawless; it is the scenario reviewers judge hardest. TC2-TC4
are the same runtime with different skills, so they degrade to "shown live, less polished"
before TC1 loses anything. The Cloud environment option stays a stretch goal.

## Workstreams and stories (M0-M2 detailed, M3-M4 coarse)

### WS-A Sandbox (Codex)
* A1 Sandbox image builds; `anvil-smoke` passes (hello on SIS). *Started by Claude, build running.*
* A2 Publish image to GHCR `ghcr.io/vishal7pandey/anvil-sandbox-rtems:7` with digest pinned in config.
* A3 Add leon3 BSP + `rtems-test` config for the posix/psx and sp test subsets; document test
  commands in `sandbox/rtems/README.md` (the agent's skill reads it).
* A4 Add ccache volume so rebuilds in a session take < 2 min.
* A5 `pre-commit` + `.pre-commit-config.yaml` for the rtems mirror (clang-format changed lines, whitespace, cppcheck, commit-msg key check); installed in the image.

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
* E2 Host tools: `git_push` (git bundle from sandbox -> host clone -> push), `github_open_pr`,
  `github_checks`, `github_code_scanning_alerts`, `sonar_issues`, `github_merge` (policy-checked).
* E3 Jira intake poller + GitHub webhook (merged -> Done + lesson).
* E4 `web_search` tool (OpenAI web search via the gateway; fallback Tavily if needed).

### WS-F Quality gates (Codex)
* F1 Actions workflow on the `rtems` mirror: build erc32/leon3 in the sandbox image, run test
  subset on SIS, upload logs.
* F2 CodeQL C/C++ (manual build mode) on PRs; Dependabot config where an ecosystem exists
  (`gitsubmodule` on rtems-libbsd, `github-actions` everywhere).
* F3 SonarCloud scan on PRs to the public `rtems` mirror (free for public repos; same setup as `ai-software-factory/docs/sonarcloud.md`; owner sets `SONAR_TOKEN`).
* F4 Syft SBOM (SPDX + CycloneDX) and OSV-Scanner as artifacts; cppcheck + clang-format check.

### WS-G Knowledge + eval (Antigravity: G1, G3; Claude: G2)

OpenCode was removed from the project on 2026-11-02 (owner decision). Its G1 work (PR #3) is kept
and adopted by Antigravity.
* G1 Ingest closed RTEMS GitLab issues + rtems-docs into pgvector; `kb_search` tool.
* G2 TC1 eval harness: create `tc1/<issue>-base` branches, Jira tickets from upstream text,
  score agent PR vs. upstream fix (diff overlap + human judgement sheet).
* G3 Seed the Confluence KB space (synthetic notes, runbooks) for TC4.

### WS-H Demo scenarios + QA (Antigravity)
* H1 TC2 spike: lwIP upgrade path, demo target, `docs/scenarios/tc2.md`, RTEMS ticket (ANVIL-17).
* H2 End-to-end browser tests of the TC1 flow in the UI (M2-M4).
* H3 Demo runbook, reset script, three recorded rehearsals (M4).

### Gateway and stack (Claude, M0)
* M0-1 `deploy/compose.yaml`: Postgres 17 + pgvector, LiteLLM with `gateway/config.yaml`.
* M0-2 `.env.example`, `uv` project for backend, Vite app for frontend.

## Order of work

M0 is serial and mine. Then WS-A, B, C, D, E, F, G run in parallel against the contracts in
02-architecture. B1 + C1 + D1 meet in M1. E and F join for M2.
