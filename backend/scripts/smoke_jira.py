"""Live Jira smoke test, run by the owner: `uv run python scripts/smoke_jira.py` from backend/.

Reads credentials from the repo .env. Creates one throwaway ANVIL task labelled `anvil-smoke`,
comments, relabels, closes it. Prints keys and URLs only, never credentials.
"""

from anvil.tools import jira

found = jira.jira_search("project = ANVIL ORDER BY created DESC", max_results=1)
print("search ok:", [i["key"] for i in found])
print("get ok:", jira.jira_get(found[0]["key"])["summary"])
new = jira.jira_create("ANVIL", "Task", "anvil smoke test (safe to delete)", "Created by `scripts/smoke_jira.py`.", ["anvil-smoke"])
print("create ok:", new["url"])
print("comment ok:", jira.jira_comment(new["key"], "[devin] smoke: **bold**, `code`, [link](https://github.com)")["url"])
print("labels ok:", jira.jira_set_labels(new["key"], add=["anvil-smoke-done"], remove=["anvil-smoke"]))
print("transition ok:", jira.jira_transition(new["key"], "Done"))
print("intake ok:", [i["key"] for i in jira.jira_intake_candidates()])
