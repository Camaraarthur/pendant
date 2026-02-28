# ESP32-S3 Firmware Development Research (February 2026)

Web-sourced research for the Honest Puck pendant firmware.
All findings current as of February 2026.

---

## 1. ESP-IDF Version Status

### Current Stable: **v5.5.3**

ESP-IDF has moved well beyond v5.2. The version timeline:

| Version | Status | Notes |
|---------|--------|-------|
| v5.1.7 | **End of Life** (Dec 2025) | Last bugfix release for v5.1 branch |
| v5.2.x | Supported (maintenance) | Within 30-month support window |
| v5.3.x | Supported (maintenance) | Within 30-month support window |
| v5.4.x | Supported (maintenance) | v5.5 is "mostly compatible" with v5.4 apps |
| **v5.5.3** | **Current stable** | Released as bugfix for v5.5. Supported through **Jan 2028**. |
| v6.0-beta2 | Pre-release | Major update with breaking changes. NOT production-ready. |

### v5.5 Key Changes Relevant to Pendant

- **Vendor-specific HCI commands disabled by default** — response to the ESP32 "backdoor" disclosure.
- **New Log v2 mechanism** — structured logging.
- **MbedTLS flow breaking changes** — review if using custom TLS code.
- **v5.5.3 fix**: SPI master/slave no longer accept `ESP_INTR_FLAG_SHARED` during init — relevant for NAND flash SPI driver.

### Recommendation

**Use ESP-IDF v5.5.3** (already specified in SOFTWARE_STACK.md). Clone with:
```bash
git clone --recursive https://github.com/espressif/esp-idf.git -b v5.5.3
```

Do NOT use v6.0 — it's in beta and the breaking changes aren't worth the risk for a hardware product.

### Sources

