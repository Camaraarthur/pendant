# Honest Puck — Architecture Reference

Privacy-first wearable microphone pendant.
50 mm diameter × 15 mm thick, translucent silicone overmold.

---

## File Map

```
honest-puck/
├── ARCHITECTURE.md                  ← this file
├── netlist/
│   ├── __init__.py
│   └── honest_puck_v3.py            ← SKiDL netlist (all subsystems)
└── tests/
    ├── __init__.py
    └── test_honest_puck_v3.py       ← 91-test validation suite (SKiDL-free)
```

To generate the KiCad netlist:
```
pip install skidl
python3 netlist/honest_puck_v3.py    # → honest_puck_v3.net
```

To run tests (no KiCad or SKiDL required):
```
python3 -m pytest tests/ -v
```

---

## Subsystem Overview

```
USB-C ──► TP4056 ──► VBAT ──► TPS63031 ──► 3V3_SYS ──► ESP32-S3
(5V)       charger    LiPo    buck-boost              │
           300 mA    400mAh   3.0–4.2V→3.3V           ├──► W25N02KV (QSPI flash)
                                                       ├──► IM73D122 mic  (PMOS gated)
                                                       ├──► WS2812B ring  (PMOS gated)
                                                       └──► 3× tactile buttons
```

---

## Subsystem A — Power Management

**Parts:** USB-C receptacle, TP4056 charger, TPS63031 buck-boost, JST battery connector

### Charging
The TP4056 operates as a constant-current / constant-voltage LiPo charger.

| Parameter | Value | Formula |
|---|---|---|
| PROG resistor (`r_prog`) | 4 kΩ | — |
| Charge current | 300 mA | `I = 1200 / R_PROG` |
| C-rate (400 mAh cell) | 0.75 C | safe ceiling for silicone-enclosed cell |
| TP4056 standby drain | < 2 µA | when VBUS absent |

USB-C CC lines carry 5.1 kΩ pull-downs to GND on CC1 and CC2, signalling the
sink role to any USB-PD host.

### 3.3 V Regulation (FIX-001)
The AP2112K-3.3 LDO from v2 was replaced by the **TPS63031 buck-boost converter**.

Why the LDO failed:
- At `Vbat = 3.7 V` (nominal) the AP2112K has consumed its entire 400 mV dropout
  budget. Zero headroom remains for load transients.
- At `Vbat = 3.5 V` (50 % discharge) the output drops to `3.1 V` — the LDO is out
  of regulation. ESP32 Wi-Fi transmission becomes unstable.
- Linear regulation wastes `(Vin − 3.3) × I` as heat. At 400 mA from a full cell:
  `(4.2 − 3.3) × 0.4 = 0.36 W` deposited inside the silicone overmold.

TPS63031 advantages:
- Input range 1.8–5.5 V covers the full LiPo discharge curve (3.0–4.2 V)
- > 90 % conversion efficiency; < 36 mW waste heat at the same load
- 500 mA continuous / 800 mA peak handles Wi-Fi TX spikes
- PS/SYNC tied to GND enables power-save mode (< 55 µA quiescent)

Key external components:

| Ref | Value | Purpose |
|---|---|---|
| `l_bb` | 2.2 µH, Isat ≥ 1.5 A | switching inductor between L1 and L2 |
| `c_vin_bulk` | 22 µF | VIN bulk bypass |
| `c_vin_hf` | 100 nF | VIN HF bypass |
| `c_vout_bulk` | 22 µF | VOUT bulk bypass |
| `c_vout_hf` | 100 nF | VOUT HF bypass |
| `r_pgood` | 100 kΩ | PGOOD open-drain pull-up to 3V3_SYS |

---

## Subsystem B — ESP32-S3 Brain

**Part:** ESP32-S3-WROOM-1 (RF_Module:ESP32-S3-WROOM-1_SMD)

The EN pin is pulled up to 3V3_SYS through a 10 kΩ resistor. Without this,
EN floats through the module's internal weak pull-up, causing unreliable power-on
reset behaviour.

Module decoupling: 10 µF bulk + 100 nF HF on the 3V3_SYS supply pins.

### GPIO Assignment

