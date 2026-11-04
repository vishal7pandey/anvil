<!-- Confluence RTKB page 28868609, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Architecture Note: Payload Telemetry Flow and Queue Specifications

# Payload Telemetry Flow and Queue Specifications (synthetic)

## 1. Overview

The science payload instrument acquires sensor telemetry through an interrupt-driven sensor interface on the SPARC/LEON3 flight computer. Telemetry frames collected by the ISR are queued to a storage and downlink worker task using an RTEMS Classic API Message Queue (`rtems_message_queue_send`).

## 2. Queue Configuration & Sizing

* **Queue Identifier**: `TM_Q`
* **Queue Storage Capacity**: 16 messages (`RTEMS_MESSAGE_QUEUE_CREATE` parameter `count = 16`)
* **Message Payload Buffer**: 64 bytes fixed payload (`max_message_size = 64`)
* **Queue Discipline**: FIFO (`RTEMS_FIFO`)
* **Wait Discipline**: Non-blocking from ISR (`RTEMS_NO_WAIT`)

## 3. Sensor Acquisition & Consumer Rates

* **Nominal Acquisition Frequency**: 100 Hz (1 telemetry message packet arriving every 10.0 ms)
* **Peak Burst Acquisition Frequency**: 500 Hz (1 telemetry message packet arriving every 2.0 ms)
* **Burst Duration Window**: Up to 100 ms continuous burst under high-data-rate mode
* **Consumer Task Execution Time**: 1.5 ms average CPU drain latency per packet
* **Guidance Task Priority**: `TM_GUIDANCE` runs at priority 10 with a Rate Monotonic period of 50 ms. Guidance execution jitter under peak computation can consume up to 35 ms of CPU budget per cycle.
