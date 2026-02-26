"""
netlist/honest_puck_v3.py
Architecture Blueprint — Privacy-First Wearable Microphone Puck (v3)

Production Blocker Fixes vs v2
──────────────────────────────
FIX-001 │ POWER REGULATION
        │ Replaced AP2112K-3.3 LDO with TPS63031 buck-boost converter.
        │ LDO math: P_waste = (Vin − Vout) × I = (4.2 − 3.3) × 0.4 = 0.36 W
        │ dissipated as heat inside the silicone overmold. TPS63031 achieves
        │ >90 % efficiency, eliminating most of that thermal burden.
        │ More critically, the LDO drops out below Vbat ≈ 3.7 V at full load,
        │ triggering endless ESP32 brownout resets during Wi-Fi TX. The
        │ TPS63031 maintains a stable 3.3 V across the full LiPo discharge
        │ range (3.0 V → 4.2 V) with 500 mA continuous / 800 mA peak.

FIX-002 │ WS2812B PARASITIC DRAIN
        │ WS2812B internal oscillator draws 0.6–1.0 mA/pixel even at 0x000000.
        │ 8 pixels × 0.8 mA = 6.4 mA continuous. At 400 mAh:
        │   T = 400 mAh / 4.866 mA ≈ 82 h (3.4 days max standby — unusable).
        │ Fix: PMOS load switch (LED_SWITCH_Q / LED_VDD_EN_N net) completely
        │ severs VDD from the WS2812B ring during deep sleep.
        │ With FIX-002: total quiescent ≈ 66 µA → ~252 days theoretical standby.
        │
        │ NOTE: WS2812B VDD(min) = 3.5 V per datasheet. Running at 3.3 V_SYS
        │ leaves < 100 mV headroom for InGaN (blue/green) internal current sinks.
        │ Acceptable at ≤ 30 % brightness for status indication. For full-
        │ brightness use, route LED_SWITCHED_PWR from a 5 V boost (TPS61023).

FIX-003 │ QUAD-SPI FLASH
        │ v2 hardwired W25N02KV WP# and HOLD# to VCC, permanently disabling
        │ Quad-SPI (QSPI). Those pins now route to ESP32 IO2 / IO3.
        │ QSPI delivers 104 MHz × 4 bits = 416 Mbps vs 104 Mbps (Standard SPI).
        │ ~4× faster audio dump → ~4× shorter Wi-Fi active window per sync →
        │ proportionally less heat deposited in silicone overmold per cycle.

GPIO Map (no conflicts)
───────────────────────
IO0   BTN_MAIN          IO7   LED_VDD_EN_N (FIX-002)
IO1   BTN_SYNC          IO8   WS2812_DATA
IO2   QSPI_IO2 / WP#    IO9   BTN_BATT (moved from IO2 — FIX-003)
IO3   QSPI_IO3 / HOLD#  IO10  FLASH_CS
IO4   MIC_ENABLE_N      IO11  SPI_MOSI
IO5   PDM_CLK           IO12  SPI_CLK
IO6   PDM_DATA          IO13  SPI_MISO
"""

import sys

try:
    from skidl import ERC, Net, Part, generate_netlist
except ModuleNotFoundError as exc:
    sys.exit(f"SKiDL not installed. Run: pip install skidl\n{exc}")

# ── Footprint constants (all must be in Library:Footprint format) ─────────────
FP_ESP32_S3       = "RF_Module:ESP32-S3-WROOM-1_SMD"
FP_IM73D122       = "Sensor_Audio:Infineon_IM73D122_BottomPort"
FP_FLASH_SPI      = "Package_SON:WSON-8-1EP_8x6mm_P1.27mm_EP3.4x4.3mm"
FP_TP4056         = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
FP_BUCKBOOST      = "Package_SON:WSON-10_3x3mm_P0.5mm"        # TPS63031DSOR
FP_INDUCTOR_2U2   = "Inductor_SMD:L_2520_6332Metric"           # 2.2 µH, Isat ≥ 1.5 A
FP_USB_C          = "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12"
FP_BAT_CONN       = "Connector_JST:JST_SH_1x02_P1.00mm_Horizontal"
FP_WS2812B        = "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.81mm"
FP_LED_0603       = "LED_SMD:LED_0603_1608Metric"
FP_SW_PUSH        = "Button_Switch_SMD:SW_SPST_PTS645"
FP_PMOS_SOT23     = "Package_TO_SOT_SMD:SOT-23"
FP_R0402          = "Resistor_SMD:R_0402_1005Metric"
FP_C0402          = "Capacitor_SMD:C_0402_1005Metric"
FP_C0603          = "Capacitor_SMD:C_0603_1608Metric"

