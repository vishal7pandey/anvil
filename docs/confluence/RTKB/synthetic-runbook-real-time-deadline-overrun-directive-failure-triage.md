<!-- Confluence RTKB page 28934145, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Runbook: Real-Time Deadline Overrun & Directive Failure Triage

# Runbook: Real-Time Deadline Overrun & Directive Failure Triage (synthetic)

## 1. Symptom Triage Matrix

| Observed Code / Symptom | Probable Subsystem | Triage Action | |---|---|---| | `RTEMS_TOO_MANY` (10) from ISR | Message Queue Manager | Check message queue capacity vs. burst arrival rate. ISRs cannot block (`RTEMS_NO_WAIT`); packets are dropped when queue count reaches limit. | | `RTEMS_TIMEOUT` (6) from Period | Rate Monotonic Manager | Task missed period deadline. Inspect CPU execution time, higher-priority ISR storm overhead, or priority inversion. | | `RTEMS_UNSATISFIED` (13) | Semaphore / Event Manager | Resource contention or non-blocking acquisition timeout. | | Memory leak / fragmentation | Heap / Workspace | Run `rtems_heap_get_information`. |

## 2. Diagnostic Steps for Concurrent Queue Drops & Overruns

1. **Differentiate ISR Saturation from Mutex Contention**:

* Check if `rtems_message_queue_send` returns `RTEMS_TOO_MANY`. If yes, the queue is physically overflowing due to burst arrivals exceeding capacity during consumer task preemption.
* If queue sends return `RTEMS_SUCCESSFUL` but consumer tasks stall, inspect mutex locks for priority inversion.

1. **Calculate Buffer Burst Window**:

* Multiply queue message depth by minimum burst arrival interval: $T_{capacity} = N_{queue} \times T_{burst\_interval}$.
* If preemption delay exceeds $T_{capacity}$, queue sizing must be expanded.
