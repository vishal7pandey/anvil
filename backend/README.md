# backend

One uv project, one Python package `anvil`, one subpackage per workstream so agents don't collide:

| Subpackage | Owner | What |
|---|---|---|
| `anvil.api` | copilot (WS-C) | FastAPI app, DB models, REST + SSE |
| `anvil.runtime` | claude (WS-B) | Deep Agents runtime, Docker sandbox backend, event adapter |
| `anvil.tools` | devin (WS-E) | Host tools: Jira, Confluence, git push, GitHub, Sonar, intake |
| `anvil.kb` | antigravity (WS-G) | Knowledge ingestion + `kb_search` |

Adding a dependency: `uv add <pkg>` in your branch; if `uv.lock` conflicts on rebase, take main's
lock and re-run `uv lock`. Tests: `uv run python -m pytest` (pytest.exe is blocked on this laptop).

## Control API

The API is created by `anvil.api.app:app`; it exposes OpenAPI at `/docs`. Configure the single
demo user's bearer credential with `ANVIL_DEMO_TOKEN` in `.env` or the process environment.
Database settings use `ANVIL_DATABASE_URL` when supplied, otherwise the local Postgres default
from `POSTGRES_PASSWORD`. Session repositories are canonicalized to GitHub's `owner/repository`
slug form for runtime checkouts.

From the repository root, apply the schema and run the API with:

```powershell
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn anvil.api.app:app --app-dir backend --port 8000
```

`create_app` accepts a `RunExecutor`; production startup must supply the runtime implementation.
Without one, session and run-read APIs remain available, while message submission and Jira intake
return an explicit `503` rather than pretending to execute work.

The executor contract includes `start`, `resume`, and `cancel`. Executor events include the run ID
and an advisory sequence; the API assigns the persisted sequence under the run lock and stamps
events itself. Run payloads include `last_event_seq` (and a `last_seq` alias) for runtime logging.
Runs default to `network=offline`; Jira intake permits `network=online` only when the issue has
the `anvil-online` label. The `run.record` event is retained as JSON and updates the run's
reproducibility fields.
