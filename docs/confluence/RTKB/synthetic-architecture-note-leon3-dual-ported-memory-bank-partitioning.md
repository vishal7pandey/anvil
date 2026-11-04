<!-- Confluence RTKB page 29097985, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Architecture Note: LEON3 Dual-Ported Memory Bank Partitioning

# Architecture Note: LEON3 Dual-Ported Memory Bank Partitioning (synthetic)

## 1. Memory Organization

The LEON3 on-chip dual-ported SRAM (128 KB) is partitioned into two independent 64 KB banks:

* **Bank A (`0x40000000 - 0x4000FFFF`)**: SpaceWire DMA packet buffers and ring descriptors.
* **Bank B (`0x40010000 - 0x4001FFFF`)**: Core CPU scratchpad memory and fast ISR vector jump tables.

## 2. AHB Bus Arbitration

AHB arbiter employs round-robin priority between CPU D-cache refill master and SpaceWire DMA controller.
