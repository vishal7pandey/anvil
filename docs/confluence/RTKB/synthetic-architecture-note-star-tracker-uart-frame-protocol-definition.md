<!-- Confluence RTKB page 28999681, version 1, exported for offline use; parent 28541041 -->

# [synthetic] Architecture Note: Star Tracker UART Frame Protocol Definition

# Architecture Note: Star Tracker UART Frame Protocol Definition (synthetic)

## 1. Physical Layer

* Baud Rate: 115,200 bps
* Data bits: 8, Parity: None, Stop bits: 1 (8N1)
* Handshaking: RS-422 differential signaling

## 2. Frame Format

* Byte 0-1: Sync word `0xEB90`
* Byte 2: Message ID (`0x10` Attitude Quaternion)
* Byte 3: Payload Length ($L = 16$ bytes)
* Byte 4-19: Quaternion float32 array $(q_0, q_1, q_2, q_3)$
* Byte 20-21: CRC-16-CCITT checksum
