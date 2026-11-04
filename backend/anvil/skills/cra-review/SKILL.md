---
name: cra-review
description: Run a Cyber Resilience Act (EU 2024/2847) Annex I compliance review of a networked RTEMS device - apply the authoritative annex1.yaml checklist, gather evidence from code/docs/SBOM/code-scanning findings, produce a gap analysis, remediation Jira stories, sandbox test evidence, a STRIDE threat model, a compliance report in RTKB and an SBOM, and route each gap through a human accept/reject decision. Use for compliance/regulatory review tasks.
---

# CRA compliance review (RTEMS networked device)

## When to use
A ticket asks for a CRA / security-compliance review of a product with digital elements.
The reference device "RefDev" is defined in `/workspace/.anvil/skills/cra-review/refdev.md`
(read it first - it holds the device definition and the data-flow diagram for the STRIDE step).

## Ground rules
- `/workspace/.anvil/skills/cra-review/annex1.yaml` is **authoritative**. Never edit `id`,
  `cite` or `requirement`; never invent requirements. Paraphrase, cite, then assess.
- Never claim a result you did not see. Cite evidence: file:line, doc URL, scan finding,
  command output.
- One Jira story per accepted gap, labels `cra`, `tc3`. **Never** add the `anvil` label.
- No client or product names. The device is a generic RTEMS-based networked device.
- If a web source disagrees with annex1.yaml, the YAML wins; note the discrepancy.

## Steps (keep them as your write_todos plan)
1. **Load the checklist.** Read `/workspace/.anvil/skills/cra-review/annex1.yaml`; one todo
   group per Part I/II section.
2. **Gather inputs.** SBOM: run Syft in the sandbox on the checkout. Code scanning:
   `github_code_scanning_alerts()` with no PR returns the open alerts on this run's branch.
   osv-scanner needs network: run it only when the run is `anvil-online`; otherwise say the CVE
   status comes from the SBOM's component versions and cite them. The checkout is the device's
   own firmware (rtems-lwip: lwIP stack, port layer, GRETH netif, networking test app); treat the
   RTEMS kernel and shell as a third-party component and assess them from the SBOM, rtems-docs
   (`kb_search`) and their version. `web_search` for CRA clarifications only.
3. **Assess each item.** Status `pass | gap | n/a | unknown` + evidence + one-paragraph note.
   `unknown` counts as a gap for triage.
4. **STRIDE the device** using the data-flow diagram in
   `/workspace/.anvil/skills/cra-review/refdev.md`; one page, save to RTKB.
5. **Test evidence.** Pick 1-2 high-severity gaps and verify them with a real sandbox run of
   code that exists: build the stack and `networking01` for leon3 and run it on SIS, inspect the
   shipped defaults in `rtemslwip/include/lwipopts.h`, run Syft/osv on the tree. The Telnet
   shell, telemetry and TFTP services in `refdev.md` are a definition, not code in this repo:
   never claim a scan or test of a service you did not build and run. Quote output verbatim.
6. **Gap review gate.** The gate is the approval on `jira_create`: in an interactive run (UI
   session, or a ticket labelled `anvil-interactive`) each remediation story waits for the
   security engineer, and a rejection comes back as the tool result with the reason. Append
   each rejection reason to `/memories/cra/lessons.md` and down-rank that class of finding next
   run. In an autonomous run there is no gate: still create the stories, add the label
   `cra-unreviewed`, and say in the report that no engineer reviewed the gaps.
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
