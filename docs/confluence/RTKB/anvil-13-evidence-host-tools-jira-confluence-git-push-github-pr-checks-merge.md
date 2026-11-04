<!-- Confluence RTKB page 28672001, version 1, exported for offline use; parent 28541041 -->

# ANVIL-13 evidence: Host tools: Jira, Confluence, git_push, GitHub PR/checks/merge

# ANVIL-13 evidence: Host tools: Jira, Confluence, git_push, GitHub PR/checks/merge

| | | |---|---| | Jira | ANVIL-13 - https://vishal7pandey.atlassian.net/browse/ANVIL-13 | | Repo / branch | vishal7pandey/anvil @ devin/anvil-13-host-tools + devin/anvil-13-github-tools | | PR | https://github.com/vishal7pandey/anvil/pull/2 (+ /pull/4) | | CI | none  | | Run | anvil-13-manual | | Cost | 0 human touches |

## Root cause

The closed loop needed host-side outward tools; credentials must never enter the sandbox (ADR-004).

## Fix

anvil.tools: Jira REST v3, Confluence v2 (+v1 CQL search), GitHub REST, git_push via bundle to a blobless host clone, sonar_issues, markdown->ADF/storage, evidence templates.

## Tests

* 39 respx/local-repo tests pass; live smoke of Jira (created ANVIL-18), Confluence page v2, GitHub draft PR open/close on the rtems mirror.
* .github/ refusal verified with add-then-delete commits against a local remote.

## Risk

PR #4 still in review; green-checks merge path and git_push from a real DockerSandbox unverified live.

## What the agent learned

git rev-list --exclude matches branch names without refs/heads/; --exclude=<full-ref> silently excludes nothing and the safety check fails open. Always test the refusal, not just the accept.