| GPIO | Net | Function |
|---|---|---|
| IO0 | GPIO_BTN_MAIN | Tactile button (wake-capable RTC GPIO) |
| IO1 | GPIO_BTN_SYNC | Tactile button |
| IO2 | QSPI_IO2 | Flash WP# (QSPI data line 2) |
| IO3 | QSPI_IO3 | Flash HOLD# (QSPI data line 3) |
| IO4 | MIC_ENABLE_N | Mic/LED load switch gate (active-low) |
| IO5 | PDM_CLK | IM73D122 PDM clock (3.072 MHz optimal) |
| IO6 | PDM_DATA | IM73D122 PDM data |
| IO7 | LED_VDD_EN_N | WS2812B load switch gate (active-low) |
| IO8 | WS2812_DATA | WS2812B NRZ data (800 kbps) |
| IO9 | GPIO_BTN_BATT | Tactile button (moved from IO2 — see FIX-003) |
| IO10 | FLASH_CS | SPI / QSPI chip select |
| IO11 | SPI_MOSI | SPI MOSI / QSPI IO0 |
| IO12 | SPI_CLK | SPI / QSPI clock |
| IO13 | SPI_MISO | SPI MISO / QSPI IO1 |
| EN | — | Chip enable (10 kΩ pull-up to 3V3_SYS) |

---

## Subsystem C — W25N02KV Flash Memory (QSPI)

**Part:** Winbond W25N02KV 2 Gb SPI NAND (WSON-8)

### FIX-003 — Quad-SPI enabled
In v2, WP# and HOLD# were hardwired to VCC, permanently locking the device
into Standard SPI mode. They now route to ESP32 IO2 and IO3.

| Mode | Bus width | Bandwidth at 104 MHz |
|---|---|---|
| Standard SPI | 1-bit | 104 Mbps |
| Dual SPI | 2-bit | 208 Mbps |
| **Quad SPI** | **4-bit** | **416 Mbps** ← enabled by FIX-003 |

The ~4× throughput improvement compresses the duration of every Wi-Fi sync
event. A shorter active RF window means less heat deposited in the silicone
overmold per session, directly mitigating the thermal pooling risk.

**Software note:** Initialise WP# = HOLD# = HIGH in Standard SPI mode first,
then set W25N02KV status register SR3[1] = 1 to enter Quad mode.

### Net Definitions

| Net | Pin | Description |
|---|---|---|
| SPI_CLK | flash CLK, esp32 IO12 | Clock |
| SPI_MOSI | flash DI / IO0, esp32 IO11 | MOSI / QSPI IO0 |
| SPI_MISO | flash DO / IO1, esp32 IO13 | MISO / QSPI IO1 |
| FLASH_CS | flash CS, esp32 IO10 | Chip select |
| QSPI_IO2 | flash WP#, esp32 IO2 | QSPI data line 2 |
| QSPI_IO3 | flash HOLD#, esp32 IO3 | QSPI data line 3 |

---

## Subsystem D — Hardware Privacy Interlock (Honest Mic)

**Parts:** IM73D122 MEMS microphone, BSS84 PMOS, RED_TRUTH LED (red, 0603), 150 Ω resistor

### The Guarantee

The mic VDD and the red LED anode share a single copper net: `HONEST_MIC_PWR`.
This net is the drain of a BSS84 PMOS load switch whose source is 3V3_SYS.

Electrons cannot flow into the microphone die without simultaneously flowing
through the LED junction. There is no firmware vector to bypass this.

```
3V3_SYS ──[BSS84 S]──[D]──► HONEST_MIC_PWR ──┬──► IM73D122 VDD
                                               └──► LED_A ──[150Ω]──► GND
              ▲ gate
         MIC_ENABLE_N (IO4)
         [10kΩ pull-up to 3V3]
```

### Voltage and current analysis

| Quantity | Calculation | Result |
|---|---|---|
| LED current | `(3.3 − 2.0) / 150` | **8.67 mA** (high-visibility) |
| Mic current | IM73D122 typical active | ~1.0 mA |
| Total PMOS current | `8.67 + 1.0` | 9.67 mA |
| PMOS V_drop | `9.67 mA × 10 Ω (R_DS(on) max BSS84)` | 96.7 mV |
| V_mic actual | `3.3 − 0.097` | **3.203 V** |
| IM73D122 V_op_min | — | 1.62 V |
| Safety margin | `3.203 − 1.62` | **+1.583 V ✓** |

