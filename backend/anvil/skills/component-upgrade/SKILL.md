---
name: component-upgrade
description: Upgrade a vendored third-party component (e.g. lwIP in rtems-lwip) to a newer upstream release end to end - establish the baseline build and tests, diff the two upstream releases, re-import the sources, adapt build specs, port layer and configuration, build for the target, run tests on the SIS simulator, produce the upgrade report (API impact, configuration changes, SBOM/CVE delta when tools exist), pre-commit, commit, push, open a PR, react to CI. Use for any "upgrade X to version Y" ticket.
---

# Component upgrade (vendored source in an RTEMS repository)

## When to use
A ticket asks to move a vendored or submodule-tracked component to a newer upstream version.

## Ground rules
- Never claim a result you did not see in command output. Quote the output.
- Work from the two upstream releases, the ticket and this branch. Do not search the internet or
  other branches for someone else's version of this upgrade.
- Upstream code is imported, not edited. Local changes belong in the port layer, the build
  specs and the configuration header, each one explained in the report.
- Keep the diff reviewable: one commit for the mechanical import, one for each adaptation.
- Never weaken, skip or delete an existing test. Never touch `.github/`.

## Steps (keep them as your write_todos plan)

1. **Pin both versions.** Record the current version (submodule commit, version macros such as
   `LWIP_VERSION_*` in `init.h`) and the target tag. Submodules were fetched at checkout and the
   sandbox is offline now: `git -C <submodule> tag | grep <version>` must list the target. If it
   does not, `handoff`; do not improvise a source.
2. **Baseline first.** Build and run the tests on the *unchanged* branch, so later failures are
   attributable to the upgrade. Save the summary line; it is evidence.
3. **Diff the releases, not the trees.** In the submodule:
   ```sh
   git diff --stat <old-tag> <new-tag> -- src/
   git diff --name-status <old-tag> <new-tag> -- src/   # A/D/R: files added, removed, renamed
   git log --oneline <old-tag>..<new-tag> | wc -l
   ```
   Read the upstream changelog/release notes in the submodule (e.g. `CHANGELOG`, `UPGRADING`)
   for the range. Note API changes (function signatures, struct fields, callback types) and
   configuration changes (new options, changed defaults in the options/defaults header).
4. **Plan the import.** List: files the import manifest must gain or lose (new upstream files
   that existing imported files now depend on, removed or renamed ones), build-spec entries to
   match, port-layer code that uses changed APIs (`grep -rn <symbol> <port dir>`), and
   configuration overrides whose meaning or default changed.
5. **Advance the submodule and re-import.** `git -C <submodule> checkout <new-tag>`, then the
   repository's own import tool (it reads the manifest; check its `--help`). Commit this as the
   mechanical step, with the submodule bump and the manifest change.
6. **Adapt.** Build; fix one class of error at a time. For each fix decide: build spec, port
   layer, or configuration. Missing symbols at link time usually mean a new upstream source file
   is not in the manifest or the build spec. For every option whose default changed, decide
   explicitly whether the local configuration keeps the old behaviour or takes the new one, and
   write the reason down: this is what a reviewer will ask about.
7. **Verify.** Clean build with no new warnings in the port layer, then the same tests as the
   baseline. Quote both summaries side by side.
8. **Artefacts.** In the run's artefacts directory (`/workspace/.anvil/`):
   - `upgrade-report.md`: versions, release-diff stats, files added/removed, API impact, every
     configuration decision with its reason, test results before/after, risks.
   - SBOM and vulnerability delta: if `syft` / `osv-scanner` (or the repository's `sbom.sh`) are
     available, run them on the tree before and after and diff the component entries. If they
     are not available, say so in the report and list the component name, old and new version
     and source URL instead. Never fabricate an SBOM or a CVE list.
9. **Review.** Ask the `reviewer` subagent to review `git diff <base>` against the report.
10. **Pre-commit** (if `.pre-commit-config.yaml` exists) on the changed files outside the
    imported upstream tree. Imported upstream files keep upstream formatting.
11. **Commit, push, PR.** Commits on your `anvil/...` branch, `<JIRA-KEY>: <component>: ...`.
    `git_push`, then `github_open_pr` with the report as the body, then `github_checks`; if CI
    fails, read the log, fix, push (max 3 rounds), else `handoff`.
12. **Evidence.** `jira_comment` with versions, test results, PR link and CI status; publish the
    report with `publish_evidence`; label the ticket `anvil-in-review`.

## Repository notes: rtems-lwip (sparc/leon3)
- Submodules: `lwip-upstream` (upstream lwIP), `rtems_waf`. Import manifest: `file-import.json`;
  import tool: `./lwip-to-rtems.py`. Build specs: `defs/common/*.json`, `defs/bsps/<arch>/<bsp>.json`.
  Port layer: `rtemslwip/`. Configuration header: `rtemslwip/include/lwipopts.h`.
- Build and test (about 2-3 minutes clean; use `execute` timeout 1800):
  ```sh
  ./waf configure --prefix=/opt/rtems/7 --rtems-tools=/opt/rtems/7 --rtems-bsps=sparc/leon3
  ./waf -j"$(nproc)"
  rtems-test --rtems-bsp=leon3-sis build/sparc-rtems7-leon3/networking01.exe
  ```
  `--rtems-tools` is the prefix, not its `bin/`. Pass = `Passed: 1` and
  `*** END OF TEST NETWORKING 1 ***` in the output.

## When to stop and hand off
Call `handoff` when: the target version is not available offline; the same build error persists
after three different attempts; an API change needs a design decision in the port layer (not a
mechanical adaptation); a test fails in a way that needs hardware to diagnose; or CI is still red
after three rounds.
