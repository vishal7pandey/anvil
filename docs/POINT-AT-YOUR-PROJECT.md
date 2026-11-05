# Point Anvil at your own project

Anvil does not need GitHub or a git host. A ticket (or the UI "Repository" field) points at a
**local source** — a git working copy, a plain folder, or an archive — in any of these `Code base:`
forms:

```
Code base: "C:\work\my-project"                       # git working copy, checked-out branch
Code base: branch develop of "C:\work\my-project"      # a specific branch
Code base: "C:\work\legacy-code"                       # a plain folder, no .git
Code base: "C:\work\My Archive.zip"                    # .zip / .tar.gz / .tar / .tgz
```

Paths with spaces work when quoted. The source never needs a remote, a Jira account, or a token.

## What is copied — and what is not

- **Git working copy**: only the *committed* state of the chosen branch is staged — a single-branch
  snapshot. **Uncommitted changes are reported, not copied** (the run's export is commits only).
  Submodules are not followed.
- **Plain folder**: an ephemeral single-commit snapshot is made. `.gitignore` is respected and
  transient directories (`node_modules/`, `build/`, `.venv/`, `dist/`, VCS data) are excluded —
  the ANVIL-91 plain-folder rehearsal measured ~110 s to stage ~10k files.
- **Archive**: extracted into the workspace with path-traversal safeguards (entries that escape the
  root — `..`, absolute paths, a sibling-prefix — are refused).

The host folder is never modified.

## Where the result lands

The run's output is exported to the host, not pushed to any remote:

```
private/runs/<run_id>/<run_id>.patch    # a git-format patch
private/runs/<run_id>/<run_id>.bundle    # a git bundle (keeps history)
```

Apply it in a git working copy with `git am` / `git fetch`, or in a plain folder with `git apply`.

## On a project that is NOT RTEMS

The acceptance skills and the demo cases assume an RTEMS-ish C codebase, but the *mechanics* are
project-agnostic — a tiny plain-folder Python project staged, ran, and exported exactly like the
RTEMS one. The real boundary is **what the sandbox image (`anvil-sandbox-rtems:7.1`) has**:

| In the image | Not in the image |
|--------------|------------------|
| `python3` 3.12 | `node` / `npm` |
| `gcc` 13.3, `make`, `git` | `go`, `rustc` / `cargo` |
| RTEMS toolchain | `clang`, `waf` |

A Python or C project can build and test inside the sandbox as-is. A Node/Go/Rust project runs, but
its build/test steps inside the sandbox will fail for want of the toolchain — the agent can still
read, edit and patch the code; verification of such projects needs a different sandbox image.

## What stays offline

All of it, on a folder/archive source with a local JSON ticket (`ANVIL_TICKETS_DIR`) — intake,
the run, the approval gate, the local knowledge folder, and the patch/bundle export. Only the
GitHub/Jira outward path (`git_push`, `github_open_pr`, `jira_*`) needs credentials, and those tools
are simply not offered for a local source.
