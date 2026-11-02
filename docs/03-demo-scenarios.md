# 03 Demo scenarios

All four run on RTEMS from our mirrors (github.com/vishal7pandey/rtems*, upstream
gitlab.rtems.org). Each has a scripted path, a measurable result, and a place where a human
steps in.

## TC1 Legacy defect fix with the solution withheld

**Setup.** Pick a closed upstream defect with a merged fix. Create a branch `tc1/<issue>-base` in
our mirror at the fix commit's parent (fix withheld), create a Jira issue in `RTEMS` with the
upstream bug report text only (no MR link). Every candidate below was fixed Aug-Oct 2026, which
is most likely after the training cutoff of the models we use, which limits "the model
remembered the patch".

| Upstream issue | Fix MR / commit | Area | Why it is a good demo |
|---|---|---|---|
| #5776 pthread_mutex_init writes flags before validating the priority ceiling | !1493 `a62e60b52501` | POSIX API | **Primary.** Portable, testable on erc32, clear root cause, clear regression test |
| #5806 counter conversions overflow above a few seconds | !1526 `97cf8529cbd5` | Classic API | Arithmetic overflow, test via conversions |
| #5723 dosfs: full 8.3 name overflows the short name buffer | !1448 `f2ad71eebbbe` | Filesystem | Memory-safety angle (buffer overflow), ties to CodeQL |
| #5750 returned partition buffer asserts in chain append | !1468 `af2560312ba2` | Classic API | Backup |
| #5749 time of day beyond year 2400 breaks watchdogs | !1484 `338bab40ecf6` | Score | Backup |
| #5760 regulator leaks its period object | !1479 `80b1ba90a590` | libmisc | Resource-leak class |

**Flow.** Closed loop in [02-architecture.md](02-architecture.md#the-closed-loop-tc1-path).
**Measure.** Root-cause agreement with upstream fix (human judge, 0-2 scale), tests added and
passing on SIS, CI rounds, wall time, USD, human touches. Run the primary three times to show
consistency.
**Human step.** PR review and merge.

## TC2 Open-source component upgrade

**Target.** `rtems-lwip`: upgrade the vendored lwIP to the latest stable upstream release.
(Fallback if too large for the demo window: a smaller vendored component in rtems-libbsd or
rtems-net-services; decide in the TC2 spike.)
**Flow.** Agent diffs vendored vs. upstream, writes an upgrade plan, applies the upgrade, resolves
build breaks in the RTEMS port layer, builds against the erc32/leon3 BSP, runs the lwIP tests that
run on the simulator, and produces: changelog digest, API-impact notes, SBOM delta (Syft before
and after), CVE delta (OSV-Scanner), release note draft. PR + Confluence release page.
**Measure.** Build green, tests green, artifacts complete, human touches.

## TC3 CRA compliance review

**Target.** "An RTEMS-based networked device" as a reference product (RTEMS + libbsd or lwIP +
shell). No client product names.
**Flow.** `compliance` subagent with a `cra-review` skill (Annex I essential requirements as a
checklist, written by us from the regulation text; web search for current guidance). Inputs:
code, docs, SBOM, CodeQL/Sonar findings. Outputs in priority order: gap analysis, remediation
plan (each item becomes a Jira story), test evidence (for one or two gaps, an actual test run in
the sandbox), threat model (STRIDE over a data-flow of the reference device), compliance report,
SBOM (SPDX + CycloneDX).
**Human step.** A security engineer accepts/rejects each gap; rejections feed `/memories/`.

## TC4 Incident investigation from a knowledge base

**Analogue.** RTEMS has no alarm system, so the scenario is an *event/interrupt storm* class
problem (for example message-queue or interrupt flooding degrading timing), investigated from a
knowledge model.
**Knowledge model.** Ingest closed RTEMS GitLab issues, rtems-docs, and a Confluence KB space
with seeded engineering notes (meeting notes, runbooks we write, clearly marked synthetic) into
pgvector; the agent also searches code. "Self-learning": every investigation writes back a KB
page and memory entries, and the next run retrieves them.
**Flow.** Search KB -> ranked hypotheses with citations -> investigation workflow (Confluence
page with steps) -> reproduce in the simulator where possible -> Jira bug -> design proposal ->
test cases -> KB update.
**Human step.** An SME confirms the root cause before the bug is filed; we show where the
agent is weak (domain judgement) on purpose.

## Demo hygiene

* Seeded data is labelled synthetic in its title.
* Nothing from any client or evaluation document goes into public systems; the answer map stays in `private/`.
* Before each recorded run: fresh sandbox, pinned image digest, gateway budget reset.
