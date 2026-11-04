## Summary

Upgrade the vendored lwIP TCP/IP stack in rtems-lwip from 2.1.3 to the latest stable release 2.2.1 (tag `STABLE-2_2_1_RELEASE`).

Code base: branch `tc2/upgrade-base-leon3` of github.com/vishal7pandey/rtems-lwip (lwIP 2.1.3, with the LEON3 BSP definition and GRETH driver).

## Scope

1. Advance the `lwip-upstream` submodule to `STABLE-2_2_1_RELEASE`.
2. Synchronise the in-tree `lwip/` source tree and update `file-import.json` as required.
3. Resolve build incompatibilities and configuration changes (`lwipopts.h`) for RTEMS 7.
4. Build the stack for `sparc/leon3` with waf.
5. Run the networking test on the SIS simulator.
6. Provide an upgrade summary: SBOM delta, CVE status, API impact notes, release note.

## Acceptance criteria

* waf build for `sparc/leon3` succeeds.
* The networking test runs cleanly on SIS.
* Pre-commit checks pass.
* PR opened with release notes, SBOM delta and CVE delta attached; release page in Confluence space RTKB.
