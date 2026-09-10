# Protocol notes

These are interoperability observations, not an official HyperX specification.
The app uses volatile RGB and mouse DPI reports on USB interface 02 of `03f0:02a1` and
`03f0:0ab5`. No firmware updates, key assignments, macros or onboard flash writes are implemented.

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


## Haste 2 Core Wireless DPI (v0.2.0)

Exact model `03f0:0ab5`, firmware `4102`. The model's NGENUITY Legacy class caps
DPI at 12,000 and lists polling rates 125/250/500/1,000 Hz. Shared command builder
uses 50-DPI steps, encoded as `DPI / 50 - 1`, little-endian 16-bit values.

- Query: `32 02`, padded to 64 bytes.
- Reply: `33 02 RR MM AA [five-byte stage records] ...`.
- Live write: `32 01 00 00 RR MM AA [five-byte stage records] ...`.
- `RR`: 64/32/16/8 for 125/250/500/1,000 Hz respectively.
- `MM`: enabled-stage mask, observed `0f` (four stages); `AA`: active stage, 0–3.
- Each stage: two-byte DPI code, then R/G/B stage-indicator color.

The write copies the returned table, changing only polling, active stage and the
four DPI values. Reserved/inactive entries and colors remain intact. In the
observed reply an inactive fifth record is present; it is preserved, not exposed
as a supported fifth stage. Profile bitmap zero is the vendor's live/AP preset.
No save-to-flash sequence is sent. Each write is followed by a query and exact
comparison of the requested settings. Unknown masks/rates/ranges fail closed.

On the test mouse, initial values were 400/800/1,600/3,200 DPI, stage 2 selected,
1,000 Hz. Changing the active stage to 900 DPI was acknowledged and read back as
900; restoring the original table yielded byte-for-byte identical readback.
Saved-stage reapplication was checked across a service restart; polling encoding
and readback mismatch handling are covered by automated tests. The mouse slept
during the separate polling-rate hardware test, so that change was not verified
on awake hardware during this release check. Hardware queries/writes run on the existing mouse worker, never a second
HID reader. Mouse sleep/timeouts do not block the keyboard worker. Physical DPI
button changes appear on the next query and are not immediately overwritten.
