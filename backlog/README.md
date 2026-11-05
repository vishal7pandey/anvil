# Backlog (offline copy of Jira)

Exported from Jira on 2026-11-05 by `backend/scripts/export_trackers.py`. This is now the backlog of
record if you don't have the Jira site; edit the JSON files directly, or keep your own tracker and
treat these as history. `STATUS.md` is the progress view (one table per epic: status, owner, merged
PR/SHA, last comment, handoff scope) — regenerate it from these files after any export.

## Refresh (needs Jira credentials)

```powershell
cd backend
uv run python scripts/export_trackers.py    # rewrites ANVIL/, RTEMS/, index.json, confluence/
```

## Add a ticket by hand (no Jira)

Create `ANVIL/ANVIL-<n>.json` (or a project folder like `RTEMS/`) in the format below — `key`,
`summary`, `status` are enough for STATUS.md/index; use the next free number. Then re-run the
export's index step or append to `index.json`.

* `ANVIL/ANVIL-<n>.json`: the engineering work (epics, stories, tasks).
* `RTEMS/RTEMS-<n>.json`: the demo tickets the **agent** receives as input. RTEMS-1 and RTEMS-7 are
  bug-replay cases (see `cases/`); RTEMS-2..6 are the TC2-TC4 scenarios. Their text is deliberately
  symptoms-only: don't add root causes or expected outcomes to them.
* `index.json`: one line per issue (key, type, status, priority, summary, labels, epic), for a quick look.

## File format (illustrative values)

```json
{
  "key": "ANVIL-41", "type": "Task", "status": "To Do", "priority": "High",
  "summary": "...", "labels": ["M2", "agent-claude"], "epic": null,
  "created": "...", "updated": "...", "resolved": null,
  "links": [{"relation": "blocks", "key": "ANVIL-32"}], "subtasks": [],
  "description": "Markdown",
  "comments": [{"created": "...", "body": "Markdown"}]
}
```

* **Labels:** `M0`-`M4` = milestone. `agent-<name>` = which AI agent owned it (claude, devin,
  antigravity, cline, codex, copilot); treat it as history, not as an assignment. `owner` = needs the
  project owner (credentials, accounts).
* **Comments** start with `[<agent>]`: all agents shared one Jira account, so that prefix is the author.
  The comments hold most of the decisions and measurements: read them before changing something.
* **Statuses:** `To Do`, `In Progress`, `Done`. The status is as of the export. A few items were
  finished in code just before the export without their status catching up (for example ANVIL-41:
  decided and merged in PR #51; it closes once the rehearsals confirm it).

## Open at export time

| Key | What | Notes |
|---|---|---|
| ANVIL-19, ANVIL-24 | Executor and API acceptance | Re-run RTEMS-1 through the Jira intake; check approve/reject/cancel in the UI |
| ANVIL-20 | Stuck detection and handoff | Handoff path proven (RTEMS-4 opened a draft PR); evidence to finish |
| ANVIL-21 | Skills | TC2 run 6 is next; if it hands off again, use the fallback track (draft PR + CI SBOM) |
| ANVIL-22 | TC1 eval harness | Consistency table done (3/3 root cause); needs 3 clean runs ending in a PR + cost per run |
| ANVIL-32 | E2E tests, runbook, reset, rehearsals | 3 recorded rehearsals were in progress |
| ANVIL-41 | TC1 time budget | Decided (keep 45 min) and merged; closes after the rehearsals |
| ANVIL-43 | Time-based KB leak guard | Spec in the file |
| ANVIL-44 | TC2 as a case spec | Lets rtems-lwip enter the KB without leaking the TC2 answer |
| ANVIL-45 | Mailing-list loader for the KB | Spec in the file |
| ANVIL-47 | Sandbox cleanup after runs | Stale containers pile up |
| ANVIL-48 | Owner-only actions | GHCR image push, token rotation, branch cleanup, Discord access |
| ANVIL-49 | Reset/preflight must protect case tickets (RTEMS-7) | Found while adding the second case |
