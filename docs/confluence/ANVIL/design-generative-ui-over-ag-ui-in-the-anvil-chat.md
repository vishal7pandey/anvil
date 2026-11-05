<!-- Confluence ANVIL page 30146561, version 1, exported for offline use; parent 28475566 -->

# Design: Generative UI over AG-UI in the Anvil chat

**Status:** proposed, waiting for owner approval · **Author:** claude (architect) · **Date:** 2026-11-04 · **Epic:** see Jira (ANVIL, label `genui`)

## 1. Why

Today the chat shows what the agent _did_: text, a folded list of tool calls, and an approval card. A reviewer gets the full value only by reading logs. Generative UI lets the agent _show_ things as purpose-built, interactive components: test results, a diff waiting for approval, the root-cause finding with its evidence, or a question with real choices. AG-UI gives us a standard event protocol for that instead of our private one, so the frontend, and any other AG-UI client, can render any Anvil run.

## 2. Where we start (main @ 9e68e01)

* **Backend:** `EventMapper` turns LangGraph `messages`/`updates` chunks into our own events: `run.status`, `message.delta`, `message`, `tool.call`, `tool.result`, `sandbox.exec`, `plan`, `diff`, `approval.request`, `subagent`, `usage`, `artifact`. They are stored per run (`seq`) and served via SSE `/api/runs/{id}/events` plus `/timeline`.
* **Frontend (React 19, no UI library):** `chatItems.ts` folds the events into items (user, assistant, activity, approval, subagent, artifact, status, error). Plan, diff and usage go to the right panel; approvals also go to `ApprovalsInbox`.
* **Human in the loop:** LangGraph `interrupt_on` gates (push, PR, merge) produce `approval.request`; approve/reject resumes the graph.

So we already have a typed event stream, a reducer and HITL. What's missing is a **standard protocol** and **agent-chosen components**.

## 3. AG-UI in one paragraph

AG-UI is an open, transport-agnostic event protocol between an agent backend and a user-facing app (typically SSE). The client posts `RunAgentInput` (threadId, runId, messages, **tools the frontend offers**, state, context) and receives typed events:

* lifecycle: `RUN_STARTED`, `RUN_FINISHED`, `RUN_ERROR`, `STEP_STARTED/FINISHED`;
* text: `TEXT_MESSAGE_START/CONTENT/END`;
* tools: `TOOL_CALL_START/ARGS/END/RESULT`;
* shared state: `STATE_SNAPSHOT` and `STATE_DELTA` (JSON Patch), plus `MESSAGES_SNAPSHOT`;
* escape hatches: `CUSTOM` and `RAW`.

Generative UI and HITL both ride on **tool calls**: the agent calls a tool, and the frontend renders it as a component, optionally returning the person's input as the tool result. _Phase 0 pins the exact spec version and event names; the interrupt/resume part of the spec is the piece that moves fastest._

## 4. Which kind of generative UI

| Level | What the agent sends | Use in Anvil |
|---|---|---|
| **Static** (recommended) | a tool call; the frontend owns a component per tool name | the core catalog below |
| **Declarative, narrow** (recommended for one tool) | JSON matching a small whitelisted schema (enum, string, bool, number fields) | `ask_user`: a form or choice card |
| Open-ended | HTML or JS written by the agent | **No.** Agent output can be prompt-injected (see ANVIL-64); we never render or run agent-authored markup or code |

## 5. Component catalog (first cut)

