# Decisions

Short records; one per row until a decision needs more room. Status: Proposed until the owner
says otherwise.

| ADR | Decision | Why | Rejected |
|---|---|---|---|
| 001 | **Harness: LangChain Deep Agents** (`deepagents` 0.7.x, LangGraph 1.x) | Model-agnostic (owner has an OpenAI key only); planning, subagents, skills, memory, human-in-the-loop interrupts and a pluggable sandbox backend are built in; Postgres checkpointer gives sessions, replay and rewind (auditability) for free; Open SWE is a reference build on it | Claude Agent SDK (needs an Anthropic key); GitHub Copilot SDK (ties model and billing to Copilot); OpenAI Agents SDK (thinner: we would write planning/memory/HITL ourselves); Codex CLI in the container (strong coder, but a black box we cannot instrument per step; keep as a possible second engine later) |
| 002 | **Model gateway: LiteLLM proxy** in docker, model aliases only in agent code | Provider swap, per-run spend log, budgets, fallbacks, one place to answer "how many models" | Direct OpenAI calls (no router, no spend log); Portkey/Kong (heavier) |
| 003 | **Sandbox: one Docker container per session**, custom `BaseSandbox` backend, image with RTEMS 7 SPARC toolchain + SIS | Docker is already on the laptop; the agent must really cross-compile and run tests; the same image in CI makes local == CI | E2B/Daytona/Modal (cloud, cost, upload of code); gVisor/Firecracker (not needed for a demo on our own code) |
| 004 | **Credentials never enter the sandbox**; outward actions are host tools behind approvals | The sandbox runs model-chosen commands; pushing from the host via patch export keeps tokens out of reach of prompt injection | Token in container env (simpler, unsafe, and a reviewer will ask) |
| 005 | **Mirror, not fork**, RTEMS repos into the owner's GitHub | Forks start with Actions and code scanning restricted and inherit upstream's PR target; mirrors behave like an owned codebase | Forks |
| 006 | **Two Jira projects, two Confluence spaces**: `ANVIL` (build the factory) and `RTEMS` (the product backlog the factory serves); spaces `ANVIL` (platform docs) and `RTKB` (engineering knowledge base for TC4) | Separates "work on the factory" from "work by the factory", which is what the demo audience sees | One project with labels (muddles the demo) |
| 007 | **Code search is agentic** (ripgrep/ctags in the sandbox); vector retrieval only for tickets/docs/notes | Matches how strong coding agents work on large C trees; a vector index of code adds build time and little accuracy | Embedding the whole kernel |
| 008 | **Black Duck replaced** by Syft (SBOM) + OSV-Scanner (vulns) + ScanCode (licences) for the demo | No usable free tier for a personal demo; the open tools produce the same artefact types (SPDX/CycloneDX, CVE list, licence report) | Black Duck trial (time-boxed, account friction) |
