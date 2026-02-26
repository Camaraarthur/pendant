"""
tests/test_honest_puck_v3.py

SKiDL-free validation of netlist/honest_puck_v3.py.
All tests inspect the source as plain text or as a parsed AST.
No KiCad, SKiDL, or hardware is required to run the suite.

Coverage
────────
FIX-001  Power regulation (TPS63031 buck-boost, no LDO)
FIX-002  WS2812B load switch (no direct 3V3 on LED VDD)
FIX-003  Quad-SPI flash (WP#/HOLD# to IO2/IO3, not VCC)
         Honest Mic hardware interlock (PMOS + LED in series)
         Button UI (hardware pull-ups, no GPIO conflict with QSPI)
         Math proofs (charge current, LED current, sleep budget)
         Footprint format (all FP_* constants in Library:Footprint)
         Regression guards (anti-patterns that must remain absent)
"""

import ast
import re
from pathlib import Path

import pytest

# ── Source under test ─────────────────────────────────────────────────────────
NETLIST = Path(__file__).parent.parent / "netlist" / "honest_puck_v3.py"


@pytest.fixture(scope="module")
def src() -> str:
    return NETLIST.read_text()


@pytest.fixture(scope="module")
def tree(src) -> ast.Module:
    return ast.parse(src)


@pytest.fixture(scope="module")
def constants(tree) -> dict:
    """Extract every top-level NAME = <literal> assignment."""
    result: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        result[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fn_body(src: str, fn_name: str) -> str:
    """Return the source text of the named function (or '' if not found)."""
    pattern = re.compile(
        rf"def {re.escape(fn_name)}\b.*?(?=\ndef |\Z)", re.DOTALL
    )
    m = pattern.search(src)
    return m.group(0) if m else ""


def _code_lines(src: str) -> str:
    """Return only non-blank, non-comment lines joined as a single string."""
    lines = [
        ln for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  FIX-001 — Power regulation: TPS63031 buck-boost replaces AP2112K LDO
# ═══════════════════════════════════════════════════════════════════════════════

class TestFix001BuckBoost:

    def test_tps63031_present(self, src):
        """TPS63031 must be instantiated as the main 3V3 regulator."""
        assert "TPS63031" in src, "TPS63031 buck-boost not found in source"

    def test_no_ap2112k_anywhere(self, src):
        """AP2112K-3.3 LDO must not be instantiated — replaced by TPS63031.
        The part name may appear in docstrings/comments, but never in Part() calls."""
        part_calls = re.findall(r'Part\([^)]+\)', src, re.DOTALL)
        for call in part_calls:
            assert "AP2112K" not in call, (
                f"AP2112K found in Part() call: {call!r} — FIX-001 regression"
            )

    def test_buckboost_footprint_constant(self, constants):
        """FP_BUCKBOOST must reference the WSON-10 package."""
        fp = constants.get("FP_BUCKBOOST", "")
        assert "WSON" in fp or "SON" in fp, (
            f"FP_BUCKBOOST='{fp}' does not reference a WSON/SON package"
        )

    def test_buckboost_inductor_constant(self, constants):
        """FP_INDUCTOR_2U2 must be defined for the buck-boost inductor."""
        assert "FP_INDUCTOR_2U2" in constants, "FP_INDUCTOR_2U2 constant missing"
        assert ":" in constants["FP_INDUCTOR_2U2"], (
            "FP_INDUCTOR_2U2 not in Library:Footprint format"
        )

    def test_buckboost_has_input_bypass_caps(self, src):
        """TPS63031 requires ≥ 10 µF on VIN — check bulk cap present."""
        power_body = _fn_body(src, "_build_power")
        assert "c_vin_bulk" in power_body, (
            "VIN bulk bypass cap (c_vin_bulk) missing from _build_power"
        )

    def test_buckboost_has_output_bypass_caps(self, src):
        """TPS63031 requires ≥ 22 µF on VOUT — check bulk cap present."""
        power_body = _fn_body(src, "_build_power")
        assert "c_vout_bulk" in power_body, (
            "VOUT bulk bypass cap (c_vout_bulk) missing from _build_power"
        )

    def test_buckboost_inductor_instantiated(self, src):
        """The switching inductor (l_bb) connecting L1 and L2 must be present."""
        power_body = _fn_body(src, "_build_power")
        assert "l_bb" in power_body, "Buck-boost inductor (l_bb) not found in _build_power"
        assert '"L1"' in power_body, "L1 pin connection missing for buck-boost inductor"
        assert '"L2"' in power_body, "L2 pin connection missing for buck-boost inductor"

    def test_buckboost_en_tied_to_vbat(self, src):
        """EN pin must be tied to VBAT (always-on when battery present)."""
        power_body = _fn_body(src, "_build_power")
        assert 'buckboost["EN"]' in power_body, "TPS63031 EN pin not connected"
        assert re.search(r'buckboost\["EN"\]\s*\+=\s*vbat', power_body), (
            "TPS63031 EN should be tied to VBAT for always-enabled operation"
        )

    def test_buckboost_ps_sync_to_gnd(self, src):
        """PS/SYNC grounded enables power-save mode (minimum quiescent current)."""
        power_body = _fn_body(src, "_build_power")
        assert re.search(r'buckboost\["PS_SYNC"\]\s*\+=\s*gnd', power_body), (
            "PS_SYNC should be grounded to enable power-save mode"
        )

    def test_pgood_pullup_present(self, src):
        """PGOOD open-drain output requires an external pull-up resistor."""
        power_body = _fn_body(src, "_build_power")
        assert "PGOOD" in power_body, "PGOOD pin not connected in _build_power"
        assert "r_pgood" in power_body, "PGOOD pull-up resistor (r_pgood) missing"

    def test_3v3_sys_net_name(self, src):
        """Primary 3V3 rail must use the canonical Net('3V3_SYS') name."""
        assert 'Net("3V3_SYS")' in src, 'Net("3V3_SYS") not defined'

    def test_vbat_net_name(self, src):
        assert 'Net("VBAT")' in src, 'Net("VBAT") not defined'

    def test_vbus_net_name(self, src):
        assert 'Net("VBUS_5V")' in src, 'Net("VBUS_5V") not defined'


# ═══════════════════════════════════════════════════════════════════════════════
#  FIX-002 — WS2812B isolated behind PMOS load switch
# ═══════════════════════════════════════════════════════════════════════════════

class TestFix002LEDLoadSwitch:

    def test_led_vdd_en_n_net_defined(self, src):
        """LED_VDD_EN_N net must exist as the load-switch gate control."""
        assert 'Net("LED_VDD_EN_N")' in src, 'Net("LED_VDD_EN_N") not defined — FIX-002'

    def test_led_switched_pwr_net_defined(self, src):
        """LED_SWITCHED_PWR net must exist as the isolated WS2812B supply."""
        assert 'Net("LED_SWITCHED_PWR")' in src, (
            'Net("LED_SWITCHED_PWR") not defined — FIX-002'
        )

    def test_ws2812b_vdd_not_directly_on_3v3(self, src):
        """WS2812B VDD must NOT connect directly to vcc_3v3 (would defeat FIX-002)."""
        assert not re.search(r'led\["VDD"\]\s*\+=\s*vcc_3v3', src), (
            'led["VDD"] += vcc_3v3 found — WS2812B is directly on 3V3_SYS, FIX-002 violated'
        )

    def test_ws2812b_vdd_on_switched_rail(self, src):
        """WS2812B VDD must connect to led_switched_pwr (the gated rail)."""
        assert re.search(r'led\["VDD"\]\s*\+=\s*led_switched_pwr', src), (
            'led["VDD"] += led_switched_pwr not found — WS2812B not behind load switch'
        )

    def test_led_switch_pmos_in_ui(self, src):
        """A PMOS load switch (q_led) must be present in _build_ui."""
        ui_body = _fn_body(src, "_build_ui")
        assert "q_led" in ui_body, "LED load switch PMOS (q_led) not found in _build_ui"
        assert "Q_PMOS_GSD" in ui_body, "q_led must be a Q_PMOS_GSD device"

    def test_led_switch_source_on_3v3(self, src):
        """PMOS source must connect to vcc_3v3 (high-side switch)."""
        ui_body = _fn_body(src, "_build_ui")
        assert re.search(r'q_led\["S"\]\s*\+=\s*vcc_3v3', ui_body), (
            "q_led source not connected to vcc_3v3"
        )

    def test_led_switch_drain_on_switched_pwr(self, src):
        """PMOS drain must connect to led_switched_pwr (the gated output)."""
        ui_body = _fn_body(src, "_build_ui")
        assert re.search(r'q_led\["D"\]\s*\+=\s*led_switched_pwr', ui_body), (
            "q_led drain not connected to led_switched_pwr"
        )

    def test_led_gate_pullup_present(self, src):
        """10 kΩ gate pull-up ensures PMOS is OFF during deep sleep (IO7 → Hi-Z)."""
        ui_body = _fn_body(src, "_build_ui")
        assert "r_led_gate" in ui_body, "LED load switch gate pull-up (r_led_gate) missing"
        assert re.search(r'r_led_gate\[1\]\s*\+=\s*vcc_3v3', ui_body), (
            "r_led_gate[1] must tie to vcc_3v3"
        )
        assert re.search(r'r_led_gate\[2\]\s*\+=\s*led_vdd_en_n', ui_body), (
            "r_led_gate[2] must tie to led_vdd_en_n"
        )

    def test_led_gate_driven_by_io7(self, src):
        """ESP32 IO7 must control the LED load switch gate (LED_VDD_EN_N)."""
        ui_body = _fn_body(src, "_build_ui")
        assert re.search(r'esp32\["IO7"\]\s*\+=\s*led_vdd_en_n', ui_body), (
            "ESP32 IO7 not connected to LED_VDD_EN_N"
        )

    def test_ws2812b_count_is_8(self, src):
        """The ring must instantiate 8 WS2812B pixels.
        The list comprehension uses range(8); verify that directly."""
        ui_body = _fn_body(src, "_build_ui")
        # Parts created via list comprehension: Part(..., value="WS2812B") × range(8)
        assert '"WS2812B"' in ui_body, '"WS2812B" value string not in _build_ui'
        assert re.search(r'range\(8\)', ui_body), (
            "range(8) not found in _build_ui — WS2812B ring may not be 8 pixels"
        )

    def test_daisy_chain_has_7_dout_nets(self, src):
        """8 chained pixels produce 7 DOUT nets via f-string inside the loop."""
        ui_body = _fn_body(src, "_build_ui")
        # The loop creates nets with an f-string like Net(f"WS2812_DOUT_{i}")
        assert re.search(r'Net\(f["\']WS2812_DOUT_', ui_body), (
            "DOUT f-string Net creation not found in _build_ui — daisy-chain missing"
        )
        # And the loop guard 'if i < 7' ensures exactly 7 DOUT nets
        assert re.search(r'if\s+i\s*<\s*7', ui_body), (
            "'if i < 7' guard missing — last pixel DOUT will be incorrectly connected"
        )

    def test_ws2812_data_net_defined(self, src):
        assert 'Net("WS2812_DATA")' in src, 'Net("WS2812_DATA") missing'


# ═══════════════════════════════════════════════════════════════════════════════
#  FIX-003 — Quad-SPI: WP# and HOLD# to IO2/IO3
# ═══════════════════════════════════════════════════════════════════════════════

class TestFix003QuadSPI:

    def test_qspi_io2_net_defined(self, src):
        """QSPI_IO2 net must be defined (replaces WP# hardwired to VCC)."""
        assert 'Net("QSPI_IO2")' in src, 'Net("QSPI_IO2") not defined — FIX-003'

    def test_qspi_io3_net_defined(self, src):
        """QSPI_IO3 net must be defined (replaces HOLD# hardwired to VCC)."""
        assert 'Net("QSPI_IO3")' in src, 'Net("QSPI_IO3") not defined — FIX-003'

    def test_flash_wp_not_hardwired_to_vcc(self, src):
        """flash[WP] must NOT be tied to vcc_3v3 — that disables QSPI."""
        assert not re.search(r'flash\["WP"\]\s*\+=\s*vcc_3v3', src), (
            'flash["WP"] += vcc_3v3 found — QSPI permanently disabled (FIX-003 violated)'
        )

    def test_flash_hold_not_hardwired_to_vcc(self, src):
        """flash[HOLD] must NOT be tied to vcc_3v3 — that disables QSPI."""
        assert not re.search(r'flash\["HOLD"\]\s*\+=\s*vcc_3v3', src), (
            'flash["HOLD"] += vcc_3v3 found — QSPI permanently disabled (FIX-003 violated)'
        )

    def test_flash_wp_routes_to_qspi_io2(self, src):
        """WP# must connect to the QSPI_IO2 net via ESP32 IO2."""
        flash_body = _fn_body(src, "_build_qspi_flash")
        assert re.search(r'flash\["WP"\]\s*\+=\s*qspi_io2', flash_body), (
            'flash["WP"] not connected to qspi_io2 net'
        )

    def test_flash_hold_routes_to_qspi_io3(self, src):
        """HOLD# must connect to the QSPI_IO3 net via ESP32 IO3."""
        flash_body = _fn_body(src, "_build_qspi_flash")
        assert re.search(r'flash\["HOLD"\]\s*\+=\s*qspi_io3', flash_body), (
            'flash["HOLD"] not connected to qspi_io3 net'
        )

    def test_esp32_io2_drives_qspi_io2(self, src):
        """ESP32 IO2 must be the MCU-side connection for QSPI_IO2."""
        flash_body = _fn_body(src, "_build_qspi_flash")
        assert re.search(r'esp32\["IO2"\]\s*\+=\s*qspi_io2', flash_body), (
            "ESP32 IO2 not connected to qspi_io2"
        )

    def test_esp32_io3_drives_qspi_io3(self, src):
        """ESP32 IO3 must be the MCU-side connection for QSPI_IO3."""
        flash_body = _fn_body(src, "_build_qspi_flash")
        assert re.search(r'esp32\["IO3"\]\s*\+=\s*qspi_io3', flash_body), (
            "ESP32 IO3 not connected to qspi_io3"
        )

    def test_standard_spi_nets_still_present(self, src):
        """SPI_CLK, SPI_MOSI, SPI_MISO and FLASH_CS must coexist with QSPI nets."""
        for net in ("SPI_CLK", "SPI_MOSI", "SPI_MISO", "FLASH_CS"):
            assert f'Net("{net}")' in src, f'Net("{net}") missing from flash subsystem'

    def test_flash_decoupling_cap_present(self, src):
        """100 nF decoupling cap must be placed close to W25N02KV VCC pad."""
        flash_body = _fn_body(src, "_build_qspi_flash")
        assert "c_flash" in flash_body, "Flash decoupling cap (c_flash) missing"


# ═══════════════════════════════════════════════════════════════════════════════
#  Honest Mic hardware interlock
# ═══════════════════════════════════════════════════════════════════════════════

class TestHonestMicInterlock:

    def test_honest_mic_pwr_net_defined(self, src):
        assert 'Net("HONEST_MIC_PWR")' in src, 'Net("HONEST_MIC_PWR") not defined'

    def test_mic_enable_n_net_defined(self, src):
        assert 'Net("MIC_ENABLE_N")' in src, 'Net("MIC_ENABLE_N") not defined'

    def test_pmos_load_switch_in_mic(self, src):
        """A PMOS (q_mic / BSS84) must be the mic load switch."""
        mic_body = _fn_body(src, "_build_honest_mic")
        assert "Q_PMOS_GSD" in mic_body, "PMOS load switch not found in _build_honest_mic"
        assert "BSS84" in mic_body, "BSS84 PMOS value not specified"

    def test_mic_pmos_source_on_3v3(self, src):
        mic_body = _fn_body(src, "_build_honest_mic")
        assert re.search(r'q_mic\["S"\]\s*\+=\s*vcc_3v3', mic_body), (
            "PMOS source not on vcc_3v3 — load switch topology broken"
        )

    def test_mic_pmos_drain_on_honest_pwr(self, src):
        mic_body = _fn_body(src, "_build_honest_mic")
        assert re.search(r'q_mic\["D"\]\s*\+=\s*honest_pwr', mic_body), (
            "PMOS drain not on honest_pwr — interlock broken"
        )

    def test_red_led_anode_on_honest_pwr(self, src):
        """LED anode on HONEST_MIC_PWR is what creates the physical interlock."""
        mic_body = _fn_body(src, "_build_honest_mic")
        assert re.search(r'red_led\["A"\]\s*\+=\s*honest_pwr', mic_body), (
            "red_led anode not on honest_pwr — interlock broken"
        )

    def test_red_led_present_with_correct_value(self, src):
        assert '"RED_TRUTH"' in src, 'RED_TRUTH LED value not found'

    def test_mic_led_rseries_constant(self, constants):
        """MIC_LED_RSERIES_OHMS must equal 150 Ω for 8.67 mA brightness."""
        assert constants.get("MIC_LED_RSERIES_OHMS") == 150, (
            f"MIC_LED_RSERIES_OHMS = {constants.get('MIC_LED_RSERIES_OHMS')}, expected 150"
        )

    def test_mic_led_current_constant(self, constants):
        """MIC_LED_CURRENT_MA must be ≈ 8.67 mA ((3.3 − 2.0) / 150)."""
        val = constants.get("MIC_LED_CURRENT_MA", 0)
        assert abs(val - 8.67) < 0.05, (
            f"MIC_LED_CURRENT_MA = {val}, expected ≈ 8.67 mA"
        )

    def test_gate_pullup_to_3v3(self, src):
        """10 kΩ gate pull-up forces PMOS OFF when IO4 is Hi-Z during sleep."""
        mic_body = _fn_body(src, "_build_honest_mic")
        assert "r_gate" in mic_body, "Gate pull-up (r_gate) missing from mic interlock"
        assert re.search(r'r_gate\[1\]\s*\+=\s*vcc_3v3', mic_body), (
            "r_gate[1] not on vcc_3v3"
        )
        assert re.search(r'r_gate\[2\]\s*\+=\s*mic_en_n', mic_body), (
            "r_gate[2] not on mic_en_n"
        )

    def test_io4_drives_mic_enable(self, src):
        mic_body = _fn_body(src, "_build_honest_mic")
        assert re.search(r'esp32\["IO4"\]\s*\+=\s*mic_en_n', mic_body), (
            "ESP32 IO4 not connected to MIC_ENABLE_N"
        )

    def test_pdm_clk_net_defined(self, src):
        assert 'Net("PDM_CLK")' in src, 'Net("PDM_CLK") missing'

    def test_pdm_data_net_defined(self, src):
        assert 'Net("PDM_DATA")' in src, 'Net("PDM_DATA") missing'

    def test_mic_vdd_on_honest_pwr(self, src):
        mic_body = _fn_body(src, "_build_honest_mic")
        assert re.search(r'mic\["VDD"\]\s*\+=\s*honest_pwr', mic_body), (
            'mic["VDD"] not on honest_pwr — mic and LED are not interlocked'
        )

    def test_mic_vdd_not_on_3v3(self, src):
        """Mic VDD must never be on 3V3_SYS — that would break the interlock."""
        assert not re.search(r'mic\["VDD"\]\s*\+=\s*vcc_3v3', src), (
            'mic["VDD"] += vcc_3v3 found — interlock broken'
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  TP4056 charging subsystem
# ═══════════════════════════════════════════════════════════════════════════════

class TestChargingSubsystem:

    def test_rprog_constant_is_4000(self, constants):
        """4 kΩ PROG resistor → 300 mA (0.75 C) — safe for silicone-enclosed cell."""
        val = constants.get("TP4056_RPROG_OHMS")
        assert val == 4000, f"TP4056_RPROG_OHMS = {val}, expected 4000"

    def test_charge_current_constant_is_300(self, constants):
        val = constants.get("TP4056_CHARGE_CURRENT_MA")
        assert val == 300, f"TP4056_CHARGE_CURRENT_MA = {val}, expected 300"

    def test_charge_current_formula_consistent(self, constants):
        """1200 / R_PROG must equal the declared TP4056_CHARGE_CURRENT_MA."""
        r = constants.get("TP4056_RPROG_OHMS", 0)
        declared = constants.get("TP4056_CHARGE_CURRENT_MA", -1)
        assert r > 0, "TP4056_RPROG_OHMS is zero or missing"
        computed = round(1200 / r * 1000)   # mA, rounded to nearest int
        assert computed == declared, (
            f"Charge current formula mismatch: 1200/{r}*1000 = {computed} mA "
            f"≠ declared {declared} mA"
        )

    def test_c_rate_below_one(self, constants):
        """Charge rate must be < 1 C for a 400 mAh cell inside silicone."""
        r = constants.get("TP4056_RPROG_OHMS", 1)
        i_ma = 1200 / r * 1000
        c_rate = i_ma / 400
        assert c_rate < 1.0, (
            f"C-rate = {c_rate:.2f} C (≥ 1C) — thermal runaway risk in encapsulated cell"
        )

    def test_cc_resistors_5k1_present(self, src):
        """USB-C CC lines need 5.1 kΩ pull-downs to signal sink role."""
        matches = re.findall(r'"5k1"', src)
        assert len(matches) >= 2, (
            f"Expected at least 2 × '5k1' CC resistors, found {len(matches)}"
        )

    def test_prog_resistor_value_in_source(self, src):
        """PROG resistor Part must use the TP4056_RPROG_OHMS constant (not a magic number)."""
        assert "TP4056_RPROG_OHMS" in src, (
            "PROG resistor value should reference TP4056_RPROG_OHMS constant"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Button UI
# ═══════════════════════════════════════════════════════════════════════════════

class TestButtonUI:

    def test_three_buttons_present(self, src):
        for name in ("BTN_MAIN", "BTN_SYNC", "BTN_BATT"):
            assert f'"{name}"' in src, f"Button {name} not found in source"

    def test_button_pull_ups_count(self, src):
        """Each of the 3 buttons needs a dedicated 10 kΩ pull-up."""
        ui_body = _fn_body(src, "_build_ui")
        pullups = re.findall(r'r_pu_\w+', ui_body)
        unique = set(pullups)
        assert len(unique) >= 3, (
            f"Expected 3 button pull-up resistors, found {len(unique)}: {unique}"
        )

    def test_button_pull_ups_to_3v3(self, src):
        """Pull-up resistors must connect to vcc_3v3."""
        ui_body = _fn_body(src, "_build_ui")
        ties = re.findall(r'r_pu_\w+\[1\]\s*\+=\s*(\w+)', ui_body)
        assert all(t == "vcc_3v3" for t in ties), (
            f"Some button pull-ups not on vcc_3v3: {ties}"
        )

    def test_gpio_btn_main_net_defined(self, src):
        assert 'Net("GPIO_BTN_MAIN")' in src

    def test_gpio_btn_sync_net_defined(self, src):
        assert 'Net("GPIO_BTN_SYNC")' in src

    def test_gpio_btn_batt_net_defined(self, src):
        assert 'Net("GPIO_BTN_BATT")' in src

    def test_btn_batt_not_on_io2(self, src):
        """IO2 is reserved for QSPI_IO2 (FIX-003); BTN_BATT must use a different pin."""
        ui_body = _fn_body(src, "_build_ui")
        # There must be no esp32["IO2"] connection to gpio_batt
        assert not re.search(r'esp32\["IO2"\]\s*\+=\s*gpio_batt', ui_body), (
            "BTN_BATT is on IO2 — conflicts with QSPI_IO2 (FIX-003)"
        )

    def test_btn_batt_not_on_io3(self, src):
        """IO3 is reserved for QSPI_IO3 (FIX-003)."""
        ui_body = _fn_body(src, "_build_ui")
        assert not re.search(r'esp32\["IO3"\]\s*\+=\s*gpio_batt', ui_body), (
            "BTN_BATT is on IO3 — conflicts with QSPI_IO3 (FIX-003)"
        )

    def test_btn_batt_on_io9(self, src):
        """BTN_BATT must be moved to IO9 (safe GPIO, away from QSPI pins)."""
        ui_body = _fn_body(src, "_build_ui")
        assert re.search(r'esp32\["IO9"\]\s*\+=\s*gpio_batt', ui_body), (
            "BTN_BATT not on IO9 — expected relocation from IO2 per FIX-003"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ESP32 brain subsystem
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrainSubsystem:

    def test_esp32_present(self, src):
        assert "ESP32-S3-WROOM-1" in src, "ESP32-S3-WROOM-1 not found"

    def test_en_pullup_present(self, src):
        """Missing EN pull-up causes unreliable boot — must be present."""
        brain_body = _fn_body(src, "_build_brain")
        assert "r_en" in brain_body, "EN pull-up resistor (r_en) missing"

    def test_en_pullup_to_3v3(self, src):
        brain_body = _fn_body(src, "_build_brain")
        assert re.search(r'r_en\[1\]\s*\+=\s*vcc_3v3', brain_body), (
            "r_en[1] not tied to vcc_3v3"
        )
        assert re.search(r'r_en\[2\]\s*\+=\s*esp32\["EN"\]', brain_body), (
            "r_en[2] not tied to esp32[EN]"
        )

    def test_decoupling_caps_present(self, src):
        brain_body = _fn_body(src, "_build_brain")
        assert "c_bulk" in brain_body, "Bulk decoupling cap missing from _build_brain"
        assert "c_hf" in brain_body,   "HF bypass cap missing from _build_brain"


# ═══════════════════════════════════════════════════════════════════════════════
#  Mathematical proofs
# ═══════════════════════════════════════════════════════════════════════════════

class TestMathProofs:

    def test_charge_current_physics(self, constants):
        """Physics: I_CHG = 1200 V / R_PROG (TP4056 internal reference ratio)."""
        r = constants.get("TP4056_RPROG_OHMS", 0)
        i_ma = 1200 / r * 1000
        assert abs(i_ma - 300) < 1, f"Charge current = {i_ma:.1f} mA, expected 300 mA"

    def test_led_current_physics(self, constants):
        """Physics: I_LED = (3.3 V − 2.0 V) / R_series = 1.3 / 150 = 8.67 mA."""
        r = constants.get("MIC_LED_RSERIES_OHMS", 0)
        i_ma = (3.3 - 2.0) / r * 1000
        declared = constants.get("MIC_LED_CURRENT_MA", -1)
        assert abs(i_ma - declared) < 0.1, (
            f"LED current calc={i_ma:.2f} mA ≠ declared {declared} mA"
        )

    def test_pmos_vdrop_acceptable(self, constants):
        """
        BSS84 R_DS(on) max = 10 Ω at Vgs = −3.3 V.
        I_total ≈ 9.67 mA → V_drop = 96.7 mV → V_mic = 3.203 V >> V_op_min (1.62 V).
        """
        r_ds_on = 10        # Ω   BSS84 max
        i_mic   = 1.0e-3    # A   IM73D122 active
        r_led   = constants.get("MIC_LED_RSERIES_OHMS", 150)
        i_led   = (3.3 - 2.0) / r_led
        i_total = i_mic + i_led
        v_drop  = i_total * r_ds_on
        v_mic   = 3.3 - v_drop
        assert v_mic > 1.62, (
            f"V_mic = {v_mic:.3f} V < IM73D122 V_op_min (1.62 V)"
        )
        assert v_drop < 0.5, (
            f"PMOS V_drop = {v_drop * 1000:.1f} mV > 500 mV — choose a lower R_DS(on) FET"
        )

    def test_sleep_quiescent_ua_constant(self, constants):
        """SLEEP_QUIESCENT_UA must be ≤ 100 µA (WS2812B contributes 0 via FIX-002)."""
        val = constants.get("SLEEP_QUIESCENT_UA", 9999)
        assert val <= 100, (
            f"SLEEP_QUIESCENT_UA = {val} µA — should be ≤ 100 µA with FIX-002 applied"
        )

    def test_standby_life_before_fix002(self):
        """
        Demonstrate the pre-FIX-002 failure: 8 WS2812B × 0.8 mA = 6.4 mA
        continuous → 400 mAh / 4.866 mA ≈ 82 h (only 3.4 days max standby).
        """
        ws2812b_quiescent_ma = 8 * 0.8    # 8 pixels at 0.8 mA each (mid-spec)
        esp32_sleep_ma       = 0.008
        ldo_quiescent_ma     = 0.055
        flash_powerdown_ma   = 0.001
        tp4056_standby_ma    = 0.002

        total_ma = (
            ws2812b_quiescent_ma + esp32_sleep_ma + ldo_quiescent_ma
            + flash_powerdown_ma + tp4056_standby_ma
        )
        battery_mah = 400
        standby_h   = battery_mah / total_ma

        assert standby_h < 100, (
            f"Pre-FIX-002 standby calc error: {standby_h:.1f} h should be < 100 h"
        )

    def test_standby_life_after_fix002(self, constants):
        """
        Post-FIX-002: WS2812B contributes 0 mA → standby > 1 000 h.
        """
        sleep_ua    = constants.get("SLEEP_QUIESCENT_UA", 9999)
        battery_mah = 400
        standby_h   = battery_mah / (sleep_ua / 1000)
        assert standby_h > 1000, (
            f"Post-FIX-002 standby = {standby_h:.0f} h — expected > 1000 h"
        )

    def test_qspi_bandwidth_improvement(self):
        """Prove: QSPI at 104 MHz gives 4× the throughput of Standard SPI."""
        clk_hz   = 104e6
        spi_bw   = clk_hz * 1   # Standard SPI: 1 bit per clock
        qspi_bw  = clk_hz * 4   # QSPI: 4 bits per clock
        assert qspi_bw / spi_bw == 4.0

    def test_ldo_dropout_would_fail_at_3v7(self):
        """
        Prove the pre-FIX-001 AP2112K brownout failure in two steps.

        Step 1 — Zero-margin at Vbat = 3.7 V (nominal):
          Vout = 3.7 V − 0.4 V(dropout) = 3.3 V exactly.
          The LDO has consumed its entire dropout budget. Zero margin.

        Step 2 — Out of regulation at Vbat = 3.5 V (typical 50 % discharge):
          Vout = 3.5 V − 0.4 V(dropout) = 3.1 V < 3.3 V target.
          The pass transistor is now in its ohmic region; the output tracks
          the battery. The 3V3_SYS rail is at 3.1 V — 200 mV below spec.
          ESP32 IO logic levels are violated; Wi-Fi TX will cause dropouts.
        """
        v_dropout = 0.400   # V  AP2112K max dropout at full load

        # Step 1: at nominal voltage, no headroom remains
        v_out_37 = 3.7 - v_dropout
        assert round(v_out_37, 2) <= 3.30, (
            f"Expected Vout ≤ 3.3 V at Vbat=3.7 V, got {v_out_37:.3f} V"
        )

        # Step 2: at 50 % discharge the rail is already below 3.3 V
        v_out_35 = 3.5 - v_dropout
        assert v_out_35 < 3.3, (
            f"LDO should be out of regulation at Vbat=3.5 V, but Vout={v_out_35:.2f} V"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Footprint compliance
# ═══════════════════════════════════════════════════════════════════════════════

class TestFootprintCompliance:

    def test_all_fp_constants_have_colon(self, tree, constants):
        """Every FP_* constant must be in Library:Footprint format."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("FP_"):
                        if isinstance(node.value, ast.Constant):
                            val = node.value.value
                            assert ":" in val, (
                                f"{target.id} = '{val}' is not in 'Library:Footprint' format"
                            )

    def test_fp_esp32_uses_rf_module_lib(self, constants):
        assert constants.get("FP_ESP32_S3", "").startswith("RF_Module:"), (
            "FP_ESP32_S3 must be in the RF_Module KiCad library"
        )

    def test_fp_ws2812b_uses_led_smd_lib(self, constants):
        assert constants.get("FP_WS2812B", "").startswith("LED_SMD:"), (
            "FP_WS2812B must be in the LED_SMD KiCad library"
        )

    def test_fp_pmos_uses_sot23(self, constants):
        fp = constants.get("FP_PMOS_SOT23", "")
        assert "SOT-23" in fp, f"FP_PMOS_SOT23 = '{fp}' does not specify SOT-23"

    def test_fp_r0402_correct_library(self, constants):
        fp = constants.get("FP_R0402", "")
        assert "Resistor_SMD" in fp, f"FP_R0402 not in Resistor_SMD library: '{fp}'"

    def test_fp_c0402_correct_library(self, constants):
        fp = constants.get("FP_C0402", "")
        assert "Capacitor_SMD" in fp, f"FP_C0402 not in Capacitor_SMD library: '{fp}'"


# ═══════════════════════════════════════════════════════════════════════════════
#  Regression guards — anti-patterns that must NEVER appear
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegressionGuards:
    """
    These tests encode the specific mistakes from honest_puck_v2 that were
    fixed in v3. If any of these pass, a blocker has been re-introduced.
    """

    def test_no_ap2112k_ldo(self, src):
        """FIX-001: AP2112K must never reappear in any Part() instantiation call."""
        part_calls = re.findall(r'Part\([^)]+\)', src, re.DOTALL)
        for call in part_calls:
            assert "AP2112K" not in call, (
                f"AP2112K found in Part() call — FIX-001 regression: {call!r}"
            )

    def test_no_ws2812b_vdd_on_3v3_sys(self, src):
        """FIX-002: WS2812B VDD must never be directly on 3V3_SYS."""
        assert not re.search(r'led\["VDD"\]\s*\+=\s*vcc_3v3', src), (
            "FIX-002 regression: WS2812B connected directly to 3V3_SYS"
        )

    def test_no_flash_wp_hardwired(self, src):
        """FIX-003: flash WP# must never be hardwired to VCC."""
        assert not re.search(r'flash\["WP"\]\s*\+=\s*vcc_3v3', src), (
            "FIX-003 regression: flash WP# hardwired to VCC"
        )

    def test_no_flash_hold_hardwired(self, src):
        """FIX-003: flash HOLD# must never be hardwired to VCC."""
        assert not re.search(r'flash\["HOLD"\]\s*\+=\s*vcc_3v3', src), (
            "FIX-003 regression: flash HOLD# hardwired to VCC"
        )

    def test_no_btn_batt_on_io2(self, src):
        """IO2 is QSPI data; BTN_BATT must not share it."""
        ui_body = _fn_body(src, "_build_ui")
        assert not re.search(r'esp32\["IO2"\]\s*\+=\s*gpio_batt', ui_body), (
            "BTN_BATT on IO2 conflicts with QSPI_IO2"
        )

    def test_no_mic_interlock_bypassed(self, src):
        """Mic VDD must never connect directly to 3V3_SYS (would bypass interlock)."""
        assert not re.search(r'mic\["VDD"\]\s*\+=\s*vcc_3v3', src), (
            "Mic interlock bypassed: mic VDD is on 3V3_SYS directly"
        )

    def test_no_en_pin_floating(self, src):
        """ESP32 EN pin must never be left unconnected (causes random resets)."""
        brain_body = _fn_body(src, "_build_brain")
        assert 'esp32["EN"]' in brain_body, "ESP32 EN pin not referenced in _build_brain"

    def test_no_missing_gnd_net(self, src):
        """Top-level GND net must be defined exactly once."""
        gnd_defs = re.findall(r'Net\("GND"\)', src)
        assert len(gnd_defs) == 1, (
            f"Expected exactly 1 Net('GND') definition, found {len(gnd_defs)}"
        )