| Component | Source | Interactive? |
|---|---|---|
| **ApprovalCard** | gate interrupt (push, PR, merge) → frontend tool `request_approval` | Approve, reject with reason, see the exact args and diff. Closes the ANVIL-24 gap |
| **TestResultsCard** | `present_test_results` tool (or derived from `sandbox.exec` of the case's test recipe) | expand a failure's log tail |
| **DiffCard** | `diff` event / shared state | per-file collapse; "approve push" links to ApprovalCard |
| **PlanChecklist** | `STATE_DELTA` on `plan` | read-only, live |
| **RootCauseCard** | `present_finding(hypothesis, evidence[], confidence)` | evidence links (KB hits, files and lines) |
| **KBEvidenceCard** | `kb_search` results | source, date, and a time-guard badge |
| **CIStatusCard** | `github_checks` results | required vs advisory checks (ANVIL-90 `[ci] required_checks`) |
| **BudgetGauge** | shared state: tokens, time, cost against the budget | read-only (feeds the existing cost meter) |
| **AskUserCard** | `ask_user(question, schema)`, declarative | returns the answer as the tool result; the run pauses until then |

An unknown tool, or args that fail validation, falls back to today's ActionRow, so nothing disappears.

## 6. Architecture

```
LangGraph astream ──► EventMapper ──► run event store (unchanged; analyze/replay keep working)
                                         │
                       ┌─────────────────┴──────────────────┐
              /api/runs/{id}/events (today)       /api/agui/... (new)
                       │                          agui.translate(): pure, per-run stateful
              chatItems (today)                   ▼
                                       frontend AG-UI reducer
                                       ├─ messages / tool calls → component registry
                                       └─ RunState ← STATE_SNAPSHOT + JSON-Patch deltas → right panel

```

* **Translate at the edge.** `anvil/api/agui.py` maps stored events to AG-UI events. The event store and `anvil.analyze` stay the same, and both protocols run side by side until parity.
* **Mapping:**

  * `run.status` → `RUN_STARTED`, `RUN_FINISHED` or `RUN_ERROR`; handoff and waiting-for-approval become a `RUN_FINISHED` interrupt outcome, or `CUSTOM anvil.handoff` if the pinned spec lacks one;
  * `message.delta`/`message` → `TEXT_MESSAGE_*`;
  * `tool.call`/`tool.result` → `TOOL_CALL_*`, with `sandbox.exec` folded into the result;
  * `subagent` → `STEP_*`;
  * `plan`/`diff`/`usage` → `STATE_DELTA` on `RunState {plan, diff, usage, budget, tests, ci}`;
  * `approval.request` → a `request_approval` tool call;
  * `artifact` → a `show_artifact` tool call.
* **Endpoints:** `POST /api/agui/run` (RunAgentInput: starts a run or posts to a session) and `GET /api/agui/runs/{id}?after=<seq>` (reattach without duplicates, the same rule as ANVIL-73).
* **UI tools on the backend:** present-only tools (`present_test_results`, `present_finding`) return `"shown"` at once. Answer tools (`ask_user`, `request_approval`) use a LangGraph `interrupt`, and the frontend's answer resumes the run through the existing resume path.
* **Frontend:** `@ag-ui/core` types plus our own small reducer (no heavy framework, everything bundled, which keeps the offline demo working). A component registry `Record<toolName, {schema, Component}>` validates args before rendering. A `VITE_PROTOCOL=agui` flag lets both paths ship.

## 7. Security rules (non-negotiable)

* Components are our code; the agent supplies **data only**, validated against the component's schema (size-capped).
* No agent-authored HTML, script or iframe. Text renders as text; links render only for allowlisted hosts (ANVIL-64 list) or local artifacts.
* ApprovalCard always shows the raw tool args next to the friendly view, so a person approves what actually runs.
* An answer tool's result is untrusted input to the agent, like any user message.

## 8. Plan

| Phase | Work | Size | Proven by |
|---|---|---|---|
| 0 | Pin the AG-UI spec version and client package; record golden fixtures from the existing TC3 run fixture | 0.5 d | fixture + decision note |
| 1 | `agui.translate` + endpoints + reattach | 1 d | golden translation test over the fixture; reattach-after-seq test |
| 2 | Frontend AG-UI reducer behind the flag, at parity with chatItems | 1–1.5 d | the same fixture renders the same items on both paths |
| 3 | ApprovalCard (HITL), TestResultsCard, DiffCard, PlanChecklist via state | 1.5 d | component tests + one live approve/reject |
| 4 | AskUserCard (declarative), RootCauseCard, KBEvidenceCard, CIStatusCard, BudgetGauge | 1–1.5 d | component tests; the agent prompt names the present_* tools |
| 5 | Flag on by default; retire `/events` after a parity week | later | — |

**Timing, my recommendation:** nothing before Friday's offline demo (frozen). For the 11-10 RFP demo, phases 1–3 make a strong, visible slice: approval and test cards on a live TC1 run. Do it **only if** Friday's dry run is clean by Friday evening; otherwise it starts after 11-10. Phases 4–5 come after 11-10.

**Owners:** devin translator + endpoints (after ANVIL-60/59); cline frontend reducer + catalog, with antigravity reviewing the frontend; claude owns the contract and reviews the backend.

## 9. Open questions for the owner

1. Approve the static + narrow-declarative stance (no open-ended markup)?
2. Is the 11-10 slice (phases 1–3) wanted, given it competes with demo hardening?
3. Should AG-UI also be exposed for external clients (e.g. the customer's own front end) in the RFP pitch, or stay internal for now?