# ── Power budget constants ─────────────────────────────────────────────────────
# TP4056 charge current: I_CHG = 1200 / R_PROG
# 4 kΩ → 1200 / 4000 = 0.300 A (300 mA = 0.75 C for 400 mAh cell)
# 0.75 C is the thermal ceiling for a silicone-encapsulated wearable cell.
TP4056_RPROG_OHMS        = 4000   # Ω  — DO NOT CHANGE without recalculating C-rate
TP4056_CHARGE_CURRENT_MA = 300    # mA — I_CHG = 1200 / R_PROG

# Honest Mic LED: I = (Vcc − Vf_red) / R = (3.3 − 2.0) / 150 = 8.67 mA
# High-visibility in daylight; still below absolute-maximum for standard indicator LED.
MIC_LED_RSERIES_OHMS  = 150       # Ω
MIC_LED_CURRENT_MA    = 8.67      # mA  (calculated)

# Deep sleep current budget — FIX-002 applied (WS2812B load switch OFF)
# ESP32-S3 deep sleep (ULP + RTC)         :   8 µA
# TPS63031 quiescent (power-save mode)    :  55 µA
# W25N02KV deep power-down                :   1 µA
# TP4056 standby (VBUS absent)            :   2 µA
# WS2812B (load switch physically open)   :   0 µA  ← FIX-002 contribution
# ─────────────────────────────────────────────────
# Total                                   :  66 µA
# T_standby = 400 mAh / 0.066 mA ≈ 6 060 h ≈ 252 days (theoretical no-wake)
SLEEP_QUIESCENT_UA = 66   # µA — full system deep sleep with FIX-002


