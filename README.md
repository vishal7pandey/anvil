# Anvil

A small, real, near-closed-loop **AI software factory** for embedded C code, built as a demo.
It takes a ticket from Jira, works on a real codebase (RTEMS, the open-source RTOS) inside a
container sandbox with a real cross toolchain and simulator, opens a pull request, reads the CI
and code-scanning results, fixes what they flag, writes the evidence back to Jira and Confluence,
and hands the work to a human for the merge. When it gets stuck, it stops, saves its progress
and says why.

It is deliberately thin: one agent harness, one sandbox image, one API, one web UI. Every box in
the architecture does something you can watch in the demo; nothing is a mock-up.

| What | Where |
|---|---|
| Why and what (scope, success criteria) | [docs/01-vision.md](docs/01-vision.md) |
| How (architecture, contracts) | [docs/02-architecture.md](docs/02-architecture.md) |
| The four demo scenarios | [docs/03-demo-scenarios.md](docs/03-demo-scenarios.md) |
| **New to the project? Start here** (offline backlog, docs, KB data) | [HANDOFF.md](HANDOFF.md) |
| Backlog: live state of every item | [backlog/](backlog/README.md) (the original plan: [docs/04-backlog.md](docs/04-backlog.md)) |
| Decisions | [docs/adr/](docs/adr/) |
| Rules for the agents building this | [AGENTS.md](AGENTS.md) |

## Layout

```
sandbox/rtems/   Sandbox image: RTEMS 7 SPARC toolchain + SIS simulator (also used by CI)
gateway/         LiteLLM model gateway config (model aliases, budgets)
backend/         FastAPI control API + agent runtime (LangChain Deep Agents)
frontend/        Web UI (React + Vite)
deploy/          docker compose for the local stack
private/         Gitignored. Material that must not be published.
```

## Quick start (once M1 lands)

```bash
cp .env.example .env                 # add OPENAI_API_KEY, GitHub and Atlassian tokens
docker build -t anvil-sandbox-rtems:7 sandbox/rtems   # 1-2 h the first time (or docker pull the GHCR :7)
docker build -f sandbox/rtems/Dockerfile.leon3 -t anvil-sandbox-rtems:7.1 sandbox/rtems   # ~2 min: adds the leon3 BSP TC2 needs
docker compose -f deploy/compose.yaml up -d           # postgres + gateway + api + ui
open http://localhost:5173
```
