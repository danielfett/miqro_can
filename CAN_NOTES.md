# B-CAN (CAN-B) reverse engineering notes — Citroën Jumper / Fiat Ducato / Peugeot Boxer

Notes on the low-speed body/comfort bus of the Sevel van platform (Jumper / Ducato / Boxer / RAM ProMaster).
Everything here was obtained by passive sniffing on a single vehicle. **Nothing is from official documentation.**

> **Note on the platform:** despite the Citroën/Peugeot badge, this van uses a Fiat/Magneti Marelli
> body computer architecture, *not* a PSA BSI. PSA "CAN Confort" documentation does **not** apply.
> The ID scheme is the same family as other Fiat B-CAN vehicles (Blue&Me era), but the individual
> IDs and payloads do not transfer.

## Confidence levels

Every entry is tagged:

| Tag | Meaning |
| --- | --- |
| ✅ | Running in production (decoded by [`canbus.py`](https://github.com/danielfett/miqro_can/blob/main/canbus.py) and cross-checked against reality over a long period) |
| 🟢 | Observed repeatedly and consistently, but not in productive use |
| 🟡 | Single observation / plausible interpretation — treat as a hypothesis |
| ❓ | ID seen on the bus, meaning unknown |
| ⚠️ | Known to have changed or to be inconsistent |

---

## Physical layer

| Property | Value |
| --- | --- |
| Bitrate | **50 kbit/s** ✅ |
| Frame format | Extended (29-bit) IDs, DLC mostly 8 (some 6) |
| Transceiver | Low-speed / fault-tolerant CAN (ISO 11898-3 class, e.g. TJA1054/TJA1055). A standard high-speed transceiver will **not** work. |

```bash
ip link set can0 up type can bitrate 50000
```

### Frame rates and DLCs (measured)

| Period | IDs |
| --- | --- |
| 50 ms | `04214001`, `04294001` |
| 100 ms | `03029000`, `03254000`, `04214006`, `04394000` (DLC 4), `04394100`, `0A014003` (DLC 4) |
| 250 ms | `02214000` (DLC 6), `06214000`, `0621401A`, `06254000`, `06254006`, `06314000` |
| 500 ms | `06264000`, `06284000`, `06314003`, `063D4000` |
| ~0.75–0.9 s | `0A354003`, `0A3D4003`, `0C054003` (event-ish) |
| 1 s | `02294000`, `06314020` (DLC 4), `08014000` (DLC 4), `0A194003`, `0C214003` (DLC 6), `0C394003`, `0E094000` (DLC 6), `0E094003`/`0E09401A`/`0E094020` (DLC 2) |

Total bus load is roughly 145 frames/s with ignition on.

Convenient tap points: the "converter"/upfitter connector (see the Ducato upfitter's manual) or the OBD-II connector. On the OBD-II port, CANH is the blue cable and CANL the white cable.

---

## ID structure (hypothesis 🟡)

All IDs follow the pattern `0x hhh 4 nn`:

```
0x 0621 40 1A
   ^^^^ ^^ ^^
   |    |  └─ node address (source or target)
   |    └──── always 0x40
   └───────── message class / priority
```

The last byte takes a small set of values (`00`, `01`, `03`, `06`, `0A`, `0B`, `18`, `1A`, `20`),
which looks like a node identifier. Working guesses:

- `00` — body computer / BSI equivalent
- `03` — instrument cluster
- others unidentified

This matches the ID format documented for other Fiat B-CAN vehicles, which supports the theory,
but the mapping of node numbers to modules has **not** been verified here.

---


## Full ID inventory

`*` = only present with ignition on.

| ID | Meaning | Conf. |
| --- | --- | --- |
| `0x02214000` | Exterior lights | 🟢 |
| `0x02294000` | Key fob buttons / lock events | ✅ |
| `0x03029000` | Steering angle, `data[0:1]`, centre `0x1E56`, ~0.1°/count | 🟢 / 🟡 |
| `0x03254000` | VIN, 3 frames | 🟢 |
| `0x04214001` * | Coolant temp, engine rpm, reverse gear, A/C | ✅ / 🟡 |
| `0x04214006` * | Speed, traction plus, two 43 200/km distance counters | ✅ / 🟢 |
| `0x04294001` * | Byte 5 bit `0x40` tracks fuel state (see `0x06214000`) | 🟡 |
| `0x04394000` | Speed (duplicate) + 10 m distance counter, DLC 4 | 🟢 |
| `0x04394100` | Pitch / roll; byte 3 = accelerator pedal | ✅ |
| `0x06214000` | Doors, handbrake, lights, ignition, lock | ✅ |
| `0x0621401A` * | Seatbelt | 🟢 |
| `0x06254000` | Service interval km | 🟢 |
| `0x06254006` * | All zero in every capture | ❓ |
| `0x06264000` | All zero in every capture | ❓ |
| `0x06284000` | Steering wheel buttons (per third-party doc) | 🟡 |
| `0x06314000` | Start/Stop disabled; rear lights? | 🟢 / 🟡 |
| `0x06314003` | Handbrake, seatbelts, headlights, ESC mode (duplicate/summary frame?) | 🟡 |
| `0x06314020` | Multiplexed static data, DLC 4 | 🟡 |
| `0x063D4000` | Outside temperature, battery voltage | ✅ |
| `0x08014000` | Cruise / limiter — status mapping disputed, DLC 4 | 🟡 |
| `0x0A014003` | Constant `FF 80 00 00`, DLC 4 | ❓ |
| `0x0A194003` | Byte 1 counts down, 1 per 2 km | 🟡 |
| `0x0A354003` | Trip meter (A or B): distance + time | 🟢 |
| `0x0A3D4003` | Trip meter (the other one): distance + time | 🟢 |
| `0x0C054003` | Trip computer: odometer, range, consumption | ✅ |
| `0x0C214003` | Clock / date, DLC 6 | ✅ |
| `0x0C394003` | Units, autoclose setting | 🟡 |
| `0x0C3D4000` * | Power-on counter | 🟡 |
| `0x0E094000` | Constant `00 0E 04 00 10 4B`, DLC 6 | ❓ |
| `0x0E094003` | Constant `00 0E`, DLC 2 | ❓ |
| `0x0E09401A` * | Constant `00 0E`, DLC 2 | ❓ |
| `0x0E094020` | Constant `00 0A`, DLC 2 | ❓ |
| `0x1E114000` * | Constant, once per ignition-on | 🟡 |
| `0x1E114003` * | Constant, once per ignition-on | 🟡 |
| `0x1E11401A` * | Constant, once per ignition-on | 🟡 |
| `0x1E114020` * | Constant, once per ignition-on | 🟡 |
| `0x20000004` | Only seen transiently during unlocking | ❓ |
| `0x2000000C` | Only seen transiently during unlocking | ❓ |


---

## Decoded messages

Byte indices are zero-based (`data[0]` is the first payload byte).

### `0x06214000` — Doors, handbrake, lights, ignition ✅

| Byte | Bits | Meaning | Conf. |
| --- | --- | --- | --- |
| 0 | `0x20` | Handbrake engaged | ✅ |
| 0 | `0x08` | Driving lights on | ✅ |
| 1 | `0x04` | Front left door open | ✅ |
| 1 | `0x08` | Front right door open | ✅ |
| 1 | `0x30` | Sliding door open (either bit set) | ✅ |
| 1 | `0x40` | Rear door open | ✅ |
| 2 | — | `0x18` with ignition off, `0x48`/`0x40` with ignition on | 🟡 |
| 3 | `0x20` | Vehicle locked (**disputed** — see below) | 🟡 |
| 4 | `0x02` | **Low fuel warning lamp** | ✅ |
| 5 | byte | **Fuel level in litres** | ✅ |
| 6 | — | `0x0F` ignition off, `0x0B` ignition on | 🟡 |

Observed frames:

```
00 00 18 00 00 00 0F 00   all closed, ignition off
00 04 18 00 00 00 0F 00   front left open
00 08 18 00 00 00 0F 00   front right open
00 40 18 00 00 00 0F 00   rear open
00 70 18 00 00 00 0F 00   sliding + rear open
20 30 48 00 02 0C 0B 00   sliding open, handbrake on, ignition on
00 00 18 20 00 00 0F 00   locked via driver's door lock cylinder
00 00 40 20 02 0C 0B 00   driving, autoclose locked, 12 L, low fuel lamp on
00 00 40 20 00 5E 0B 00   driving, autoclose locked, 94 L, lamp off
```

A second, independent fuel indication lives in **`0x04294001` byte 5**, which read `0x10` on the
low tank and `0x50` on the full one. Since the two values differ only in bit `0x40` and the rest of
the frame is static, this is a flag (fuel above reserve, or the low-fuel lamp again) rather than a
gauge position — the earlier "gauge" reading of it was wrong. 🟡

**Fuel level** (byte 5) is the raw sender value in litres, verified by a fill-up test:
12 L when the low fuel lamp was lit, 94 L immediately after brimming a 90 L tank. It overreads
slightly at full — an independent report on another vehicle saw 91 L on a 90 L tank and 11 L near
empty, i.e. the same behaviour. There is no separate percentage or gauge-position field in this
frame.

⚠️ **Byte 3 is unresolved.** It was derived from locking with the driver's door key cylinder, and
it is indeed `0x20` throughout normal driving. Autoclose would explain that neatly — but the owner
confirms autoclose is **disabled**, and `0x0C394003` bit `0x10` agrees. So either the van locks on
drive-away regardless of the menu setting, or byte 3 means something else that also happens to be
set when locking with the key. Confidence dropped back to 🟡 pending a capture with the vehicle
stationary, ignition on, and demonstrably unlocked.

Note the third-party ProMaster documentation (see below) decodes byte 0 of this ID as key position
and engine running state. That interpretation has **not** been reproduced here — the low fuel level
flag also reportedly lives in this frame but was not located.

### `0x04214006` — Speed and traction control ✅

| Field | Encoding | Conf. |
| --- | --- | --- |
| Traction Plus active | `data[2] & 0x40` | ✅ |
| Speed (km/h) | `((data[2] & 0x0F) << 8 \| data[3]) / 16` | ✅ |

The upper nibble of byte 2 carries additional flags beyond the traction bit; these are unidentified.
This frame is only sent with ignition on, which makes it a reliable ignition-state proxy
(no frame for >3 s ⇒ ignition off). ✅

**Bytes 4–7 are two independent distance accumulators**, each a 13-bit counter wrapping at `0x2000`:

| Field | Encoding | Conf. |
| --- | --- | --- |
| Distance counter 1 | `(data[4] << 8 \| data[5]) mod 0x2000` | 🟢 |
| Distance counter 2 | `(data[6] << 8 \| data[7]) mod 0x2000` | 🟢 |

Both increment at **43 200 counts/km** (≈ 2.31 cm per count, i.e. 12 counts per second per km/h).
This is by far the highest-resolution odometry on the bus and is well suited to dead reckoning.

**They are the left and right wheels of one axle.** 

Practical consequence: the difference between the two counters is a direct measurement of path
curvature, independent of the steering sensor. Useful for dead reckoning, and as a cross-check on
the steering signal.

These are not the Trip A / Trip B odometers — those are `0x0A354003` and `0x0A3D4003`, at 100 m
resolution. The 13-bit wrap every 190 m alone makes these unusable as a trip display.

### `0x04394000` — Speed (duplicate) and coarse distance counter 🟢

DLC 4. Also carries a speed and odometry signal:

| Field | Encoding | Conf. |
| --- | --- | --- |
| Speed / traction | `data[0:1]` — byte-for-byte identical to `0x04214006` bytes 2–3 | 🟢 |
| — | `data[2]` — always `0x00` in all captures | — |
| Distance counter | `data[3]`, counts up, wraps at 256 | 🟢 |

The byte 3 counter runs at **≈ 100 counts/km, i.e. one count per 10 m** (measured 102.0 counts/km
at 70 km/h and 101.3 counts/km at 103 km/h; the ~1.6 % excess is consistent with the CAN speed
signal being the optimistic displayed speed rather than true ground speed). It wraps every ~2.5 km.

### `0x02294000` — Key fob buttons and central locking state ✅

Each button occupies one nibble; the nibble encodes the *event*, not a level:

| Nibble value | Event |
| --- | --- |
| `8` | Press |
| `2` | Held down |
| `1` | Release after hold |
| `4` | Double click |
| `0` | Nothing |

| Button | Location | Conf. |
| --- | --- | --- |
| Lock all | `data[5]` high nibble | ✅ |
| Unlock front | `data[5]` low nibble | ✅ |
| Unlock rear | `data[6]` high nibble | ✅ |

```
00 00 00 00 00 80 00 00   lock all pressed
00 00 00 00 00 08 00 00   unlock front pressed
00 00 00 00 00 00 80 00   unlock rear pressed
```

There is **no periodic lock-state broadcast anywhere on this bus** — a full dump comparison across
locked/unlocked states showed no other frame changing. Lock state therefore has to be tracked by
accumulating these events, which means it is unknown after a restart of the listener. 🟢

### `0x063D4000` — Outside temperature and battery voltage ✅

| Field | Encoding | Conf. |
| --- | --- | --- |
| Outside temperature (°C) | `(data[0] - 80) / 2` | ✅ |
| Starter battery voltage (V) | `data[1] / 6` | ✅ |

The voltage divisor of 6 was determined empirically and matches a multimeter well enough for
monitoring, but is not necessarily the exact factory scaling. Observed `0x57` → 14.5 V, correct for
a running engine.

**Byte 5** reads like a second temperature on the same scale: `0xC1` → 56.5 °C and `0xBD`/`0xBE` →
54.5/55.0 °C in two summer captures (outside air 28.5 °C). However, in two November captures
(outside air 9.5 °C, cold start, engine warming from 15 to 86 °C) it sat at `0xC0`/`0xC1` → 56/56.5 °C
throughout. That rules out intake/underhood air and any engine temperature; meaning unknown. ❓
Byte 3 was a constant `0x50`.

### `0x0C054003` — Trip computer ✅

| Field | Encoding | Conf. |
| --- | --- | --- |
| Current consumption (l/100 km) | BCD: integer part `data[0]`, first decimal `data[1] >> 4` | 🟢 |
| Total odometer (km) | `(data[1] & 0x0F) << 16 \| data[2] << 8 \| data[3]` | ✅ |
| Range remaining (km) | `(data[4] & 0x0F) << 8 \| data[5]` | ✅ |
| Stationary flag | `data[4] & 0x80` | 🟢 |

The consumption field is read as *hex digits interpreted as decimal* (BCD), i.e. byte `0x12` means 12.

Odometer and range were re-verified in the same fill-up test: the odometer advanced 93191 → 93227 km
over a 36 km round trip, and range remaining went 65 → 840 km across the refuel.

Note that **fuel level is not in this frame** — it is byte 5 of `0x06214000`.

**Consumption floor and stationary flag** (two captures, 2026-11-15): the consumption field never
goes below `0x20 0x0_` = 2.0 l/100 km. It sits at 2.0 during overrun fuel cut-off (injection value
in `0x04214001` bytes 4–5 exactly 0) and while rolling at idle at 20–45 km/h, and rises again below
~15 km/h. Bit `0x80` of byte 4 is set when speed drops below ~3.4 km/h and cleared above ~5 km/h;
while it is set, the consumption field is frozen at its last driving value (14.9, 7.3, 8.4, 4.7 at
the four stops observed — engine idling throughout, Start/Stop never intervened). The flag stays
set after ignition off. Per the owner, the cluster displays 2.0 at standstill with ignition on and
dashes with ignition off, i.e. it ignores the frozen value. 🟢

### `0x04394100` — Pitch and roll ✅

Two 12-bit values with a fixed offset of 2048, scaled by 0.1°:

```
pitch = ((data[0] << 4 | data[1] >> 4) - 2048) / 10
roll  = (((data[1] & 0x0F) << 8 | data[2]) - 2048) / 10
```

Frames where bytes 0 and 1 are both zero are invalid and must be discarded. Useful for levelling a
camper.

**Sign convention** ✅ (two captures, 2026-11-15, plus a comparison against an independent IMU):

| Signal | Positive when | Evidence |
| --- | --- | --- |
| Pitch | **front up** | Rises when accelerating: slope 0.87–0.88 against `atan(a_long / g)` from the speed signal, r = 0.83 / 0.95 |
| Roll | **right side down** | Falls in right-hand curves: slope −0.75 against `atan(a_lat / g)` from the wheel distance counters in `0x04214006`, r = −0.76 / −0.88 |

Turn direction was taken from the wheel distance counters: counter 1 (bytes 4–5) is the **left**
wheel, as confirmed by the indicator flags in `0x02214000` (right indicator ⇒ counter 1 travels
further). A right turn produces a rightward specific force, which an accelerometer reads like
"right side up", hence the negative slope for a right-down-positive convention.

The values behave like a plain accelerometer inclinometer: while driving they include longitudinal
and lateral acceleration (excursions up to about ±14° observed), and at standstill with the engine
running they jitter by roughly ±0.8°. Use them for levelling only at standstill and averaged.

A cabin-mounted IMU on the same vehicle (3 drives in July 2026, 1 s bins) tracks both values with
correlation +0.94…+0.98 at zero lag and a slope of about 1.09, i.e. same sign; the remaining
differences are constant mounting offsets of that IMU, not a decoding issue.

The remaining bytes:

| Byte | Observation | Conf. |
| --- | --- | --- |
| 3 | **Accelerator pedal position**, raw 0–255 (see below) | ✅ |
| 4 | Constant `0x04` | — |
| 5 | Small values, 1–13, noisy | ❓ |
| 6 | Alternates `0x01` / `0x81` — bit 7 is an alive/toggle flag | 🟢 |
| 7 | Constant `0x00` | — |


**Accelerator pedal (byte 3)** — evidence from two captures (cold start + continuation, 2026-11-15):

- Exactly `0` at standstill idle (both cold idle ~830 rpm and warm idle ~736 rpm), during overrun
  at well above idle rpm, and during gear changes. An engine torque/load signal would be non-zero
  at idle; a pedal signal is not.
- Correlates with vehicle acceleration (r = 0.55 / 0.68) more strongly than the load-like value in
  `0x04214001` bytes 4–5 (r = 0.39 / 0.52), and *leads* that value by ≥ 1 s — i.e. it is the
  input, not a consequence.
- Earlier: mean 57 at a steady 70 km/h, 160 at 103 km/h.
- **Full scale verified** with two full-throttle captures (2026-11-17): the pedal has two end
  stops. Flooring it gives a flat plateau at **253**; pushing past the detent (kickdown) steps to
  **255**, and back to 253 on the way out. Nothing above 255 or wrapping was seen. The earlier
  maximum of 184 was just a partial press.

`canbus.py` publishes it as `raw / 255 * 100` %, averaged over ~1 s.

### `0x06254000` — Service intervals ✅

| Field | Encoding | Conf. |
| --- | --- | --- |
| Km until oil change | `data[1] << 8 \| data[2]`, `0xFFFF` = not available | 🟢 |
| Km until *something else* | `((data[4] << 4 \| data[5] >> 4)) * 16` | 🟡 |

Observed constant in both driving captures: `80 A0 CC 00 FF F0 00 00`, giving 41 164 km to the oil
change and `0xFFF0` — the not-available marker — for the second field. Byte 0 is `0x80`, probably a
flag/validity byte.

The second value is a distance in km that counts down like a service interval, but which service it
refers to (inspection? timing belt?) is unknown.

### `0x08014000` — Cruise control / speed limiter 🟢

| Field | Encoding | Conf. |
| --- | --- | --- |
| Status | `data[0] & 0x80` ⇒ cruise control active; `data[0] & 0x40` ⇒ speed limiter active; else off | 🟡 |
| Button pressed | `data[0] & 0x08` | 🟢 |

### `0x04214001` — Engine data and reverse gear 🟢

The fastest frame on the bus (50 ms). Observed as `00 00 C0 86 xx xx xx 00` while driving.

| Byte | Encoding | Meaning | Conf. |
| --- | --- | --- | --- |
| 2 | `0xC0` in all captures | A/C state — see below | 🟡 |
| 3 | `data[3] - 40` | **Coolant temperature in °C** | ✅ |
| 4–5 | 16-bit, quantised | Engine load / injection quantity? | ❓ |
| 6 | `data[6] × 32` | **Engine speed in rpm** | 🟢 |
| 7 | `== 0x04` | Reverse gear engaged | 🟢 |

**Coolant temperature** read `0x86` = 134 → 94 °C throughout one log and alternated 93/94 °C in the
other, both on a fully warm engine at motorway speed. The −40 offset is the standard convention and
gives a very plausible figure. **Cold start confirmed** (2026-11-15, outside air 9.5 °C): the value
started at `0x37` → 15 °C and rose steadily to 35 °C over 4½ min of town driving; the follow-on
capture went from 58 °C to 86 °C. Idle speed tracked it as expected (elevated cold idle ~830 rpm,
dropping to ~736 rpm from about 30 °C), which also supports the 32 rpm scaling of byte 6.
One oddity: the first capture ended (ignition off) at 35 °C, the second began ~17 min and only
~1 km later at 58 °C — most likely heat soak at the sensor after shutdown, not yet checked
against the cluster gauge.

**Engine speed**: byte 6 averaged 52.95 at 70 km/h and 68.70 at 103 km/h. The ratio (1.30) does not
match the speed ratio (1.48), which is expected if the gear differed between the two runs. At a
scale of 32 rpm/LSB this gives 1695 rpm and 2199 rpm — plausible for a 6-speed van in 5th and 6th
respectively. The 32 rpm scale comes from third-party Fiat B-CAN notes and is **not** independently
confirmed here; a capture with a fixed gear and varying revs would settle both scale and offset.

**Bytes 4–5** change roughly once per second and every observed step is an exact multiple of `0x83`
(131), including the wraps. The value sat around `0x07AB`–`0x0CC8` at 70 km/h and around `0x2348`
at 103 km/h, and it moves both up and down, so it is a quantised measurement rather than a counter.
Load or injected fuel quantity is the obvious guess.

**A/C**: byte 2 was `0xC0` in both captures, taken with the A/C running. Third-party notes place
"A/C on" and "A/C disabled" flags in this frame, so bits `0x80` and `0x40` of byte 2 are the natural
candidates — but with no A/C-off capture this is untested.

Bytes 0 and 1 were `0x00` throughout.

### `0x0621401A` — Seatbelt 🟢

`data[2]` non-zero ⇒ driver's seatbelt unbuckled. Only present with ignition on.
Passenger seatbelt is reportedly also in this frame but the bit has not been isolated. 🟡

### `0x06314000` — Start/Stop 🟢

`data[6] == 0x04` ⇒ Start/Stop system disabled.

Bit 0 of byte 0 is a candidate for rear light state 🟡 — unconfirmed.

### `0x02214000` — Exterior lights 🟢

DLC 6. Observed with all lights off as baseline `00 00 00 00 00 00` (daytime running lights are not
represented).

| Byte | Value | Meaning |
| --- | --- | --- |
| 1 | `0x68` | Low beam on |
| 1 | `0x02` | Front fog lights (observed as `0x6A` together with low beam) |
| 1 | `0x10` | High beam / flash (momentary only) |
| 2 | `0x20` | Right indicator — set only while the bulb is actually lit |
| 2 | `0x40` | Left indicator — set only while the bulb is actually lit |
| 2 | `0x60` | Hazard lights — set only while lit |
| 2 | `0x08` | Mirror defroster |
| 5 | `0x08` | Right indicator (steady) |
| 5 | `0x04` | Left indicator (steady) |

The byte 2 flags blink with the bulb; the byte 5 flags appear to be the steady request. This
interpretation is 🟡. Rear fog lights are documented elsewhere as being in this frame but were not
located.

### `0x0C214003` — Clock and date ✅

BCD, one field per byte:

```
17 40 21 06 20 26   →   17:40 on 21.06.2026
hh mm DD MM CC YY
```

Verified independently: two captures decoded to 17:53 and 18:38 on 09.08.2026, matching the
wall-clock timestamps in the candump filenames exactly. Promoted to ✅.

### `0x0C394003` — Display / unit settings 🟡

Example: `01 00 00 10 0A 00 20 00`

| Byte | Bit | Meaning |
| --- | --- | --- |
| 0 | `0x01` | Consumption unit is l/100 km (otherwise 0) |
| 3 | `0x10` | Autoclose (automatic locking while driving) enabled |
| 4 | `0x0A` | Constant in all captures — unknown |
| 6 | `0x20` | Constant in all captures — unknown |

### `0x03254000` — VIN 🟢

VIN transmitted across three frames; `data[0]` is the frame index. Only transmitted with ignition on.

### `0x03029000` — Steering angle 🟢

Byte 0 is a constant `0x1E` and byte 2 a constant `0x80`; the varying part is byte 1, so treat the
signal as the 16-bit big-endian value `data[0:1]`. Bytes 3–7 are always zero.

**Straight ahead is `0x1E56` ± 2** 🟢.

**Scale is approximately 0.1° per count** 🟡.

### `0x0C3D4000` — Power-on counter 🟡

Sent once when the ignition is switched on. Byte 2 was observed incrementing by one between
ignition cycles (`0xB0` → `0xB1`), which matches the third-party description of this ID as a
power-up cycle counter.

### `0x1E11xxxx` — Constant "fingerprint" frames 🟡

`0x1E114000`, `0x1E114003`, `0x1E11401A`, `0x1E114020` are each sent once when the ignition is
switched on, always with identical contents. The third-party source reports the same behaviour and
suspects these are per-vehicle coding values emitted by each major module — consistent with the
PROXI alignment concept used on this platform. Contents were not analysed here.

---

### `0x0A354003` / `0x0A3D4003` — Trip meters A and B 🟢

These are the two dashboard trip odometers. Fields are plain binary but **not byte-aligned**, which
is why byte-wise reading makes them look like noise.

| Field | Bits (0 = LSB of the 8-byte frame) | Extraction | Meaning | Conf. |
| --- | --- | --- | --- | --- |
| Trip distance | 6–15 (at least) | `(data[6] << 8 \| data[7]) >> 6` | **100 m per count** | 🟢 |
| Trip time | 28–39 | `(data[3] << 4) \| (data[4] >> 4)` | **Minutes** | 🟡 |


### `0x06314020` — Multiplexed static data 🟡

DLC 4. Byte 0 cycles `00 → 08 → 10 → 18` once per second, i.e. bits `0x18` are a 2-bit multiplex
index. Byte 3 carries the payload and is identical in both captures:

| Mux | Byte 3 |
| --- | --- |
| 0 | `0xB4` (180) |
| 1 | `0xB4` (180) |
| 2 | `0x9C` (156) |
| 3 | `0xA0` (160) |

Bytes 1–2 are always zero. The values did not change between captures at all, so this is
configuration or calibration data rather than a measurement.

### Baseline frames (constant while driving)

Recorded with lights off, no doors open, handbrake off, belts fastened, A/C on, no buttons pressed.
Useful as a reference for spotting which bits move when a function is exercised.

```
02214000  00 00 00 00 00 00          all lights off
02294000  00 00 00 00 00 00 00 00    no fob activity
04294001  00 00 00 40 00 10 00 00    (byte 5 = 0x50 with a full tank — see below)
0621401A  00 01 00 00 00 00 00 00    belts fastened
06254000  80 A0 CC 00 FF F0 00 00    service data
06254006  00 00 00 00 00 00 00 00    always zero in every capture
06264000  00 00 00 00 00 00 00 00    always zero in every capture
06284000  00 00 00 00 00 00 00 00    no steering-wheel button pressed
06314000  00 00 00 00 00 00 00 00    Start/Stop not disabled
06314003  00 10 70 00 00 00 00 00    baseline
08014000  40 00 00 00                cruise/limiter idle
0A014003  FF 80 00 00                never observed to change
0A194003  4E 3D 00 00 00 00 00 00    byte 1 counts down — see below
0C394003  01 00 00 00 0A 00 20 00    settings
0E094000  00 0E 04 00 10 4B          static
0E094003  00 0E                      static
0E09401A  00 0E                      static
0E094020  00 0A                      static
```

`0x0A194003` byte 1 went `0x3D` → `0x2B` (61 → 43) across 36 km of driving, i.e. **one count per
2 km**, counting down. What it is counting down to is unknown. 🟡

---

## Not found / negative results

- **Windscreen wipers** — no frame found that correlates with wiper activity.
- **Periodic lock state** — see `0x02294000`; only events, no state broadcast.
- **Writing to the instrument cluster display** — `0x0A394021` is documented as the text display ID
  on other Fiat B-CAN vehicles. Injecting it here has no effect:
  ```
  cansend can0 0A394021#101ACF4D76DF8E7A
  cansend can0 0A394021#111A2CC000000000
  ```
  Either the ID differs on this platform, or a handshake/authentication frame is required first.

---

## Third-party sources

Two external sources were used for cross-referencing. **Neither is verified on this vehicle** and
they should be treated as leads, not facts.

1. A general Fiat B-CAN signal list (Blue&Me era vehicles). Contributes plausible-looking mappings
   for `0x02214000`, `0x04214001` (coolant temperature, engine speed in 32 rpm steps, A/C state),
   `0x06214000` (low fuel), `0x0621401A` (passenger seatbelt), `0x0C114003` / `0x0C194003`
   (operating hours, trip distance) and `0x063D4000` (ambient temperature).
2. A RAM ProMaster CAN documentation table. The ProMaster is the same vehicle sold in North America,
   so this is the closest thing to a reference that exists. Its `0x02294000` (key fob) and
   `0x06214000` (doors) entries match observations here, which lends it credibility. It additionally
   documents:
   - `0x06284000` — steering wheel buttons, all in byte 0: mute `0x20`, volume down `0x40`,
     volume up `0x80`, seek up `0x10`, seek down `0x08`, hang up `0x01`, phone `0x02`;
     voice is byte 1 `0x40`. Values return to zero within ~0.25 s.
   - `0x0421400B` — brake pressed and tow/haul mode. **This ID has never been observed here**, so it
     is probably ProMaster-specific.
   - `0x06314018` — reverse gear. Also not observed here; on this vehicle reverse is in
     `0x04214001` instead.

   Where the ProMaster document and observations on this vehicle disagree, the observations win.

3. A GitHub issue report from a vehicle using **11-bit (standard) CAN IDs** on a 50 kbit/s B-CAN,
   reporting odometer in bytes 1–3 of `0x603` and fuel level in litres in byte 5 of `0x380`.
   Both signals sit at *exactly* the byte offsets used here — odometer bytes 1–3 of `0x0C054003`,
   fuel level byte 5 of `0x06214000` — with the same encodings, so these are almost certainly the
   same logical messages under a different addressing scheme.

   No single shift or mask maps the 29-bit IDs used here onto those 11-bit IDs (the closest,
   a 17-bit right shift, yields `0x602`/`0x310` against their `0x603`/`0x380`), so don't try to
   derive one. **Findings from such reports should be transferred by byte position, not by ID.**

   No 11-bit frames have ever been observed on this vehicle. The capture chain here cannot filter
   by ID length — a gs_usb-class adapter passes all frames to the host and `candump` with no filter
   argument installs a match-everything filter — so their absence is real, not an artefact.

---

## Reference implementation

The decoders marked ✅ above are implemented and running in
[`canbus.py`](https://github.com/danielfett/miqro_can/blob/main/canbus.py), which publishes the
signals to MQTT with Home Assistant auto-discovery.

## Contributing

Corrections and additions welcome, especially for the unidentified IDs, the wipers, and anything
that makes the instrument cluster display usable. Please state how a finding was verified.