# ── Subsystem A: Power management ────────────────────────────────────────────
def _build_power(gnd: Net) -> tuple:
    """
    USB-C receptacle → TP4056 charger → LiPo (VBAT)
    VBAT → TPS63031 buck-boost → 3V3_SYS

    FIX-001: The TPS63031 WSON-10 buck-boost replaces the AP2112K-3.3 LDO.
    The inductor must be Bourns SRN2512 or equivalent 2.2 µH, Isat ≥ 1.5 A,
    DCR ≤ 130 mΩ. Place within 2 mm of L1/L2 pads per TI layout guide.
    """
    vbus_5v = Net("VBUS_5V")
    vbat    = Net("VBAT")
    vcc_3v3 = Net("3V3_SYS")

    # USB-C receptacle
    usb_c = Part("Connector_USB", "USB_C_Receptacle", footprint=FP_USB_C)
    usb_c["GND"]  += gnd
    usb_c["VBUS"] += vbus_5v

    # CC1 / CC2 5.1 kΩ pull-downs signal sink role to the charger
    r_cc1 = Part("Device", "R", footprint=FP_R0402, value="5k1")
    r_cc2 = Part("Device", "R", footprint=FP_R0402, value="5k1")
    r_cc1[1] += usb_c["CC1"]; r_cc1[2] += gnd
    r_cc2[1] += usb_c["CC2"]; r_cc2[2] += gnd

    # TP4056 LiPo charger
    charger = Part("HonestPuck_Lib", "TP4056", footprint=FP_TP4056)
    charger["GND"] += gnd
    charger["VCC"] += vbus_5v
    charger["BAT"] += vbat
    # PROG: 4 kΩ → I_CHG = 300 mA (0.75 C) — thermally safe in silicone enclosure
    r_prog = Part("Device", "R", footprint=FP_R0402, value=f"{TP4056_RPROG_OHMS}")
    r_prog[1] += charger["PROG"]; r_prog[2] += gnd

    # Battery connector (JST SH 1.25 mm 2-pin)
    bat_conn = Part("Connector_JST", "JST_SH_1x02", footprint=FP_BAT_CONN)
    bat_conn[1] += vbat
    bat_conn[2] += gnd

    # TPS63031 buck-boost converter (FIX-001)
    # VIN: 1.8–5.5 V — covers 3.0 V (discharged) → 4.2 V (full) LiPo
    # VOUT: 3.3 V fixed; PS/SYNC → GND enables power-save at light loads (< 1 µA Iq)
    buckboost = Part("HonestPuck_Lib", "TPS63031", footprint=FP_BUCKBOOST)
    buckboost["GND"]     += gnd
    buckboost["VIN"]     += vbat
    buckboost["EN"]      += vbat       # Enabled whenever battery is present
    buckboost["VOUT"]    += vcc_3v3
    buckboost["PS_SYNC"] += gnd        # Power-save mode: lowest Iq at light loads

    # Switching inductor (TPS63031 requirement): 2.2 µH between L1 and L2
    l_bb = Part("Device", "L", footprint=FP_INDUCTOR_2U2, value="2u2")
    l_bb[1] += buckboost["L1"]
    l_bb[2] += buckboost["L2"]

    # PGOOD open-drain pull-up — can be monitored by ESP32 for power sequencing
    r_pgood = Part("Device", "R", footprint=FP_R0402, value="100k")
    r_pgood[1] += vcc_3v3; r_pgood[2] += buckboost["PGOOD"]

    # Input bypass: 22 µF bulk + 100 nF HF (TI datasheet §10.1)
    c_vin_bulk = Part("Device", "C", footprint=FP_C0603, value="22u")
    c_vin_hf   = Part("Device", "C", footprint=FP_C0402, value="100n")
    c_vin_bulk[1] += vbat;    c_vin_bulk[2] += gnd
    c_vin_hf[1]   += vbat;    c_vin_hf[2]   += gnd

    # Output bypass: 22 µF bulk + 100 nF HF
    c_vout_bulk = Part("Device", "C", footprint=FP_C0603, value="22u")
    c_vout_hf   = Part("Device", "C", footprint=FP_C0402, value="100n")
    c_vout_bulk[1] += vcc_3v3; c_vout_bulk[2] += gnd
    c_vout_hf[1]   += vcc_3v3; c_vout_hf[2]   += gnd

    # VBAT rail bulk decoupling (absorbs TP4056 → LiPo current transients)
    c_bat = Part("Device", "C", footprint=FP_C0603, value="10u")
    c_bat[1] += vbat; c_bat[2] += gnd

    return vbus_5v, vbat, vcc_3v3


# ── Subsystem B: ESP32-S3 microcontroller ────────────────────────────────────
def _build_brain(gnd: Net, vcc_3v3: Net) -> Part:
    """
    ESP32-S3-WROOM-1 with mandatory EN pull-up and decoupling.
    The 10 kΩ EN pull-up ensures clean power-on reset; without it, EN can
    float through the module's internal weak pull-up, causing unreliable boot.
    """
    esp32 = Part("HonestPuck_Lib", "ESP32-S3-WROOM-1", footprint=FP_ESP32_S3)
    esp32["GND"] += gnd
    esp32["3V3"] += vcc_3v3

    # EN pull-up (required for reliable boot)
    r_en = Part("Device", "R", footprint=FP_R0402, value="10k")
    r_en[1] += vcc_3v3; r_en[2] += esp32["EN"]

    # Module decoupling (10 µF bulk + 100 nF HF near module pads)
    c_bulk = Part("Device", "C", footprint=FP_C0603, value="10u")
    c_hf   = Part("Device", "C", footprint=FP_C0402, value="100n")
    c_bulk[1] += vcc_3v3; c_bulk[2] += gnd
    c_hf[1]   += vcc_3v3; c_hf[2]   += gnd

    return esp32


