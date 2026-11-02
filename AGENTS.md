# Anvil: rules for agents building this repo

Several coding agents build Anvil in parallel (Claude, Codex, Devin, GitHub Copilot, Cline,
OpenCode) under one GitHub account. These rules keep that from turning into a mess. Read
[docs/02-architecture.md](docs/02-architecture.md) before writing code; the contracts there are
how your work fits with everyone else's.

## Before you start

1. Your work has a Jira issue in project `ANVIL` (site vishal7pandey.atlassian.net). No issue,
   no work: ask the owner or Claude (PM) to create it.
2. Check the issue is assigned to you via label `agent-<name>` (`agent-claude`, `agent-codex`,
   `agent-devin`, `agent-copilot`, `agent-cline`, `agent-opencode`). Do not take another
   agent's issue.
3. Check your inbox `C:\Dev\comms\<name>\Inbox\` at the start of every session and between
   tasks. The protocol (sending, replying, archiving only when every action is done) is in
   `C:\Dev\comms\README.md`. Read it once per session.

## One laptop, one GitHub account, one Jira account

All six agents run on this laptop as the same GitHub and Atlassian user, so label yourself:
commit trailer `Agent: <name>`, PR body first line `Agent: <name>`, Jira comments start with
`[<name>]`. CPU, memory, Docker and network are shared: take `C:\Dev\comms\locks\rtems-build.lock`
before any RTEMS build, prefix your containers `<name>-`, never touch others' containers,
worktrees or processes. Details in `C:\Dev\comms\README.md`.

## Doing the work

* Branch: `<agent>/anvil-<n>-<slug>`, e.g. `codex/anvil-12-ghcr-publish`. Use a git worktree under
  `C:\Dev\wt\anvil-<agent>-<n>` if another agent may be working in the same clone.
* Commits and PR title start with the key: `ANVIL-12: Publish sandbox image to GHCR`.
* Stay inside your workstream's folders (`sandbox/`, `backend/`, `frontend/`, `gateway/`,
  `deploy/`, `.github/`). Changing a contract in 02-architecture needs Claude's OK first.
* Small PRs (< ~400 changed lines excluding lockfiles). Tests for logic you add. Run them:
  Python `uv run python -m pytest` (pytest.exe is blocked on this laptop), UI `npm test`.
* No secrets in code, logs, tests or PR text. Secrets come from `.env` (gitignored).
* Never write client names, client product names, or evaluation documents anywhere in this repo, Jira or
  Confluence. This project is "an AI factory demo on RTEMS".
* Quick and dirty is fine; fake is not. No mocked UI data in `main`, no stub that pretends to
  call a service.

## Finishing

* Open the PR, link the Jira issue, comment on the issue with the PR link, move it to In Review.
* Claude reviews every PR and comments `REVIEW (claude): APPROVE` or `CHANGES REQUESTED`.
  Claude's own PRs are reviewed by Codex. Merge after APPROVE and green CI; the owner can
  override anything.
* Say what you did not do or could not verify. A truthful "untested on Windows" beats a green
  checkmark that means nothing.

## Machine notes

* Docker Desktop (WSL2), 8 CPUs, ~7 GB RAM: do not run more than one RTEMS build at a time.
* Python via `uv`; Node 24.
