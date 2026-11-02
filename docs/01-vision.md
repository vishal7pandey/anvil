# 01 Vision and scope

## The one-sentence goal

Show, live and on a real embedded codebase, an AI factory that takes a ticket, branches the repo,
changes the code, runs the tests and pre-commit hooks, commits, pushes, opens a pull request, reacts
to CI, and merges when policy allows; and that knows when to stop and hand back.

## Why build our own instead of showing a vendor tool

A reviewer of an "AI code factory" asks questions a chat demo cannot answer: how many agents and
models, where the code runs, how work is validated, whether history is auditable and reversible,
how the agent detects being stuck, how gains are measured. Each of those maps to a component we
can point at (question-by-question map kept in `private/`, not published).

## Audience of the demo

Engineering leaders and senior embedded engineers evaluating AI-assisted sustaining
engineering. They know C, RTOS internals and DevSecOps tooling; they will notice a facade.

## In scope (the demo)

1. A web UI that feels like a coding-agent app: sessions, chat, live plan, tool calls, terminal
   output, diffs, approvals, environment picker, cost meter.
2. One agent harness (LangChain Deep Agents) with specialised subagents and skills, behind a
   model gateway (LiteLLM -> OpenAI).
3. A per-session Docker sandbox with the RTEMS SPARC toolchain and the SIS simulator: the agent
   really builds for the target and really runs tests.
4. Integrations that close the loop: Jira (intake, comments, transitions), Confluence (knowledge
   in, evidence out), GitHub (branches, PRs, Actions, CodeQL, Dependabot), SonarQube, web search.
5. Four scenarios on RTEMS: defect fix, component upgrade, CRA compliance review, incident
   investigation from a knowledge base. Details in [03-demo-scenarios.md](03-demo-scenarios.md).
6. Guardrails you can see: approvals before outward actions, budgets, stuck detection and
   handoff, credentials never inside the sandbox.
7. Measurement: per-run time, tokens, cost, human touches, CI iterations, and for the defect
   scenario, agreement with the withheld upstream fix.

## Out of scope (say no fast)

Multi-tenancy, SSO, Kubernetes, durable workflow engine, policy engine, self-hosted models,
fine-tuning, a plugin marketplace, a VS Code fork. All of these live in the long-form design
(`projects/software-factory-ideation-design`) and stay there. If a story needs one of them,
the story is wrong.

## Success criteria for the demo

| # | Criterion | How we check |
|---|---|---|
| S1 | A Jira ticket becomes a merged PR without a human typing code (branch, tests, pre-commit, commit, push, PR, CI, merge) | Recorded run, TC1 |
| S2 | The agent builds for the target and runs tests on the simulator | Sandbox logs in the run timeline |
| S3 | CI (build + tests + CodeQL + Sonar) runs on the agent's PR and the agent reacts to it | PR checks, follow-up commit |
| S4 | The TC1 root cause matches the withheld upstream fix | Side-by-side diff, judged by a human |
| S5 | A stuck run hands back cleanly: WIP branch, Jira note, state "Needs human" | Forced-failure demo |
| S6 | Every run is replayable: inputs, model aliases, image digest, skill versions, events | Run record in the UI |
| S7 | Nothing in the demo is a mock: every screen is backed by a real call | Walkthrough |

## Constraints

* Personal laptop (8 cores, ~7 GB for Docker), personal GitHub, Jira, Confluence, OpenAI key.
* Public repos and spaces: **no client names, client product names or evaluation documents** anywhere public.
  Scenarios are framed on RTEMS alone.
* Quick and dirty is fine; fake is not.