# ── Subsystem C: W25N02KV NAND flash — Quad-SPI enabled ─────────────────────
def _build_qspi_flash(gnd: Net, vcc_3v3: Net, esp32: Part) -> None:
    """
    Winbond W25N02KV 2 Gb SPI NAND — Quad-SPI (QSPI) mode.

    FIX-003: WP# and HOLD# are NOT tied to VCC. They route to ESP32 IO2/IO3,
    enabling 4-bit QSPI transfers for ~4× the read bandwidth of Standard SPI.

    QSPI performance at 104 MHz:
      Standard SPI : 104 MHz × 1 bit = 104 Mbps
      Dual SPI     : 104 MHz × 2 bit = 208 Mbps
      Quad SPI     : 104 MHz × 4 bit = 416 Mbps  ← FIX-003 enables this

    For QSPI to function, the ESP32 SPI driver must be configured with
    quad_mode=True and io2/io3 mapped to IO2/IO3 before flash access.
    Software initialisation: set WP#=1, HOLD#=1 in Standard SPI first,
    then switch to QSPI mode via W25N02KV status register SR3[1] = 1.
    """
    flash = Part("HonestPuck_Lib", "W25N02KV", footprint=FP_FLASH_SPI)
    flash["GND"] += gnd
    flash["VCC"] += vcc_3v3

    # Standard SPI bus (shared IO0/IO1 in quad mode)
    spi_clk  = Net("SPI_CLK")
    spi_mosi = Net("SPI_MOSI")   # IO0 in QSPI mode
    spi_miso = Net("SPI_MISO")   # IO1 in QSPI mode
    spi_cs   = Net("FLASH_CS")

    # QSPI extended data lines (FIX-003)
    qspi_io2 = Net("QSPI_IO2")   # W25N02KV WP# pin — was hardwired VCC in v2
    qspi_io3 = Net("QSPI_IO3")   # W25N02KV HOLD# pin — was hardwired VCC in v2

    esp32["IO12"] += spi_clk;   flash["CLK"]  += spi_clk
    esp32["IO11"] += spi_mosi;  flash["DI"]   += spi_mosi   # IO0
    esp32["IO13"] += spi_miso;  flash["DO"]   += spi_miso   # IO1
    esp32["IO10"] += spi_cs;    flash["CS"]   += spi_cs
    esp32["IO2"]  += qspi_io2;  flash["WP"]   += qspi_io2   # IO2 — FIX-003
    esp32["IO3"]  += qspi_io3;  flash["HOLD"] += qspi_io3   # IO3 — FIX-003

    # Flash decoupling (100 nF as close as possible to VCC pad)
    c_flash = Part("Device", "C", footprint=FP_C0402, value="100n")
    c_flash[1] += vcc_3v3; c_flash[2] += gnd


