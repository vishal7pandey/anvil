<!-- Confluence RTKB page 28901377, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Post-Mortem: 2025 Payload Guidance Timing Overrun Incident

# Post-Mortem: 2025 Payload Guidance Timing Overrun Incident (synthetic)

## 1. Incident Summary

During thermal vacuum testing in 2025, flight telemetry observed packet loss accompanied by `RTEMS_TIMEOUT` error codes logged by the `TM_GUIDANCE` rate-monotonic task.

## 2. Root Cause Analysis

Investigation revealed **unbounded priority inversion** on a shared data logging mutex:

* Task `TM_GUIDANCE` (priority 10) shared an telemetry calibration table protected by a Classic API semaphore (`rtems_semaphore_create`) with a low-priority flash logger task (priority 50).
* The semaphore was initialized with `RTEMS_PRIORITY` without the `RTEMS_INHERIT_PRIORITY` attribute.
* An intermediate priority communications worker (priority 25) preempted the logger while it held the lock, causing guidance to miss its 50 ms rate monotonic deadline.

## 3. Key Diagnostic Findings

* **Directive Error Codes**: The rate-monotonic manager returned `RTEMS_TIMEOUT` (6).
* **Queue Health**: `rtems_message_queue_send` always returned `RTEMS_SUCCESSFUL` (0). The message queue was never saturated, and no `RTEMS_TOO_MANY` codes were recorded.
* **Resolution**: Enabled `RTEMS_INHERIT_PRIORITY` on the calibration table semaphore and verified priority inheritance on the LEON3 target.