The 10 kΩ gate pull-up to 3V3_SYS clamps V_GS = 0 V during deep sleep
(IO4 → Hi-Z), guaranteeing the PMOS is OFF and both mic and LED are dark.

---

## Subsystem E — UI (Buttons + LED Ring)

### Tactile Buttons — Side-Mount (90° Right-Angle)

Three side-mount tactile switches placed on the PCB edge, actuated from
the pendant's perimeter. This allows button presses through the silicone
overmold without top-surface PCB area.

**Recommended part:** ALPS SKRTLAE010 (4.5 × 3.4 × 3.3 mm, 1.6 N force,
J-bend SMD right-angle). LCSC C110293, ~$0.06.

Alternative (slimmer): ALPS SKSCLBE010 (3.5 × 3.5 × 1.1 mm). LCSC C115361.

PCB layout note: The 50 mm circular outline needs small flat edges or notches
at each button position to provide solderable surface for the SMD pads.

Hardware pull-ups (10 kΩ to 3V3_SYS) remain essential — the ESP32-S3
internal pull-ups (~45 kΩ) are disabled during deep sleep, leaving wake
pins floating and vulnerable to EMI false-triggers.

| Button | GPIO | Net | Position |
|---|---|---|---|
| BTN_MAIN | IO0 | GPIO_BTN_MAIN | Side (RTC-capable wake) |
| BTN_SYNC | IO1 | GPIO_BTN_SYNC | Side |
| BTN_BATT | IO9 | GPIO_BTN_BATT | Side |

### LED Ring — 8 pixels, SK6805-EC15 or WS2812C-2020 (FIX-002 + FIX-004)

#### FIX-004 — WS2812B 5050 → smaller addressable RGB LEDs

The WS2812B PLCC-4 (5.0 × 5.0 mm) is oversized for a 50 mm pendant.
Replacing with smaller addressable RGB LEDs frees significant PCB area.

| Option | Package | Area vs WS2812B | VDD min | mA/ch | LCSC Stock | Price |
|--------|---------|-----------------|---------|-------|-----------|-------|
| **SK6805-EC15** | **1.5 × 1.5 mm** | **10× smaller** | 3.5–3.7 V | 3 mA | C2890035 (272k) | $0.05 |
| **WS2812C-2020-V1** | **2.0 × 2.0 mm** | **6× smaller** | 3.7 V | 5 mA | C2976072 (904k) | $0.04 |
| WS2812B-2020 | 2.0 × 2.0 mm | 6× smaller | 3.7 V | 12 mA | C965555 (546k) | $0.04 |

**Recommendation:** SK6805-EC15 (1.5 mm) for minimum footprint, or
WS2812C-2020-V1 (2.0 mm) for best stock availability and easiest assembly.

Both use the same NRZ single-wire protocol as WS2812B — **no firmware
change required**. The SK6805-EC15 3 mA variant draws less power (8 × 3 ×
3 mA = 72 mA full white vs 8 × 3 × 16 mA = 384 mA for WS2812B 5050).

**Note on the privacy indicator LED:** The separate red 0603 LED on
HONEST_MIC_PWR **must be kept**. It provides the hardware interlock
guarantee — if you replace it with an addressable RGB LED, firmware could
be modified to suppress the red indicator while the mic is on, breaking
the core privacy promise. The red 0603 LED (1.6 × 0.8 mm) is already
smaller than any addressable RGB option.

#### FIX-002 — Load switch (unchanged)

The PMOS load switch remains mandatory. Even the SK6805-EC15 draws
quiescent current from its internal oscillator.

| Scenario | Current | Standby life (400 mAh) |
|---|---|---|
| v2 (LEDs hardwired to 3V3) | ~4.87 mA total | **82 h (3.4 days)** |
| v3+ (PMOS load switch) | ~66 µA total | **~6 060 h (252 days)** |