# ── Subsystem D: Hardware-enforced privacy interlock ─────────────────────────
def _build_honest_mic(gnd: Net, vcc_3v3: Net, esp32: Part) -> None:
    """
    The defining guarantee of the Honest Puck: it is physically impossible to
    power the microphone silicon die without simultaneously forward-biasing the
    red LED. Both share the HONEST_MIC_PWR net on the drain side of the PMOS.

    Voltage / current analysis (Ohm's Law):
      I_LED = (Vcc − Vf_red) / R = (3.3 V − 2.0 V) / 150 Ω = 8.67 mA ✓
      I_mic ≈ 1.0 mA (IM73D122 typical active current)
      I_total = 9.67 mA through PMOS (BSS84)

      V_drop(PMOS) = I × R_DS(on) = 9.67 mA × 10 Ω (max BSS84 at Vgs=−3.3 V)
                   = 96.7 mV
      V_mic_actual = 3.3 V − 0.097 V = 3.203 V
      IM73D122 V_op_min = 1.62 V → margin = +1.583 V ✓

    Gate pull-up R_MIC_GATE (10 kΩ to 3V3) clamps PMOS gate to source
    during deep sleep (ESP32 IO4 → Hi-Z), guaranteeing mic is OFF.
    There is no firmware path to power the mic without lighting the LED.
    """
    mic     = Part("HonestPuck_Lib", "IM73D122",    footprint=FP_IM73D122)
    q_mic   = Part("Device",        "Q_PMOS_GSD",   footprint=FP_PMOS_SOT23, value="BSS84")
    red_led = Part("Device",        "LED",           footprint=FP_LED_0603,   value="RED_TRUTH")
    r_led   = Part("Device",        "R",             footprint=FP_R0402,      value=f"{MIC_LED_RSERIES_OHMS}")
    r_gate  = Part("Device",        "R",             footprint=FP_R0402,      value="10k")

    mic_en_n   = Net("MIC_ENABLE_N")    # Active-low: LOW = mic on (PMOS conducts)
    honest_pwr = Net("HONEST_MIC_PWR")  # Switched rail: only live when mic is active

    # PMOS high-side load switch
    q_mic["S"] += vcc_3v3
    q_mic["G"] += mic_en_n
    q_mic["D"] += honest_pwr

    # Gate: ESP32 drives LOW to activate; 10 kΩ pull-up forces OFF during sleep
    esp32["IO4"] += mic_en_n
    r_gate[1] += vcc_3v3; r_gate[2] += mic_en_n

    # Hardware interlock: mic VDD and LED anode share the same copper trace
    mic["VDD"]    += honest_pwr
    mic["GND"]    += gnd
    red_led["A"]  += honest_pwr   # Anode on switched rail — always lit with mic
    red_led["K"]  += r_led[1]
    r_led[2]      += gnd

    # PDM audio interface
    pdm_clk  = Net("PDM_CLK")
    pdm_data = Net("PDM_DATA")
    mic["CLK"]  += pdm_clk;   esp32["IO5"] += pdm_clk
    mic["DATA"] += pdm_data;  esp32["IO6"] += pdm_data


