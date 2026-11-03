---
name: cra-review
description: Run a Cyber Resilience Act (EU 2024/2847) Annex I compliance review of a networked RTEMS device - apply the authoritative annex1.yaml checklist, gather evidence from code/docs/SBOM/code-scanning findings, produce a gap analysis, remediation Jira stories, sandbox test evidence, a STRIDE threat model, a compliance report in RTKB and an SBOM, and route each gap through a human accept/reject decision. Use for compliance/regulatory review tasks.
---

# CRA compliance review (RTEMS networked device)

## When to use
A ticket asks for a CRA / security-compliance review of a product with digital elements
(see `docs/scenarios/tc3.md` for the reference device "RefDev").

## Ground rules
- `backend/anvil/skills/cra-review/annex1.yaml` is **authoritative**. Never edit `id`, `cite`
  or `requirement`; never invent requirements. Paraphrase, cite, then assess.
- Never claim a result you did not see. Cite evidence: file:line, doc URL, scan finding,
  command output.
- One Jira story per accepted gap, labels `cra`, `tc3`. **Never** add the `anvil` label.
- No client or product names. The device is a generic RTEMS-based networked device.
- If a web source disagrees with annex1.yaml, the YAML wins; note the discrepancy.

## Steps (keep them as your write_todos plan)
1. **Load the checklist.** Read `annex1.yaml`; one todo group per Part I/II section.
2. **Gather inputs.** SBOM (run Syft in the sandbox or fetch the run's artefact),
   `github_code_scanning_alerts` and `sonar_issues` on the device repos, rtems-docs via
   `kb_search`, code via grep/read in the sandbox, `web_search` for CRA clarifications only.
3. **Assess each item.** Status `pass | gap | n/a | unknown` + evidence + one-paragraph note.
   `unknown` counts as a gap for triage.
4. **STRIDE the device** using the data-flow diagram in `docs/scenarios/tc3.md`; one page,
   save to RTKB.
5. **Test evidence.** Pick 1-2 high-severity gaps and verify them with a real sandbox run
   (listener scan, auth check, malformed-input test). Quote the command output verbatim.
6. **Gap review gate.** Present each `gap` to the security engineer for accept/reject.
   For rejections, append the reason to `/memories/cra/lessons.md` and down-rank that class
   of finding next run. Accepted-but-risk-accepted gaps still get stories marked
   `risk-accepted`.
7. **Remediation plan.** `jira_create` one Story per accepted gap (project RTEMS, labels
   `cra`, `tc3`); record each `story_key` back into the checklist item.
8. **Report.** Confluence page in RTKB `<KEY> evidence: CRA review of RefDev` via
   `confluence_write` / `publish_evidence`: full table, threat-model link, story keys, SBOM
   links, cost. `jira_comment` on the ticket and label `anvil-in-review`.

## Output artefacts (priority order)
Gap analysis -> remediation Jira stories -> sandbox test evidence (1-2 gaps) -> STRIDE page ->
compliance report (RTKB) -> SBOM (SPDX + CycloneDX).

## When to stop and hand off
Call `handoff` when: a checklist item cannot be assessed because the input artefact (SBOM,
scan, docs) doesn't exist and can't be produced in the sandbox; the security engineer rejects
the same finding class twice; or a remediation needs hardware you cannot run.