```
3V3_SYS ──[BSS84 S]──[D]──► LED_SWITCHED_PWR ──► SK6805-EC15 VDD (× 8)
              ▲ gate
         LED_VDD_EN_N (IO7)
         [10kΩ pull-up to 3V3]
```

#### Voltage supply options

No addressable RGB LED is rated below 3.5 V. At 3.3 V:
- Red/green/amber: **work well** (LED Vf ~2.0–2.2 V, plenty of headroom)
- Blue/white: **dim or unreliable** (InGaN Vf ~3.0–3.2 V, insufficient headroom)

| Option | Approach | Trade-off |
|--------|----------|-----------|
| **A (simplest)** | Run at 3.3 V, use red/green/amber palette only | No extra parts. Blue unreliable. |
| **B (recommended)** | Feed LED_SWITCHED_PWR from VBAT (3.0–4.2 V) | Full colour above 3.7 V (~70% of battery life). Blue fades = natural low-battery indicator. |
| C (full colour) | Add TPS61023 boost to 5 V for LED rail only | Extra IC + inductor + caps. Full brightness all colours. |

#### Daisy-chain wiring (unchanged protocol)

```
IO8 ──► LED[0] DOUT ──► LED[1] DOUT ──► ... ──► LED[7]
         DIN              DIN
        (WS2812_DATA)    (WS2812_DOUT_0)  ...  (WS2812_DOUT_6)
```

---

## Deep Sleep Power Budget

All figures are worst-case with load switches OFF.

| Component | State | Current |
|---|---|---|
| ESP32-S3-WROOM-1 | Deep sleep (ULP + RTC active) | 8 µA |
| TPS63031 | Power-save mode (no load) | 55 µA |
| W25N02KV | Deep power-down | 1 µA |
| TP4056 | Standby (VBUS absent) | 2 µA |
| SK6805-EC15 × 8 | Load switch open | **0 µA** |
| **Total** | | **66 µA** |

`T_standby = 400 mAh / 0.066 mA ≈ 6 060 h ≈ 252 days` (zero-wake theoretical)

---

## Thermal Constraints

The silicone overmold has thermal conductivity k ≈ 0.2–0.3 W/m·K
(compare: thermally conductive potting compound 1.5–4.5 W/m·K).
Heat escapes almost exclusively through conduction to the ambient air at
the silicone surface.

### Worst-case active dissipation (syncing while charging)

| Source | Calculation | Dissipation |
|---|---|---|
| ESP32-S3 Wi-Fi TX | `355 mA × 3.3 V × (1 − η_rf)` | ~1.17 W |
| TPS63031 | `(1 − 0.90) × I × Vin` | ~0.05 W |
| TP4056 (charging) | `(5.0 − 3.7) × 0.3 A` | 0.39 W |
| **Total** | | **~1.61 W** |

At ~1.6 W inside the silicone shell, the internal temperature will rise
significantly. The LiPo cell must never exceed **60 °C** (electrolyte
degradation onset). If charging and syncing simultaneously, firmware should
throttle one or both operations.

**Primary mitigation:** FIX-003 (QSPI) reduces active Wi-Fi time ~4×,
cutting the energy deposited per sync cycle proportionally.

---

## Custom KiCad Library: HonestPuck_Lib

The following symbols must be created in a project-local `HonestPuck_Lib`:

| Symbol | Package | Notes |
|---|---|---|
| `ESP32-S3-WROOM-1` | RF_Module:ESP32-S3-WROOM-1_SMD | Use standard RF_Module footprint |
| `IM73D122` | Sensor_Audio:Infineon_IM73D122_BottomPort | Bottom-port MEMS; custom FP required |
| `W25N02KV` | Package_SON:WSON-8-1EP_... | Standard WSON-8; check pad 9 EP tie |
| `TP4056` | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm | Pin 1 = PROG |
| `TPS63031` | Package_SON:WSON-10_3x3mm_P0.5mm | Expose pad to GND |

All other parts (`Device:R`, `Device:C`, `Device:L`, `Device:LED`,
`Device:SW_Push`, `Device:Q_PMOS_GSD`, `Connector_USB:USB_C_Receptacle`,
`Connector_JST:JST_SH_1x02`, `Regulator_Linear:AP2112K-3.3`) are available
in the standard KiCad 7/8 libraries.

