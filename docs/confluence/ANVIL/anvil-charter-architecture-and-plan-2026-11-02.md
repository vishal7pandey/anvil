<!-- Confluence ANVIL page 28541121, version 1, exported for offline use; parent 28475566 -->

# Anvil: charter, architecture and plan (2026-11-02)

Source of truth: the `anvil` repo (`docs/01-vision.md` to `docs/04-backlog.md`, `docs/adr/`). This page summarises it for people and agents without the repo.

## Goal

Show, live on a real embedded codebase (RTEMS), an AI factory that goes from a Jira ticket to a reviewed, CI-green pull request with evidence, and that knows when to stop and hand back. Thin but real: every box does work you can watch; nothing is a mock-up.

## Architecture (7 components)

| # | Component | Tech |
|---|---|---|
| C1 | Web UI: sessions, chat, plan, tool calls, diffs, approvals, environment picker, cost | React + Vite |
| C2 | Control API: sessions, runs, SSE events, approvals, Jira intake, GitHub webhooks | FastAPI + Postgres |
| C3 | Agent runtime: main agent + subagents (investigator, builder, reviewer, writer, compliance) + skills | LangChain Deep Agents 0.7 on LangGraph 1.x |
| C4 | Model gateway: aliases, spend per run, budgets, fallbacks | LiteLLM proxy -> OpenAI |
| C5 | Sandbox: one container per session, RTEMS 7 SPARC toolchain + SIS simulator, no credentials inside | Docker |
| C6 | Knowledge + memory: KB index of tickets/docs/notes, agent memories | Postgres + pgvector |
| C7 | Quality gates on PRs: build + tests on simulator, CodeQL, SonarQube, cppcheck, SBOM, OSV | GitHub Actions, same sandbox image |

## Closed loop

Jira `RTEMS` issue labelled `anvil` -> intake -> sandbox -> plan, reproduce, fix, build, test on SIS -> self-review -> PR (approval-gated, pushed from host) -> CI -> agent reacts to failures and alerts -> evidence to Jira and this space -> human merges -> Jira Done, lesson to memory. Stuck (budget, repeated failure, no progress, CI red x3) -> WIP branch, handoff note, Jira _Needs human_.

## Scenarios

TC1 defect fix with upstream fix withheld (primary: RTEMS #5776, fix !1493). TC2 lwIP upgrade in rtems-lwip with SBOM/CVE delta. TC3 EU Cyber Resilience Act review of a reference RTEMS networked device. TC4 incident investigation from a knowledge base (RTKB space).

## Milestones

| Milestone | Exit | Target |
|---|---|---|
| M0 Foundations | mirrors, trackers, sandbox image + smoke test, compose stack | Day 1 |
| M1 Walking skeleton | UI -> API -> agent -> sandbox, streamed, run record | Day 3 |
| M2 TC1 closed loop | ticket -> PR -> CI -> evidence -> merge; forced handoff | Day 6 |
| M3 TC2 + TC3 | upgrade PR + CRA artefacts | Day 9 |
| M4 TC4 + rehearsal | KB investigation, metrics, 3 clean runs each | Day 12 |

## Team

Claude: PM, architect, runtime core, reviews every PR. Codex: sandbox + CI. Devin: Jira/Confluence/GitHub tools, intake. GitHub Copilot: control API. Cline: web UI. OpenCode: knowledge ingestion + TC1 eval harness.

## Decisions (proposed)

Deep Agents harness (model-agnostic, built-in planning/subagents/memory/HITL/checkpoints); LiteLLM gateway; Docker sandbox per session; credentials never in the sandbox; mirrors not forks; two Jira projects (ANVIL builds the factory, RTEMS is the backlog it serves) and two spaces (ANVIL, RTKB); agentic code search; Syft + OSV-Scanner + ScanCode instead of Black Duck.
