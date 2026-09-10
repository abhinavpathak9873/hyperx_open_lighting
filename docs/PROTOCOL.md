# Protocol notes

These are interoperability observations, not an official HyperX specification.
The app uses only volatile RGB reports on USB interface 02 of `03f0:02a1` and
`03f0:0ab5`. No firmware updates, key assignments, macros, DPI changes, polling
changes or onboard flash writes are implemented.

Packet layouts were examined in the exact model classes and lighting command
builders of NGENUITY Legacy 2.38.0.0, then checked against responses from the
connected devices. The Windows app was inspected statically, never executed.
No vendor executable, firmware or decompiled source is distributed here.
The keyboard layout JSON records factual LED indices and physical positions.

| 64-byte output report, padded with zeros | Meaning |
| --- | --- |
| `10 01` | Device info query; reply `11 01` |
| `40 01 00 00 BB` | Volatile brightness, BB from 0 to 255 |
| `40 02` | Read brightness; reply `41 02 BB` |
| `44 01 NN 00` | Begin direct lighting frame, NN chunks |
| `44 02 II 00 [RGB triples]` | Chunk II, up to 20 LED slots |

ACK is `ff 01`, with report ID at byte 14, subreport at 15 and result at 16;
result zero is success. Some mouse read requests identify their response report
in the ACK. Device info is checked for exact VID, PID and LED count before RGB
writes. Keyboard: 103 LED slots, firmware 2110 observed. Mouse: 1 slot,
firmware 4102 observed. Numeric firmware fields are hex, not dotted versions.

Brightness readbacks observed:

| Requested | Keyboard raw byte | Mouse raw byte |
| --- | --- | --- |
| 25% | 61 | 63 |
| 70% | 175 | 178 |
| 100% | 255 | 255 |

The keyboard appears to quantize through integer percentages; the readback
check permits a three-level difference. RGB command ACKs do not independently
verify physical light output. Key-event notifications (`fb`) are discarded and
never logged. See [known issues](KNOWN_ISSUES.md) for setup/reconnect observations.

References:
- [HyperX NGENUITY](https://hyperx.com/pages/ngenuity)
- [OpenRGB development device list](https://openrgb.org/devices_pipeline.html)
- [SagaCtrl protocol notes](https://github.com/notwaterbtl/hyperx-saga-control/blob/main/docs/protocol-notes.md),
  independent evidence of the shared protocol on a different mouse.