# ── Subsystem E: UI — buttons and WS2812B LED ring ───────────────────────────
def _build_ui(gnd: Net, vcc_3v3: Net, esp32: Part) -> None:
    """
    Three tactile buttons (hardware pull-ups for deep-sleep reliability) and
    an 8-pixel WS2812B ring behind a PMOS load switch (FIX-002).

    Button GPIO assignment note: BTN_BATT moved IO2 → IO9 because IO2 is now
    reserved for QSPI_IO2 (W25N02KV WP#) per FIX-003.

    FIX-002 load switch analysis:
      Q_LED (BSS84): gate pulled to 3V3 by R_LED_GATE → PMOS OFF during sleep.
      ESP32 IO7 drives LED_VDD_EN_N LOW to illuminate the ring.
      Power saved: 8 × 0.8 mA = 6.4 mA → 0 mA during deep sleep.

    WS2812B supply warning (see module docstring):
      VDD_min = 3.5 V per datasheet; 3V3_SYS is below spec.
      InGaN blue/green Vf ≈ 3.0–3.4 V; internal current sink needs
      ≥ 300 mV overhead to remain in saturation. At 3.3 V there is
      < 100 mV margin — blue/green may flicker at low battery.
    """
    # ── Tactile buttons with 10 kΩ hardware pull-ups ─────────────────────────
    btn_main = Part("Device", "SW_Push", footprint=FP_SW_PUSH, value="BTN_MAIN")
    btn_sync = Part("Device", "SW_Push", footprint=FP_SW_PUSH, value="BTN_SYNC")
    btn_batt = Part("Device", "SW_Push", footprint=FP_SW_PUSH, value="BTN_BATT")

    gpio_main = Net("GPIO_BTN_MAIN"); esp32["IO0"] += gpio_main
    gpio_sync = Net("GPIO_BTN_SYNC"); esp32["IO1"] += gpio_sync
    gpio_batt = Net("GPIO_BTN_BATT"); esp32["IO9"] += gpio_batt  # IO2 freed for QSPI

    btn_main[1] += gpio_main; btn_main[2] += gnd
    btn_sync[1] += gpio_sync; btn_sync[2] += gnd
    btn_batt[1] += gpio_batt; btn_batt[2] += gnd

    # Hardware pull-ups (10 kΩ): I_pullup = 3.3/10k = 330 µA momentary on press
    # Physical resistors prevent floating-gate false wakeups from EMI / ESD
    r_pu_main = Part("Device", "R", footprint=FP_R0402, value="10k")
    r_pu_sync = Part("Device", "R", footprint=FP_R0402, value="10k")
    r_pu_batt = Part("Device", "R", footprint=FP_R0402, value="10k")
    r_pu_main[1] += vcc_3v3; r_pu_main[2] += gpio_main
    r_pu_sync[1] += vcc_3v3; r_pu_sync[2] += gpio_sync
    r_pu_batt[1] += vcc_3v3; r_pu_batt[2] += gpio_batt

    # ── WS2812B load switch (FIX-002) ────────────────────────────────────────
    q_led       = Part("Device", "Q_PMOS_GSD", footprint=FP_PMOS_SOT23, value="BSS84")
    r_led_gate  = Part("Device", "R",          footprint=FP_R0402,      value="10k")

    led_vdd_en_n     = Net("LED_VDD_EN_N")      # Active-low control from ESP32 IO7
    led_switched_pwr = Net("LED_SWITCHED_PWR")  # Switched VDD for WS2812B ring

    q_led["S"] += vcc_3v3
    q_led["G"] += led_vdd_en_n
    q_led["D"] += led_switched_pwr

    # Gate pull-up: forces PMOS OFF when ESP32 IO7 is high-impedance (deep sleep)
    esp32["IO7"] += led_vdd_en_n
    r_led_gate[1] += vcc_3v3; r_led_gate[2] += led_vdd_en_n

    # ── 8-pixel WS2812B ring (powered from switched rail, not 3V3_SYS) ───────
    led_data_net = Net("WS2812_DATA")
    esp32["IO8"] += led_data_net

    leds = [
        Part("Device", "LED_ARGB", footprint=FP_WS2812B, value="WS2812B")
        for _ in range(8)
    ]

    current_din = led_data_net
    for i, led in enumerate(leds):
        led["VDD"] += led_switched_pwr   # FIX-002: switched rail, NOT 3V3_SYS
        led["VSS"] += gnd
        led["DIN"] += current_din
        if i < 7:
            dout_net    = Net(f"WS2812_DOUT_{i}")
            led["DOUT"] += dout_net
            current_din = dout_net


# ── Top-level entry point ─────────────────────────────────────────────────────
def generate_netlist() -> None:
    gnd = Net("GND")

    vbus_5v, vbat, vcc_3v3 = _build_power(gnd)
    esp32 = _build_brain(gnd, vcc_3v3)
    _build_qspi_flash(gnd, vcc_3v3, esp32)
    _build_honest_mic(gnd, vcc_3v3, esp32)
    _build_ui(gnd, vcc_3v3, esp32)

    ERC()
    generate_netlist(file_="honest_puck_v3.net")

    i_chg = 1200 / TP4056_RPROG_OHMS
    c_rate = (i_chg * 1000) / 400   # C-rate for 400 mAh cell
    i_led  = (3.3 - 2.0) / MIC_LED_RSERIES_OHMS
    t_standby_h = 400 / (SLEEP_QUIESCENT_UA / 1000)
    print(
        f"Netlist: honest_puck_v3.net\n"
        f"  Charge current : {i_chg * 1000:.0f} mA ({c_rate:.2f} C)\n"
        f"  LED current    : {i_led * 1000:.2f} mA (R={MIC_LED_RSERIES_OHMS} Ω)\n"
        f"  Sleep budget   : ~{SLEEP_QUIESCENT_UA} µA\n"
        f"  Max standby    : ~{t_standby_h:.0f} h ({t_standby_h / 24:.0f} days)"
    )


if __name__ == "__main__":
    generate_netlist()