- [ESP-IDF Versions Documentation (v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/versions.html)
- [ESP-IDF GitHub Releases](https://github.com/espressif/esp-idf/releases)
- [IDF v5.5 Released — Production ESP32](https://productionesp32.com/posts/idf-v5.5-released/)

---

## 2. PDM Microphone Capture (I2S PDM RX) on ESP32-S3

### Hardware Constraints

- **PDM RX is only available on I2S0.** No other I2S port supports it.
- Only **16-bit sample width** is supported in PDM mode.
- ESP32-S3 supports up to **4 data lines** in PDM RX mode (up to 8 mics total). Pendant uses 1 data line, 1 mic.
- There is **no L/R GPIO pin** in the ESP-IDF PDM config struct — must be hard-wired on the PCB. The IM73D122's L/R select is already handled in hardware.

### PDM-to-PCM Conversion

ESP32-S3 has a **hardware PDM-to-PCM converter** on I2S0. Two down-sampling modes:

| Mode | Clock Frequency | Best For |
|------|----------------|----------|
| `I2S_PDM_DSR_8S` | `sample_rate × 64` | Lower clock, adequate for 16 kHz |
| `I2S_PDM_DSR_16S` | `sample_rate × 128` | Better SNR at higher sample rates |

For the pendant (16 kHz mono), `I2S_PDM_DSR_16S` gives `16000 × 128 = 2.048 MHz` PDM clock — well within spec.

### Configuration Code Pattern

```c
#include "driver/i2s_pdm.h"

i2s_chan_handle_t rx_handle;
i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
chan_cfg.dma_desc_num = 6;
chan_cfg.dma_frame_num = 1024;  // 64 ms per buffer at 16 kHz

i2s_new_channel(&chan_cfg, NULL, &rx_handle);

i2s_pdm_rx_config_t pdm_rx_cfg = {
    .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(16000),
    .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                 I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
        .clk = GPIO_NUM_5,  // PDM_CLK
        .din = GPIO_NUM_6,  // PDM_DATA
    },
};
pdm_rx_cfg.clk_cfg.dn_sample_mode = I2S_PDM_DSR_16S;

i2s_channel_init_pdm_rx_mode(rx_handle, &pdm_rx_cfg);
```

### Best Practices

1. **Use the new driver** (`i2s_pdm.h`), not the deprecated legacy `i2s.h`.
2. **Pin audio task to Core 1** — Wi-Fi ISRs on Core 0 cause cache eviction stalls.
3. **Use DMA callbacks** (`i2s_channel_register_event_callback()`) for low-latency async capture. Avoid complex logic in the ISR.
4. **Mark ISR-path functions with `IRAM_ATTR`** — prevents flash cache misses during DMA interrupts.
5. **16 kHz sample rate** is the most stable on ESP32-S3 for PDM; up to 48 kHz works but PDM clock gets tight.
6. **Hard-wire L/R pin** on the microphone — there's no GPIO config for it in the ESP-IDF API.
7. **DMA buffer sizing**: `dma_frame_num=1024, dma_desc_num=6` gives 384 ms total DMA buffering at 16 kHz — enough headroom for Opus encoder blocking.

### Reference Examples

- `peripherals/i2s/i2s_recorder` — records PDM mic to SD card as .wav
- `peripherals/i2s/i2s_basic/i2s_pdm` — basic PDM RX configuration

### Sources

- [I2S Programming Guide (ESP32-S3, v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/i2s.html)
- [PDM Microphone Mystery — atomic14](https://www.atomic14.com/2025/10/10/prm-microphone-mystery)
- [Seeed XIAO ESP32S3 Sense Microphone Usage](https://wiki.seeedstudio.com/xiao_esp32s3_sense_mic/)

---

## 3. WS2812B LEDs via RMT Peripheral

### Official Component: `espressif/led_strip` v3.x

The recommended approach is Espressif's official `led_strip` component from the ESP Component Registry, using the **RMT backend**.

```bash
idf.py add-dependency "espressif/led_strip"
```

### RMT Backend vs SPI Backend

| Backend | Pros | Cons |
|---------|------|------|
| **RMT** (recommended) | Only uses 1 RMT channel. DMA available on ESP32-S3. | Without DMA, high interrupt frequency for large strips. |
| **SPI** | Works on any SPI bus. | Occupies the entire bus — no other SPI devices. Not usable (NAND is on SPI). |

**RMT is the only viable choice** for the pendant since the SPI bus is needed for the W25N02KV NAND flash.

### Configuration (v3.x API)

```c
#include "led_strip.h"

led_strip_handle_t led_strip;

led_strip_config_t strip_config = {
    .strip_gpio_num = GPIO_NUM_8,          // WS2812_DATA
    .max_leds = 8,
    .led_model = LED_MODEL_WS2812,
    .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,  // v3.x field
    .flags.invert_out = false,
};

led_strip_rmt_config_t rmt_config = {
    .clk_src = RMT_CLK_SRC_DEFAULT,
    .resolution_hz = 10 * 1000 * 1000,    // 10 MHz
    .mem_block_symbols = 64,
    .flags.with_dma = true,                // ESP32-S3 supports RMT DMA
};

led_strip_new_rmt_device(&strip_config, &rmt_config, &led_strip);
```

### Best Practices

1. **Enable DMA** (`.flags.with_dma = true`) — ESP32-S3 is the only chip that supports RMT DMA. This prevents corruption when Wi-Fi ISRs preempt the RMT interrupt.
2. **Pin LED task to Core 1** — same rationale as audio: avoid Wi-Fi ISR contention on Core 0.
3. **v3.x API**: Use `color_component_format` (not the older `led_pixel_format` from v2.x).
4. **Drive the load switch first**: Set IO7 LOW (active-low PMOS) before sending any WS2812B data.
5. **Brightness limit ≤ 30%** — WS2812B spec minimum VDD is 3.5 V, but the pendant runs at 3.3 V. Blue/green channels are unreliable above 30% duty cycle.
6. **Prefer warm colors** (red, amber, warm white) for reliable status indication at 3.3 V.
7. **PSRAM caution**: If using PSRAM (ESP32-S3-WROOM-1-N16R8), force internal RAM allocation for RMT buffers to avoid flickering.

### Sources

- [espressif/led_strip v3.0.1 — ESP Component Registry](https://components.espressif.com/components/espressif/led_strip/)
- [LED Strip Documentation](https://espressif.github.io/idf-extra-components/latest/led_strip/index.html)
- [RMT Peripheral (v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/rmt.html)
- [ESP-IDF RMT LED Strip Example](https://github.com/espressif/esp-idf/blob/master/examples/peripherals/rmt/led_strip/README.md)

---

## 4. SPI NAND Flash (W25N02KV) Driver Support

### Official Component: `espressif/spi_nand_flash` — W25N02KV Supported

**The W25N02KV is officially supported.** No custom driver needed.

| Detail | Value |
|--------|-------|
| Component | `espressif/spi_nand_flash` |
| Current version | **v0.17.0** |
| W25N02KV added in | **v0.13.0** |
| Supported variants | W25N02KVxxIR/U |
| Registry URL | [components.espressif.com](https://components.espressif.com/components/espressif/spi_nand_flash) |

### Architecture

```
Application (FATFS)
    ↓
Dhara Library (FTL — wear leveling + bad block management)
    ↓
NAND Flash Layer
    ↓
SPI NAND Flash Driver
    ↓
ESP-IDF SPI Driver
    ↓
Hardware (SPI/QSPI)
```

### Installation

```bash
idf.py add-dependency "espressif/spi_nand_flash^0.17"
```

Or in `idf_component.yml`:
```yaml
dependencies:
  espressif/spi_nand_flash: "^0.17"
```

### Supported Winbond Chips (v0.17.0)

- W25N01GVxxxG/T/R
- W25N512GVxIG/IT
- W25N512GWxxR/T
- W25N01JWxxxG/T
- **W25N02KVxxIR/U** ← pendant's chip
- W25N04KVxxIR/U

Also supports Gigadevice, Alliance, Micron, and Zetta NAND chips.

### Key Configuration

- **Write verification** (development only): Enable via `idf.py menuconfig → Component config → SPI NAND Flash → NAND_FLASH_VERIFY_WRITE`.
- **QSPI init sequence**: Start in Standard SPI mode → write W25N02KV Status Register 3 bit 1 (`SR3[1]=1`) → switch to Quad mode. Reversing this order bricks the flash session.

### Important Notes

- External SPI NAND flash **cannot** be used for code execution — it's data-only storage.
- The Dhara FTL handles wear leveling and bad block management transparently.
- FATFS is layered on top of Dhara for file-level access.

### Sources

- [espressif/spi_nand_flash — ESP Component Registry](https://components.espressif.com/components/espressif/spi_nand_flash)
- [espressif/spi_nand_flash v0.13.0 Changelog](https://components.espressif.com/components/espressif/spi_nand_flash/versions/0.13.0/changelog?language=en)
- [SPI Flash API (v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/spi_flash/index.html)

---

## 5. OTA Firmware Update Best Practices

### Architecture: Dual OTA Partitions

The pendant's partition table already includes `ota_0` and `ota_1` slots. The OTA process writes to whichever slot is NOT currently booted, then updates the OTA data partition to select it for next boot.

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│  Factory │  │  OTA-0   │  │  OTA-1   │
│  (skip)  │  │ (active) │  │ (update) │
└──────────┘  └──────────┘  └──────────┘
                    ↕            ↕
              ┌──────────────────────┐
              │      OTA Data        │
              │  (boot selection)    │
              └──────────────────────┘
```

### Recommended API: `esp_https_ota()`

Use the high-level HTTPS OTA API for encrypted firmware delivery:

```c
#include "esp_https_ota.h"

esp_http_client_config_t http_cfg = {
    .url = "https://ota.honestpuck.com/firmware/latest.bin",
    .cert_pem = server_cert_pem,
};

esp_https_ota_config_t ota_cfg = {
    .http_config = &http_cfg,
    .partial_http_download = true,       // Save ~12 KB RAM
    .max_http_request_size = 4096,       // 4 KB chunks
};

esp_err_t ret = esp_https_ota(&ota_cfg);
if (ret == ESP_OK) {
    esp_restart();
}
```

### Best Practices Checklist

| Practice | Config / Code |
|----------|--------------|
| **HTTPS transport** | Use `esp_https_ota()` with TLS cert pinning |
| **Partial downloads** | `partial_http_download = true, max_http_request_size = 4096` — saves ~12 KB RAM |
| **Rollback support** | `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` |
| **Post-boot validation** | Call `esp_ota_mark_app_valid_cancel_rollback()` after diagnostics pass |
| **Anti-rollback** | `CONFIG_BOOTLOADER_APP_ANTI_ROLLBACK=y` — uses eFuse security versioning |
| **Version check before flashing** | Use `esp_https_ota_get_img_desc()` to read firmware version from image header |
| **Firmware signing** | Secure Boot v2 (RSA-3072 or ECDSA-256) — verify signature during OTA |
| **Binary size monitoring** | CI check: firmware must be < 1.75 MB (0x1C0000, OTA partition size) |
| **Thermal throttling** | Pause OTA if device temperature exceeds 45°C (NTC reading) |

### Rollback Flow

```
1. Download new firmware → write to OTA-1
2. Verify signature + image integrity
3. Update OTA data → select OTA-1 for next boot
4. Reboot into OTA-1
5. Run diagnostics:
   - Can read NAND flash?
   - Can initialize I2S PDM?
   - Can drive WS2812B?
   - Battery voltage in range?
6. If all pass: esp_ota_mark_app_valid_cancel_rollback()
7. If any fail: esp_ota_mark_app_invalid_rollback_and_reboot()
   → automatically rolls back to OTA-0
```

### Sources

- [OTA Updates (ESP32-S3, v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/system/ota.html)
- [ESP HTTPS OTA API (v5.2)](https://docs.espressif.com/projects/esp-idf/en/v5.2/esp32s3/api-reference/system/esp_https_ota.html)

---

## 6. Audio Codec Comparison for On-Device Compression

### Espressif Official Benchmarks (ESP32-S3R8, esp_audio_codec v2.3)

#### Decoder Performance

| Codec | Sample Rate | Channels | Memory (KB) | CPU Loading (%) |
|-------|------------|----------|-------------|-----------------|
| **ADPCM** | 48,000 Hz | Stereo | 0.11 | 2.43% |
| **Opus** | 48,000 Hz | Stereo | 26.6 | 5.86% |
| **FLAC** | 44,100 Hz | Stereo | 89.4 | 8.0% |

#### Encoder Performance (Opus, mono, complexity 1, VoIP mode, 20 ms frame)

| Sample Rate | Memory (KB) | CPU Loading (%) |
|------------|-------------|-----------------|
| 8,000 Hz | 43 | 15.9% |
| 16,000 Hz | 43 | 16.8% |
| 48,000 Hz | 43 | 19.9% |

Dual-channel encoding uses ~1.6× the CPU of mono.

### Comparison Matrix for Pendant Use Case (16 kHz mono speech)

| Factor | **Opus** | **ADPCM** | **FLAC** |
|--------|----------|-----------|----------|
| **Type** | Lossy (advanced psychoacoustic) | Lossy (simple waveform) | Lossless |
| **Compression** | ~16:1 at 16 kbps | ~4:1 fixed | ~2:1 typical |
| **Encoding CPU (ESP32-S3)** | 16.8% (complexity 1) | Very low (~2%) | **Not officially supported** |
| **Decoding CPU** | 5.86% | 2.43% | 8.0% |
| **Encoding Memory** | 43 KB heap + 30 KB stack | < 1 KB | N/A (no official encoder) |
| **Quality at speech** | Excellent (optimized for speech) | Mediocre (audible artifacts) | Perfect |
| **Storage (35 hr target)** | ~252 MB (fits 256 MB NAND) | ~1.01 GB (**does NOT fit**) | ~2+ GB (**does NOT fit**) |
| **ESP-IDF official support** | Encode + Decode | Encode + Decode | **Decode only** |

### Verdict: Opus Is the Right Choice

**Opus at 16 kHz mono, complexity 1, VoIP mode** is correct for the pendant:

- **16.8% CPU on one core** — leaves plenty of headroom for DMA, encryption, and NAND writes on Core 1.
- **~16 kbps** → 35 hours on 256 MB NAND. ADPCM would need 1 GB. FLAC would need 2+ GB. Neither fits.
- **Excellent speech quality** at low bitrates — Opus was literally designed for this.
- **Royalty-free** — no licensing concerns.

### Implementation Notes

- **Use fixed-point build** (`FIXED_POINT=1`) — ESP32's FPU is too slow for Opus math.
- **Allocate 30 KB stack** for the audio FreeRTOS task — Opus uses `alloca()` heavily.
- **Frame size: 20 ms** — standard for low-latency speech.
- **Complexity ≤ 2** — complexity 1 is the sweet spot. Going higher gives marginal quality gain but significant CPU increase.
- **VBR mode** at quality 5 gives ~16 kbps average for speech.
- Use `espressif/esp_audio_codec` (v2.3+) for official Opus support, or `esp-libopus` for direct libopus access.
- libopus 1.6 was released December 2025 — includes improved packet loss concealment via deep learning (relevant if streaming, not for local recording).

### Alternative: Hybrid Approach

For maximum flexibility, consider recording raw PCM to a circular buffer and encoding Opus in a lower-priority background task. This decouples capture latency from encoding latency. However, the 16.8% CPU figure at complexity 1 suggests inline encoding is feasible.

### Sources

- [espressif/esp_audio_codec v2.3.0 — ESP Component Registry](https://components.espressif.com/components/espressif/esp_audio_codec)
- [esp-libopus — GitHub](https://github.com/XasWorks/esp-libopus)
- [Opus Encoding on ESP32 — Performance Data](https://www.pschatzmann.ch/home/2022/05/06/audio-streaming-the-opus-codec/)
- [Audio Encoders/Decoders for Microcontrollers — Phil Schatzmann](https://www.pschatzmann.ch/home/2021/08/13/audio-decoders-for-microcontrollers/)
- [FLAC Codec for Arduino Audio Tools](https://www.pschatzmann.ch/home/2022/05/14/a-flac-codec-for-the-arduino-audio-tools/)

---

## 7. Power Management / Deep Sleep Best Practices

### Sleep Mode Comparison (ESP32-S3)

| Mode | Current | What's Active | Wake Latency |
|------|---------|--------------|--------------|
| **Active** | 40–240 mA | Everything | N/A |
| **Modem-sleep** | ~20 mA | CPU, peripherals (Wi-Fi off) | < 1 ms |
| **Light-sleep** | ~0.2–2 mA | RTC, ULP optional | ~1 ms |
| **Deep-sleep** | 10–150 µA | RTC memory, RTC timer, optional ULP | ~10 ms (full reboot) |
| **Hibernation** | ~5 µA | RTC timer only | ~10 ms (full reboot) |

### Pendant Deep Sleep Budget: 66 µA

Achieving 66 µA requires careful configuration:

#### Entry Sequence (Critical Order)

```c
// 1. Stop Wi-Fi EXPLICITLY (deep_sleep_start doesn't do this gracefully)
esp_wifi_stop();

// 2. Gate peripherals OFF via PMOS load switches
gpio_set_level(GPIO_NUM_4, 1);  // MIC_ENABLE_N → OFF (pull-up does this in Hi-Z)
gpio_set_level(GPIO_NUM_7, 1);  // LED_VDD_EN_N → OFF

// 3. Flush NAND write buffer
// (ensure all pending FATFS writes are committed)
fflush(recording_file);
fclose(recording_file);

// 4. Turn off ADC (critical — draws 40+ µA if left enabled)
adc_oneshot_del_unit(adc_handle);

// 5. Isolate ALL unused RTC GPIOs (internal pull-ups leak 100+ µA)
for (int i = 0; i < SOC_RTCIO_PIN_COUNT; i++) {
    if (i != RTC_GPIO_FOR_WAKEUP) {
        rtc_gpio_isolate(rtc_gpio_number);
    }
}

// 6. Power down unnecessary RTC domains
esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_OFF);
esp_sleep_pd_config(ESP_PD_DOMAIN_RTC8M, ESP_PD_OPTION_OFF);

// 7. Configure wake source (IO0 = MAIN button, RTC-capable)
esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0);  // Wake on LOW

// 8. Enter deep sleep
esp_deep_sleep_start();
```

#### Wake Sources for Pendant

| Source | Config | Use Case |
|--------|--------|----------|
| **EXT0 (IO0)** | `esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0)` | Main button press (primary) |
| **Timer** | `esp_sleep_enable_timer_wakeup(µs)` | Periodic battery check (optional) |
| **ULP** | `esp_sleep_enable_ulp_wakeup()` | Low-battery monitoring via ADC (advanced) |

### Common Pitfalls

1. **ADC leaks current in deep sleep** — a developer found 50 µA instead of 8 µA because the ADC was enabled before sleep. Always deinit the ADC.
2. **Floating RTC GPIOs** — internal pull-ups on un-isolated RTC pins leak 100+ µA. Call `rtc_gpio_isolate()` on every unused RTC GPIO.
3. **Wi-Fi not stopped** — `esp_deep_sleep_start()` doesn't cleanly shut down the Wi-Fi stack. Call `esp_wifi_stop()` first.
4. **Dev board peripherals** — voltage regulators and onboard LEDs on dev boards draw mA. Test final current on the actual pendant PCB, not the devkit.
5. **Deep sleep = full reboot** — all RAM is lost. Use `RTC_DATA_ATTR` for variables that must survive (e.g., recording count, wake reason tracking).

### Battery Life Estimates (400 mAh LiPo)

| Mode | Current | Duration |
|------|---------|----------|
| Active recording (PDM + Opus + NAND) | ~80 mA (est.) | ~5 hours |
| Wi-Fi sync (TX active) | ~150 mA (est.) | ~2.7 hours |
| Deep sleep (66 µA) | 0.066 mA | **252 days** |
| Hibernation (5 µA) | 0.005 mA | **3.3 years** |

### Sources

- [Sleep Modes (ESP32-S3, v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/system/sleep_modes.html)
- [ESP32-S3 ULP Power Consumption — ESP32 Forum](https://www.esp32.com/viewtopic.php?t=43804)
- [Deep Sleep Low Power Optimization](https://www.espboards.dev/blog/esp32-power-optimisation/)
- [ESP32 Sleep Modes — Arrow Electronics](https://www.arrow.com/en/research-and-events/articles/esp32-power-consumption-can-be-reduced-with-sleep-modes)
- [ESP32 Sleep Modes & Power Consumption — Last Minute Engineers](https://lastminuteengineers.com/esp32-sleep-modes-power-consumption/)

---

## Summary: Answers to All 7 Questions

| # | Question | Answer |
|---|----------|--------|
| 1 | Latest stable ESP-IDF? | **v5.5.3** (v6.0 is in beta). Stick with v5.5.3 for production. |
| 2 | PDM mic best practices? | Use new `i2s_pdm.h` driver on I2S0, 16 kHz mono, 16-bit. Pin to Core 1. DMA callbacks. `IRAM_ATTR` on ISR paths. |
| 3 | WS2812B via RMT? | Use `espressif/led_strip` v3.x with RMT backend. **Enable DMA** (`.flags.with_dma = true`). Pin to Core 1. Brightness ≤ 30% at 3.3 V. |
| 4 | W25N02KV driver? | **Officially supported** via `espressif/spi_nand_flash` (since v0.13.0, current v0.17.0). No custom driver needed. |
| 5 | OTA best practices? | Dual partition (OTA-0/OTA-1), `esp_https_ota()`, rollback with post-boot validation, anti-rollback via eFuse, firmware signing, partial HTTP downloads. |
| 6 | Audio codec? | **Opus** is the clear winner — 16 kbps @ 16 kHz mono uses 16.8% CPU. ADPCM can't fit on 256 MB NAND. FLAC encoding isn't supported. |
| 7 | Deep sleep? | 66 µA achievable: stop Wi-Fi, gate load switches, deinit ADC, isolate RTC GPIOs, power down unused RTC domains. Wake on IO0 (EXT0). |

All findings are consistent with the decisions already documented in SOFTWARE_STACK.md. The current architecture is well-aligned with the state of ESP-IDF v5.5.3 and its component ecosystem.
