"""Seed 10 synthetic pages into Confluence space RTKB for TC4.

All pages are titled with the prefix '[synthetic]' per demo hygiene rules.
Devin's real page 'ANVIL-13 evidence' is left completely untouched.

Usage:
    cd backend
    uv run python scripts/seed_rtkb.py
"""

from __future__ import annotations

import time
from anvil.tools.confluence import confluence_write, confluence_search


PAGES: list[dict[str, str]] = [
    {
        "title": "[synthetic] Architecture Note: Payload Telemetry Flow and Queue Specifications",
        "content": """# Payload Telemetry Flow and Queue Specifications (synthetic)

## 1. Overview
The science payload instrument acquires sensor telemetry through an interrupt-driven sensor interface on the SPARC/LEON3 flight computer. Telemetry frames collected by the ISR are queued to a storage and downlink worker task using an RTEMS Classic API Message Queue (`rtems_message_queue_send`).

## 2. Queue Configuration & Sizing
- **Queue Identifier**: `TM_Q`
- **Queue Storage Capacity**: 16 messages (`RTEMS_MESSAGE_QUEUE_CREATE` parameter `count = 16`)
- **Message Payload Buffer**: 64 bytes fixed payload (`max_message_size = 64`)
- **Queue Discipline**: FIFO (`RTEMS_FIFO`)
- **Wait Discipline**: Non-blocking from ISR (`RTEMS_NO_WAIT`)

## 3. Sensor Acquisition & Consumer Rates
- **Nominal Acquisition Frequency**: 100 Hz (1 telemetry message packet arriving every 10.0 ms)
- **Peak Burst Acquisition Frequency**: 500 Hz (1 telemetry message packet arriving every 2.0 ms)
- **Burst Duration Window**: Up to 100 ms continuous burst under high-data-rate mode
- **Consumer Task Execution Time**: 1.5 ms average CPU drain latency per packet
- **Guidance Task Priority**: `TM_GUIDANCE` runs at priority 10 with a Rate Monotonic period of 50 ms. Guidance execution jitter under peak computation can consume up to 35 ms of CPU budget per cycle.
""",
    },
    {
        "title": "[synthetic] Post-Mortem: 2025 Payload Guidance Timing Overrun Incident",
        "content": """# Post-Mortem: 2025 Payload Guidance Timing Overrun Incident (synthetic)

## 1. Incident Summary
During thermal vacuum testing in 2025, flight telemetry observed packet loss accompanied by `RTEMS_TIMEOUT` error codes logged by the `TM_GUIDANCE` rate-monotonic task.

## 2. Root Cause Analysis
Investigation revealed **unbounded priority inversion** on a shared data logging mutex:
- Task `TM_GUIDANCE` (priority 10) shared an telemetry calibration table protected by a Classic API semaphore (`rtems_semaphore_create`) with a low-priority flash logger task (priority 50).
- The semaphore was initialized with `RTEMS_PRIORITY` without the `RTEMS_INHERIT_PRIORITY` attribute.
- An intermediate priority communications worker (priority 25) preempted the logger while it held the lock, causing guidance to miss its 50 ms rate monotonic deadline.

## 3. Key Diagnostic Findings
- **Directive Error Codes**: The rate-monotonic manager returned `RTEMS_TIMEOUT` (6).
- **Queue Health**: `rtems_message_queue_send` always returned `RTEMS_SUCCESSFUL` (0). The message queue was never saturated, and no `RTEMS_TOO_MANY` codes were recorded.
- **Resolution**: Enabled `RTEMS_INHERIT_PRIORITY` on the calibration table semaphore and verified priority inheritance on the LEON3 target.
""",
    },
    {
        "title": "[synthetic] Runbook: Real-Time Deadline Overrun & Directive Failure Triage",
        "content": """# Runbook: Real-Time Deadline Overrun & Directive Failure Triage (synthetic)

## 1. Symptom Triage Matrix

| Observed Code / Symptom | Probable Subsystem | Triage Action |
|---|---|---|
| `RTEMS_TOO_MANY` (10) from ISR | Message Queue Manager | Check message queue capacity vs. burst arrival rate. ISRs cannot block (`RTEMS_NO_WAIT`); packets are dropped when queue count reaches limit. |
| `RTEMS_TIMEOUT` (6) from Period | Rate Monotonic Manager | Task missed period deadline. Inspect CPU execution time, higher-priority ISR storm overhead, or priority inversion. |
| `RTEMS_UNSATISFIED` (13) | Semaphore / Event Manager | Resource contention or non-blocking acquisition timeout. |
| Memory leak / fragmentation | Heap / Workspace | Run `rtems_heap_get_information`. |

## 2. Diagnostic Steps for Concurrent Queue Drops & Overruns
1. **Differentiate ISR Saturation from Mutex Contention**:
   - Check if `rtems_message_queue_send` returns `RTEMS_TOO_MANY`. If yes, the queue is physically overflowing due to burst arrivals exceeding capacity during consumer task preemption.
   - If queue sends return `RTEMS_SUCCESSFUL` but consumer tasks stall, inspect mutex locks for priority inversion.
2. **Calculate Buffer Burst Window**:
   - Multiply queue message depth by minimum burst arrival interval: $T_{capacity} = N_{queue} \\times T_{burst\\_interval}$.
   - If preemption delay exceeds $T_{capacity}$, queue sizing must be expanded.
""",
    },
    {
        "title": "[synthetic] Runbook: Flash Memory ECC Single-Bit Error Scrubbing",
        "content": """# Runbook: Flash Memory ECC Single-Bit Error Scrubbing (synthetic)

## 1. Overview
In-flight radiation environments cause single-event upsets (SEUs) in NOR flash memory banks. The flight software runs a background low-priority scrubber task (priority 200).

## 2. Scrubbing Parameters
- **Scrub Interval**: 120 seconds per 64 KB sector.
- **Syndrome Register**: Read `LEON3_ECC_STATUS_REG` at base `0x80000400`.
- **Single-Bit Correction**: Hardware corrects data word in cache; scrubber re-writes corrected word to flash if threshold exceeds 3 errors per sector.
""",
    },
    {
        "title": "[synthetic] Meeting Notes: 2026-09-15 Instrument Thermal Vacuum Test Review",
        "content": """# Meeting Notes: 2026-09-15 Instrument Thermal Vacuum Test Review (synthetic)

## Attendees
Thermal Lead, Lead Systems Engineer, Flight Software Lead.

## Discussion Points
- Shroud temperature cycled between -40C and +65C across 4 thermal cycles.
- Thermocouple TC-04 on payload sensor interface registered peak temperature of +58C during telemetry transmission.
- X-band transponder output stable at 10W RF power.
- Recommendation: Proceed to acoustic vibration testing.
""",
    },
    {
        "title": "[synthetic] Architecture Note: Star Tracker UART Frame Protocol Definition",
        "content": """# Architecture Note: Star Tracker UART Frame Protocol Definition (synthetic)

## 1. Physical Layer
- Baud Rate: 115,200 bps
- Data bits: 8, Parity: None, Stop bits: 1 (8N1)
- Handshaking: RS-422 differential signaling

## 2. Frame Format
- Byte 0-1: Sync word `0xEB90`
- Byte 2: Message ID (`0x10` Attitude Quaternion)
- Byte 3: Payload Length ($L = 16$ bytes)
- Byte 4-19: Quaternion float32 array $(q_0, q_1, q_2, q_3)$
- Byte 20-21: CRC-16-CCITT checksum
""",
    },
    {
        "title": "[synthetic] Post-Mortem: 2024 Power Distribution Unit Relay Chatter Flap",
        "content": """# Post-Mortem: 2024 Power Distribution Unit Relay Chatter Flap (synthetic)

## 1. Incident Summary
During spacecraft bus solar array wing release, the Power Distribution Unit (PDU) latching relay 3 command line chattered, asserting intermittent power brownout warnings.

## 2. Root Cause
Inductive kickback from the deployment solenoid coupled into the unregulated relay command bus.

## 3. Corrective Action
Added flyback diode suppression and a 20 ms RC debounce filter in PDU FPGA firmware rev 2.1.
""",
    },
    {
        "title": "[synthetic] Runbook: Spurious Watchdog Timer Reset Diagnostics",
        "content": """# Runbook: Spurious Watchdog Timer Reset Diagnostics (synthetic)

## 1. Scope
Applies to external hardware watchdog timer on LEON3 payload microcontroller.

## 2. Watchdog Pet Mechanism
- Watchdog interval: 1000 ms hardware timeout.
- Pet frequency: RTEMS timer server directive `rtems_timer_fire_after` kicks watchdog every 250 ms.
- Reset Cause Register: Read `LEON3_RST_REASON` at `0x80000004`. Bit 2 set indicates watchdog timer timeout.
""",
    },
    {
        "title": "[synthetic] Meeting Notes: 2026-10-02 Downlink Transponder X-Band Calibration",
        "content": """# Meeting Notes: 2026-10-02 Downlink Transponder X-Band Calibration (synthetic)

## Attendees
RF Communications Engineer, Ground Operations Lead.

## Discussion Points
- Evaluated phase noise on traveling wave tube amplifier (TWTA).
- Carrier frequency offset tuned to +1.2 kHz at 8.4 GHz.
- Verified bit error rate (BER) < 1e-9 at 5 Mbps QPSK modulation.
""",
    },
    {
        "title": "[synthetic] Architecture Note: LEON3 Dual-Ported Memory Bank Partitioning",
        "content": """# Architecture Note: LEON3 Dual-Ported Memory Bank Partitioning (synthetic)

## 1. Memory Organization
The LEON3 on-chip dual-ported SRAM (128 KB) is partitioned into two independent 64 KB banks:
- **Bank A (`0x40000000 - 0x4000FFFF`)**: SpaceWire DMA packet buffers and ring descriptors.
- **Bank B (`0x40010000 - 0x4001FFFF`)**: Core CPU scratchpad memory and fast ISR vector jump tables.

## 2. AHB Bus Arbitration
AHB arbiter employs round-robin priority between CPU D-cache refill master and SpaceWire DMA controller.
""",
    },
]


def seed_rtkb() -> None:
    print(f"Starting seeding of {len(PAGES)} synthetic pages into Confluence space 'RTKB'...")
    created_pages = []

    for idx, page_data in enumerate(PAGES, start=1):
        title = page_data["title"]
        content = page_data["content"]
        print(f"[{idx}/{len(PAGES)}] Publishing: {title}")
        try:
            result = confluence_write(
                space_key="RTKB",
                title=title,
                markdown_or_storage=content,
            )
            print(f"    -> OK (id: {result['id']}, version: {result['version']})")
            created_pages.append(result)
            time.sleep(0.5)  # slight pause between API calls
        except Exception as e:
            print(f"    -> ERROR: {e}")
            raise

    print(f"\nSuccessfully seeded all {len(created_pages)} pages into RTKB!")


if __name__ == "__main__":
    seed_rtkb()
