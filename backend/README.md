# backend

One uv project, one Python package `anvil`, one subpackage per workstream so agents don't collide:

| Subpackage | Owner | What |
|---|---|---|
| `anvil.api` | copilot (WS-C) | FastAPI app, DB models, REST + SSE |
| `anvil.runtime` | claude (WS-B) | Deep Agents runtime, Docker sandbox backend, event adapter |
| `anvil.tools` | devin (WS-E) | Host tools: Jira, Confluence, git push, GitHub, Sonar, intake |
| `anvil.kb` | opencode (WS-G) | Knowledge ingestion + `kb_search` |

Adding a dependency: `uv add <pkg>` in your branch; if `uv.lock` conflicts on rebase, take main's
lock and re-run `uv lock`. Tests: `uv run python -m pytest` (pytest.exe is blocked on this laptop).
