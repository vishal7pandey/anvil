<!-- Confluence RTKB page 29065217, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Runbook: Spurious Watchdog Timer Reset Diagnostics

# Runbook: Spurious Watchdog Timer Reset Diagnostics (synthetic)

## 1. Scope

Applies to external hardware watchdog timer on LEON3 payload microcontroller.

## 2. Watchdog Pet Mechanism

* Watchdog interval: 1000 ms hardware timeout.
* Pet frequency: RTEMS timer server directive `rtems_timer_fire_after` kicks watchdog every 250 ms.
* Reset Cause Register: Read `LEON3_RST_REASON` at `0x80000004`. Bit 2 set indicates watchdog timer timeout.