---

## Known Issues (v3 → v4 Candidates)

Issues identified via deep design review. Must be fixed before fab.

### ISSUE-001 — TPS63031 Missing Pin Connections (CRITICAL)

The v3 netlist does not connect the TPS63031's **FB**, **VINA**, or **PGND** pins.

- **FB** must connect directly to VOUT (3V3_SYS). This is how the internal
  error amplifier senses the output voltage. Without it, the converter will
  not regulate at all. This is the #1 documented TPS63031 mistake on TI's
  E2E forums.
- **VINA** is the internal analog supply rail. It requires a 100 nF ceramic
  bypass capacitor to GND (max 220 nF). VINA must NOT be shorted to VIN
  externally — there is an internal filter resistor.
- **PGND** (power ground) carries the high-current switching return path.
  If floating, the converter cannot operate.

**Fix for netlist:**
```python
buckboost["FB"]   += vcc_3v3          # Fixed 3.3V output: FB senses VOUT
buckboost["PGND"] += gnd              # Power ground
c_vina = Part("Device", "C", footprint=FP_C0402, value="100n")
c_vina[1] += buckboost["VINA"]; c_vina[2] += gnd
```

### ISSUE-002 — IM73D122 Missing Bypass Capacitor (HIGH)

The IM73D122 datasheet specifies a 100 nF capacitor between VDD and GND
for best performance. The HONEST_MIC_PWR rail has no local decoupling.
Switching noise from the TPS63031 (2.4 MHz) will couple into the mic
supply, degrading the 73 dB(A) SNR that makes this mic worth its cost.

**Fix:** Add 100 nF between HONEST_MIC_PWR and GND, placed as close to
the IM73D122 VDD pad as physically possible.

### ISSUE-003 — No NTC Thermistor for Battery Temperature (SAFETY)

The worst-case active dissipation is ~1.61 W inside a silicone shell with
k ≈ 0.2 W/m·K. The TP4056 has no built-in temperature sensing. Without an
NTC thermistor, firmware cannot implement the thermal throttling described
in the architecture. This is a safety certification blocker (IEC 62133,
UL 2054) for an encapsulated LiPo.

**Fix:** Add a 10 kΩ NTC thermistor (B=3950) bonded to the cell or on the
PCB near the battery connector. Connect as a voltage divider to an ESP32
ADC input. Firmware must:
- Pause charging (TP4056 CE low) when T > 45 °C
- Halt Wi-Fi TX when T > 50 °C
- Shutdown when T > 55 °C

### ISSUE-004 — USB-C Missing ESD Protection (MEDIUM)

No TVS diodes on CC1/CC2 lines. USB-C connectors see real-world ESD from
cable insertion. The 5.1 kΩ pull-downs provide no current limiting for an
ESD event (nanosecond timescale).

**Fix:** Add a dual-channel TVS diode (PRTR5V0U2X or similar) across
CC1/CC2 to GND, placed as close to the USB-C connector as possible.

### ISSUE-005 — ESP32-S3 Antenna Keep-Out on 50 mm Circular PCB (LAYOUT)

The ESP32-S3-WROOM-1 requires either antenna overhang past the PCB edge
(no FR4 underneath) or a 15 mm keep-out zone around the antenna. On a
50 mm diameter board, this is extremely tight after placing all components.

**Options:**
1. Position module at PCB edge with antenna overhanging the circle boundary
2. Switch to ESP32-S3-WROOM-1U (U.FL connector) with external flex antenna
3. Accept 3–6 dB RF penalty and plan for reduced range

---

## ECO History

| ID | Description |
|---|---|
| v1 | Initial architecture (ESP32-S3, IM73D122, W25N02KV, TP4056, AP2112K LDO) |
| v2 | Fixed SKiDL syntax; added EN pull-up; tied Flash WP#/HOLD# to VCC; hardware button pull-ups; PROG → 4 kΩ; LED resistor → 150 Ω |
| **v3** | **FIX-001** AP2112K → TPS63031 buck-boost; **FIX-002** WS2812B behind PMOS load switch; **FIX-003** QSPI (WP#/HOLD# → IO2/IO3); BTN_BATT moved IO2 → IO9 |
