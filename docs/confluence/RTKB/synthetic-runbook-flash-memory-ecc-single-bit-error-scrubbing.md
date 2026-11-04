<!-- Confluence RTKB page 28934165, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Runbook: Flash Memory ECC Single-Bit Error Scrubbing

# Runbook: Flash Memory ECC Single-Bit Error Scrubbing (synthetic)

## 1. Overview

In-flight radiation environments cause single-event upsets (SEUs) in NOR flash memory banks. The flight software runs a background low-priority scrubber task (priority 200).

## 2. Scrubbing Parameters

* **Scrub Interval**: 120 seconds per 64 KB sector.
* **Syndrome Register**: Read `LEON3_ECC_STATUS_REG` at base `0x80000400`.
* **Single-Bit Correction**: Hardware corrects data word in cache; scrubber re-writes corrected word to flash if threshold exceeds 3 errors per sector.
