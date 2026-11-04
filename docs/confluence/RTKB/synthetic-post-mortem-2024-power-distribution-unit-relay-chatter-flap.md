<!-- Confluence RTKB page 29032449, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Post-Mortem: 2024 Power Distribution Unit Relay Chatter Flap

# Post-Mortem: 2024 Power Distribution Unit Relay Chatter Flap (synthetic)

## 1. Incident Summary

During spacecraft bus solar array wing release, the Power Distribution Unit (PDU) latching relay 3 command line chattered, asserting intermittent power brownout warnings.

## 2. Root Cause

Inductive kickback from the deployment solenoid coupled into the unregulated relay command bus.

## 3. Corrective Action

Added flyback diode suppression and a 20 ms RC debounce filter in PDU FPGA firmware rev 2.1.
