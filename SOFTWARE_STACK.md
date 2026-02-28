# Honest Puck — Software Stack

Everything you need to turn the v3 PCB into a shipping privacy-first
wearable microphone pendant — from silicon to cloud.

---

## Table of Contents

1. [What Exists Today](#what-exists-today)
2. [Layer 0 — PCB Fabrication & Assembly](#layer-0--pcb-fabrication--assembly)
3. [Layer 1 — Firmware (ESP32-S3)](#layer-1--firmware-esp32-s3)
4. [Layer 2 — Companion App (Phone)](#layer-2--companion-app-phone)
5. [Layer 3 — Cloud Backend (Optional)](#layer-3--cloud-backend-optional)
6. [Layer 4 — CI/CD & DevOps](#layer-4--cicd--devops)
7. [Layer 5 — Mechanical & Enclosure](#layer-5--mechanical--enclosure)
8. [Full Dependency Map](#full-dependency-map)
9. [Development Environment Setup](#development-environment-setup)
10. [Key Technical Constraints](#key-technical-constraints)
11. [Bill of Materials (Key ICs)](#bill-of-materials-key-ics)
12. [Summary & Roadmap](#summary--roadmap)

---

## What Exists Today

```
pendant/
├── ARCHITECTURE.md              ← Hardware reference (power, GPIO, thermals)
├── SOFTWARE_STACK.md            ← This file
├── netlist/
│   ├── __init__.py
│   └── honest_puck_v3.py       ← SKiDL netlist — generates KiCad .net
└── tests/
    ├── __init__.py
    └── test_honest_puck_v3.py   ← 91 validation tests (AST-based, no HW needed)
```

**Current toolchain:**

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.10+ | Runtime for netlist generation and tests |
| SKiDL | latest (`pip install skidl`) | Hardware description language — outputs KiCad netlists |
| pytest | latest (`pip install pytest`) | Validates design invariants without KiCad installed |
| KiCad | 7 or 8 | Schematic capture, PCB layout, Gerber export (manual step) |

```bash
# Generate netlist
pip install skidl
python3 netlist/honest_puck_v3.py     # → honest_puck_v3.net

# Run design validation
pip install pytest
python3 -m pytest tests/ -v           # 91 tests, no hardware required
```

---

## Layer 0 — PCB Fabrication & Assembly

The SKiDL netlist defines the schematic. These are the steps to get a
physical PCB in your hands.

### Workflow

```
honest_puck_v3.py  ──SKiDL──►  .net file  ──KiCad import──►  schematic
    │                                                             │
    │                                                        DRC / ERC
    │                                                             │
    │                                                        PCB layout
    │                                                        (50 mm circular)
    │                                                             │
    │                                                        Gerber export
    │                                                        (RS-274X)
    │                                                             │
    └──────────────────────────────────────────────────►    Fab + assembly
```

### Tools & Services

| Need | Tool / Service | Notes |
|------|----------------|-------|
| Schematic → PCB layout | **KiCad 8** | Route traces from the imported netlist |
| Custom symbol library | **HonestPuck_Lib** | ESP32-S3-WROOM-1, IM73D122, W25N02KV, TP4056, TPS63031 (see ARCHITECTURE.md §Custom KiCad Library) |
| Gerber / drill export | KiCad → Gerber | Standard RS-274X format |
| BOM generation | KiCad BOM plugin or `kicost` | Map refs to Digi-Key / LCSC / Mouser part numbers |
| PCB fabrication | **JLCPCB** / PCBWay / OSH Park | 4-layer recommended for EMI control (ESP32 RF + buck-boost switching); 50 mm circular outline |
| SMT assembly | **JLCPCB Assembly** / hand solder | All components are SMD: 0402, 0603, WSON-8, WSON-10, SOIC-8, PLCC-4, SOT-23 |
| Stencil | Stainless 0.12 mm | Needed for WSON-10 (TPS63031) exposed pad and WSON-8 (W25N02KV) |

### PCB Layout Constraints

| Constraint | Reason |
|------------|--------|
| TPS63031 inductor (2.2 µH) within 2 mm of L1/L2 pads | TI layout guide — minimizes EMI and ringing |
| Solid ground plane under ESP32-S3 antenna keep-out zone | RF performance — no copper pour under the antenna |
| W25N02KV decoupling cap within 1 mm of VCC pad | NAND flash signal integrity at 104 MHz |
| IM73D122 acoustic port unobstructed on bottom side | Bottom-port MEMS — needs clear path through PCB |
| 4-layer stack-up: SIG / GND / PWR / SIG | Clean return paths for PDM clock (3.072 MHz) and SPI (up to 104 MHz) |

---

## Layer 1 — Firmware (ESP32-S3)

This is the largest missing piece. The pendant needs firmware running on the
ESP32-S3-WROOM-1 to do anything useful.

### Framework Choice

| Option | Verdict |
|--------|---------|
| **ESP-IDF v5.5.x** (currently v5.5.3) | **Use this.** Full FreeRTOS, mature I2S PDM / SPI / RMT drivers, official SPI NAND component, OTA support, deep sleep control. Supported through Jan 2028. v6.0 is in beta but not production-ready. |
| Arduino-ESP32 | Weaker QSPI NAND and deep-sleep control. Acceptable for prototyping only. |
| MicroPython / CircuitPython | Too slow for 3.072 MHz PDM capture. Not viable. |

### Project Structure

```
firmware/
├── CMakeLists.txt
├── sdkconfig.defaults              ← ESP-IDF Kconfig overrides
├── partitions.csv                  ← Flash partition table (NVS, app, OTA-0, OTA-1, FATFS)
├── main/
│   ├── CMakeLists.txt
│   ├── app_main.c                  ← Entry point, FreeRTOS task orchestration
│   ├── Kconfig.projbuild           ← Project-specific menuconfig options
│   │
│   ├── audio/
│   │   ├── audio_capture.c         ← PDM mic → I2S DMA → ring buffer
│   │   ├── audio_capture.h
│   │   ├── audio_codec.c           ← Opus encoding (16 kHz mono → ~16 kbps)
│   │   └── audio_codec.h
│   │
│   ├── storage/
│   │   ├── flash_storage.c         ← W25N02KV NAND: Dhara FTL + FATFS
│   │   ├── flash_storage.h
│   │   ├── recording_manager.c     ← Recording lifecycle (start → chunk → stop → index)
│   │   └── recording_manager.h
│   │
│   ├── privacy/
│   │   ├── privacy_interlock.c     ← IO4 (MIC_ENABLE_N) PMOS gate control
│   │   └── privacy_interlock.h     ← Hardware guarantee: mic ON ↔ red LED ON
│   │
│   ├── ui/
│   │   ├── led_ring.c              ← WS2812B × 8 via RMT peripheral (IO8)
│   │   ├── led_ring.h
│   │   ├── button_handler.c        ← GPIO ISR + software debounce (IO0/IO1/IO9)
│   │   └── button_handler.h
│   │
│   ├── power/
│   │   ├── power_manager.c         ← Deep sleep entry/exit, wake source config
│   │   ├── power_manager.h
│   │   ├── battery_monitor.c       ← ADC reading of VBAT via resistor divider
│   │   └── battery_monitor.h
│   │
│   ├── network/
│   │   ├── wifi_sync.c             ← Wi-Fi STA mode, HTTPS upload, mDNS
│   │   ├── wifi_sync.h
│   │   ├── ota_update.c            ← OTA firmware updates via HTTPS
│   │   └── ota_update.h
│   │
│   └── util/
│       ├── event_bus.c             ← FreeRTOS event group for inter-task signalling
│       └── event_bus.h
│
├── components/
│   ├── opus/                       ← Opus codec (libopus, compiled for Xtensa)
│   │   └── CMakeLists.txt
│   └── led_strip/                  ← Espressif led_strip component (RMT backend)
│       └── CMakeLists.txt
│
└── test/                           ← Unity-based on-target tests
    └── test_privacy_interlock.c
```

### Critical Driver Details

| Subsystem | ESP-IDF API | GPIOs | Implementation Notes |
|-----------|-------------|-------|----------------------|
| **PDM Microphone** | `i2s_new_channel()` + `i2s_channel_init_pdm_rx_mode()` | IO5 (CLK), IO6 (DATA) | **Must use I2S0** (only port supporting PDM RX). 16-bit @ 16 kHz mono. Use `I2S_PDM_RX_SLOT_PCM_FMT_DEFAULT_CONFIG` for hardware PDM→PCM conversion. DMA double-buffer via `i2s_channel_register_event_callback()` (preferred over blocking reads). Pin audio task to Core 0 to avoid Wi-Fi ISR jitter. |
| **QSPI NAND Flash** | `espressif/spi_nand_flash` component (v0.17+) | IO10 (CS), IO11 (MOSI/IO0), IO12 (CLK), IO13 (MISO/IO1), IO2 (WP/IO2), IO3 (HOLD/IO3) | **W25N02KV is officially supported** (W25N02KVxxIR/U listed since v0.14+). Architecture: App → FATFS → Dhara FTL (wear leveling + bad block mgmt) → SPI NAND driver → ESP-IDF SPI. Init: Standard SPI first → set SR3[1]=1 → Quad mode. Enable `NAND_FLASH_VERIFY_WRITE` in menuconfig during development. Add with `idf.py add-dependency "espressif/spi_nand_flash^0.17"`. |
| **WS2812B LEDs** | `espressif/led_strip` component v3.x (RMT backend) | IO8 (DATA), IO7 (load switch) | Drive IO7 LOW first to power the ring via PMOS. **ESP32-S3 is the only chip with RMT DMA** — set `.flags.with_dma = true` for reliable operation alongside Wi-Fi. Use `rmt_transmit()` (v5.x encoder API, not deprecated `rmt_write_items()`). Pin LED task to Core 1. Keep brightness ≤ 30% at 3.3 V (WS2812B VDD_min is 3.5 V). `idf.py add-dependency "espressif/led_strip"`. |
| **Buttons** | `gpio_install_isr_service()` + `esp_timer` | IO0 (MAIN), IO1 (SYNC), IO9 (BATT) | 20 ms software debounce via one-shot `esp_timer`. IO0 is RTC-capable: use `esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0)` for deep sleep wake. |
| **Mic Load Switch** | `gpio_set_level(GPIO_NUM_4, 0)` | IO4 (MIC_ENABLE_N) | Active-low PMOS gate. 10 kΩ HW pull-up forces OFF during deep sleep (IO4 → Hi-Z). Never disable LED independently of mic — they share HONEST_MIC_PWR. |
| **LED Load Switch** | `gpio_set_level(GPIO_NUM_7, 0)` | IO7 (LED_VDD_EN_N) | Same PMOS topology. Must drive LOW before any WS2812B data transmission. |
| **Deep Sleep** | `esp_deep_sleep_start()` | — | Before sleeping: (1) `esp_wifi_stop()` explicitly (deep_sleep_start does NOT do this gracefully), (2) gate IO4/IO7 HIGH via Hi-Z + pull-ups, (3) flush NAND write buffer, (4) call `rtc_gpio_isolate()` on all unused RTC GPIOs (internal pull-ups leak 100+ µA if left floating), (5) power down RTC peripherals via `esp_sleep_pd_config()`. Wake on IO0 (`esp_sleep_enable_ext0_wakeup`). Use `RTC_DATA_ATTR` for variables that must survive deep sleep. Budget: 66 µA total. |
| **Wi-Fi** | `esp_wifi_start()` STA mode | Internal antenna | Connect to user's AP. Use `esp_https_server` for local companion pairing, or `esp_http_client` for cloud upload. Throttle if TP4056 is charging (thermal ceiling: 1.61 W). |
| **OTA Updates** | `esp_https_ota()` | — | Dual OTA partition scheme (OTA-0 / OTA-1). Enable `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` — call `esp_ota_mark_app_valid_cancel_rollback()` after diagnostics pass. Enable anti-rollback via eFuse security versioning. Use `partial_http_download = true` to save ~12 KB RAM. Sign with Secure Boot v2 (RSA-3072 or ECDSA-256). |

### Audio Pipeline

```
IM73D122 (PDM) ──► I2S PDM RX (DMA) ──► PCM ring buffer (32 KB)
                                              │
                                         Opus encoder
                                        (16 kHz mono, ~16 kbps)
                                              │
                                         NAND flash (FATFS)
                                        /recordings/YYYYMMDD_HHMMSS.opus
                                              │
                                         Wi-Fi sync ──► phone / cloud
```

**Storage math (2 Gb = 256 MB NAND):**

| Format | Bitrate | Recording Time |
|--------|---------|----------------|
| Raw PCM (16 kHz, 16-bit mono) | 256 kbps (32 KB/s) | ~2.2 hours |
| Opus VBR @ quality 5 | ~16 kbps (2 KB/s) | **~35 hours** |
| Opus VBR @ quality 3 | ~12 kbps (1.5 KB/s) | **~47 hours** |

Opus is the clear winner — it's royalty-free, handles speech extremely well at
low bitrates, and Espressif provides an official `esp_audio_codec` component
(v2.3+) with native Opus support. On ESP32-S3 (Xtensa LX7 @ 240 MHz), 16 kHz
mono encoding at complexity 1–2 runs comfortably in real-time on a single core
(~30–40% CPU utilisation). Alternative: `esphome/micro-opus` (lightweight,
benchmarks show 22.8x real-time decode on S3). Add with:
`idf.py add-dependency "espressif/esp_audio_codec"`.

### Partition Table

```csv
# Name,    Type, SubType, Offset,   Size,    Flags
nvs,       data, nvs,     0x9000,   0x6000,
phy_init,  data, phy,     0xf000,   0x1000,
otadata,   data, ota,     0x10000,  0x2000,
ota_0,     app,  ota_0,   0x20000,  0x1C0000,
ota_1,     app,  ota_1,   0x1E0000, 0x1C0000,
```

(External W25N02KV handles all audio storage via SPI — internal flash is
only used for firmware, NVS, and OTA.)

### FreeRTOS Task Architecture

```
┌──────────────────────────────────────────────────┐
│                  FreeRTOS (2 cores)               │
│                                                    │
│  Core 0 (PRO_CPU):                                │
│    ├── audio_task    (priority 10, pinned)         │
│    │   PDM capture → Opus encode → NAND write     │
│    └── button_task   (priority 5)                  │
│        GPIO ISR → debounce → state machine        │
│                                                    │
│  Core 1 (APP_CPU):                                │
│    ├── wifi_task     (priority 8)                  │
│    │   mDNS, HTTPS sync, OTA                      │
│    ├── led_task      (priority 3)                  │
│    │   Animation state → RMT write                │
│    └── power_task    (priority 2)                  │
│        Battery ADC, thermal throttle, sleep entry │
│                                                    │
│  Inter-task: FreeRTOS event groups + queues        │
└──────────────────────────────────────────────────┘
```

Pin audio capture to Core 0 to avoid Wi-Fi ISR jitter on the I2S DMA.

### Build, Flash & Monitor

```bash
# 1. Install ESP-IDF v5.5+ (v5.5 recommended)
git clone --recursive https://github.com/espressif/esp-idf.git -b v5.5
cd esp-idf && ./install.sh all && source export.sh

# 2. Create project and add dependencies
cd firmware/
idf.py add-dependency "espressif/spi_nand_flash^0.17"
idf.py add-dependency "espressif/led_strip"

# 3. Configure target
idf.py set-target esp32s3

# 4. Configure (optional — sdkconfig.defaults should cover most settings)
idf.py menuconfig
# → Component config → SPI NAND Flash → enable NAND_FLASH_VERIFY_WRITE (debug only)
# → Component config → Wi-Fi → enable STA mode
# → Component config → ESP System Settings → CPU freq = 240 MHz

# 5. Build + Flash + Monitor
idf.py build
idf.py -p /dev/ttyACM0 flash monitor    # USB-JTAG built into ESP32-S3
```

---

## Layer 2 — Companion App (Phone)

The pendant records audio locally. A companion app pulls recordings off
the device over the local Wi-Fi network.

### Framework Choice

| Option | Verdict |
|--------|---------|
| **Flutter** | **Recommended.** Best BLE library stability (`flutter_blue_plus`), strong ESP32 community examples, dominant 46% market share (2026). Single codebase compiles to native ARM via Impeller renderer. `just_audio` + `just_audio_background` for Opus playback with lock-screen controls. `drift` (SQLite) for type-safe local storage. |
| React Native + Expo | Strong alternative if team is JS/TS-native. `react-native-ble-plx` for BLE, `react-native-track-player` for audio. Expo EAS Update enables OTA JS pushes without app store review. BLE requires dev builds (not Expo Go). |
| Kotlin Multiplatform | Maturing (18% adoption in 2026) but BLE/IoT library ecosystem still immature vs Flutter/RN. Better for large enterprise apps, overkill for a 4-screen companion. |

### Architecture

```
┌─────────────────────────────────────────────────┐
│               Companion App                      │
│                                                   │
│  ┌───────────┐  ┌────────────┐  ┌──────────────┐ │
│  │  Pairing  │  │ Recordings │  │   Settings   │ │
│  │  Screen   │  │   Screen   │  │    Screen    │ │
│  └─────┬─────┘  └──────┬─────┘  └──────┬───────┘ │
│        │               │               │         │
│  ┌─────▼───────────────▼───────────────▼───────┐ │
│  │            State Management                  │ │
│  │         (Zustand or Riverpod)                │ │
│  └─────────────────┬───────────────────────────┘ │
│                    │                              │
│  ┌─────────────────▼───────────────────────────┐ │
│  │           Device Service Layer               │ │
│  │  • BLE pairing (Wi-Fi credential exchange)   │ │
│  │  • mDNS discovery (_honestpuck._tcp)         │ │
│  │  • HTTPS file transfer (chunked download)    │ │
│  │  • Device status polling (battery, storage)  │ │
│  └─────────────────┬───────────────────────────┘ │
│                    │                              │
│  ┌─────────────────▼───────────────────────────┐ │
│  │           Local Storage                      │ │
│  │  SQLite (op-sqlite / drift)                  │ │
│  │  • Recording metadata + sync state           │ │
│  │  • Audio files in app sandbox                │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### Key Libraries (Flutter — recommended)

| Purpose | Library | Notes |
|---------|---------|-------|
| BLE (pairing) | `flutter_blue_plus` | More stable than RN equivalents across Android/iOS device combinations |
| mDNS discovery | `bonsoir` or `nsd` | Find pendant via `_honestpuck._tcp` on local network |
| HTTPS client | `dio` | Interceptors, retry logic, chunked downloads |
| Audio playback | `just_audio` + `just_audio_background` | Opus/FLAC support, background playback, lock screen controls, seeking, speed adjust |
| Local DB | `drift` (SQLite) | Type-safe, reactive, code-generated from schema |
| State management | `riverpod` | Mature, testable, compile-time safety |
| File system | `path_provider` + `dart:io` | Save downloaded .opus files to app sandbox |

### Key Libraries (React Native + Expo — alternative)

| Purpose | Library | Notes |
|---------|---------|-------|
| BLE (pairing) | `react-native-ble-plx` | Requires Expo dev build (not Expo Go). One-time BLE handshake to exchange Wi-Fi PSK |
| mDNS discovery | `react-native-zeroconf` | Find pendant on local network via `_honestpuck._tcp` |
| HTTPS client | `axios` or `fetch` | TLS 1.3 for file transfer |
| Audio playback | `react-native-track-player` | Gold standard for audio apps. Opus decoding, background playback, lock screen controls |
| Local DB | `op-sqlite` (fastest RN SQLite via JSI) | Recording index, sync state, device metadata |
| State management | `zustand` | Lightweight, no boilerplate |
| File system | `expo-file-system` or `react-native-fs` | Save downloaded .opus files to app sandbox |

### Screens

| Screen | Purpose | Key Interactions |
|--------|---------|------------------|
| **Onboarding / Pairing** | First-time setup. BLE scan → find pendant → exchange Wi-Fi credentials → verify mDNS connection | One-time flow, stored in SQLite |
| **Recordings** | List synced audio files. Playback with waveform. Swipe to delete. Filter by date. | Pull-to-refresh triggers sync |
| **Device Status** | Battery %, NAND storage used/free, firmware version, recording count on device | Auto-refresh on screen focus |
| **Settings** | Wi-Fi config, audio quality (Opus bitrate), auto-sync toggle, privacy policy link, firmware update trigger | Persisted in SQLite |

### Pairing Flow (BLE → Wi-Fi Handoff)

```
Phone                          Pendant (ESP32-S3)
  │                                   │
  │──── BLE scan ────────────────────►│
  │◄─── Advertise "HonestPuck-XXXX" ─│
  │                                   │
  │──── BLE connect ─────────────────►│
  │──── Write Wi-Fi SSID + PSK ──────►│  (encrypted BLE characteristic)
  │                                   │
  │     ESP32 connects to Wi-Fi       │
  │     ESP32 starts mDNS             │
  │                                   │
  │◄─── BLE notify: "Wi-Fi OK" ──────│
  │──── BLE disconnect ──────────────►│
  │                                   │
  │──── mDNS resolve ────────────────►│  (_honestpuck._tcp → 192.168.x.x)
  │◄─── HTTPS handshake ─────────────│
  │                                   │
  │     All future comms via Wi-Fi    │
```

---

## Layer 3 — Cloud Backend (Optional)

Only needed if recordings should leave the phone (cloud backup,
transcription, multi-device access). A privacy-first device should
default to local-only with cloud as an explicit opt-in.

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Cloud Backend                        │
│                                                           │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────┐ │
│  │   API        │    │   Worker     │    │  Object     │ │
│  │   (FastAPI)  │◄──►│   (Celery /  │◄──►│  Storage    │ │
│  │              │    │   Dramatiq)  │    │  (S3 / R2)  │ │
│  └──────┬───────┘    └──────┬───────┘    └─────────────┘ │
│         │                   │                             │
│  ┌──────▼───────┐    ┌──────▼───────┐                    │
│  │  PostgreSQL  │    │   Whisper /  │                    │
│  │  (metadata)  │    │   Deepgram   │                    │
│  │              │    │   (STT)      │                    │
│  └──────────────┘    └──────────────┘                    │
│                                                           │
│  Auth: JWT (access) + refresh tokens                     │
│  Encryption: AES-256-GCM at rest, TLS 1.3 in transit    │
└─────────────────────────────────────────────────────────┘
```

### Stack

| Component | Technology | Why |
|-----------|------------|-----|
| API framework | **FastAPI** (Python 3.12+) | Async, auto-generated OpenAPI docs, Pydantic v2 validation. Best fit since the netlist tooling is already Python. |
| Database | **PostgreSQL 16** | User accounts, device registry, recording metadata, transcriptions. Use `asyncpg` for async access. |
| Object storage | **Cloudflare R2** (~$0.015/GB/mo, **zero egress**) or **AWS S3** ($0.023/GB/mo + egress) | Audio generates heavy egress (playback, transcription fetch). R2's zero egress saves significant money at scale. S3 for ecosystem maturity and compliance. MinIO for full data sovereignty (self-hosted, S3-compatible). |
| Task queue | **Dramatiq + Redis** | ~10x faster than RQ, actor-based model with built-in retries and rate limiting. Simpler than Celery. Upgrade to Celery only if you need Canvas workflows (chains/groups/chords). Never use pickle serialisation — enforce JSON. |
| Transcription | **Deepgram Nova-3** (API, $0.0043/min batch) or **Whisper large-v3-turbo** (self-hosted, free) | Deepgram: sub-300 ms latency, built-in diarization, best speed/cost/accuracy. Whisper: self-hosted = full data sovereignty, 6x faster than full model with ~1–2% accuracy loss. GPT-4o-Transcribe has best raw accuracy but higher cost ($6/1K min). |
| Auth | **JWT** (PyJWT) + refresh tokens | Standard bearer token auth. Optional: passkey/WebAuthn for passwordless. |
| Hosting | **Fly.io** or **Railway** | Container-based, global edge, easy PostgreSQL managed add-on. Fly for production, Railway for prototyping. |
| Monitoring | **Sentry** (errors) + **Prometheus/Grafana** (metrics) | Catch firmware OTA failures, track sync reliability. |

### API Endpoints (Core)

```
POST   /auth/register              Create account
POST   /auth/login                 Get JWT + refresh token
POST   /auth/refresh               Refresh JWT

POST   /devices                    Register a pendant (device_id, fw_version)
GET    /devices                    List user's pendants
PATCH  /devices/:id                Update device metadata

POST   /recordings/upload          Upload encrypted .opus blob + metadata
GET    /recordings                 List recordings (paginated)
GET    /recordings/:id/audio       Download audio blob
GET    /recordings/:id/transcript  Get transcription text
DELETE /recordings/:id             Delete recording + blob

POST   /firmware/check             Check for OTA update (device_id, current_version)
GET    /firmware/:version/binary   Download signed firmware binary
```

### E2E Encryption (Privacy-First)

For a device whose core promise is privacy, encryption should be non-negotiable:

```
Phone generates:
  • X25519 key pair (device-bound, stored in Keychain / Keystore)
  • AES-256-GCM symmetric key per recording

Encryption flow:
  Audio (.opus) ──► AES-256-GCM encrypt ──► Upload to S3/R2
  AES key ──► Wrap with X25519 public key ──► Store in PostgreSQL

Only the phone (with private key) can decrypt recordings.
Server never sees plaintext audio.
Transcription: decrypt on phone → send to Whisper API → discard.
(Or: user opts into server-side transcription = explicit trust grant.)
```

---

## Layer 4 — CI/CD & DevOps

### GitHub Actions Pipeline

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  netlist-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install skidl pytest
      - run: python3 -m pytest tests/ -v
      - run: python3 netlist/honest_puck_v3.py  # verify netlist generates

  firmware-build:
    runs-on: ubuntu-latest
    container: espressif/idf:v5.5
    steps:
      - uses: actions/checkout@v4
      - run: |
          cd firmware
          idf.py set-target esp32s3
          idf.py build
      - uses: actions/upload-artifact@v4
        with:
          name: firmware-binary
          path: firmware/build/*.bin

  firmware-size-check:
    needs: firmware-build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: firmware-binary
      - run: |
          SIZE=$(stat -c%s honest_puck_fw.bin)
          MAX=$((1835008))  # 0x1C0000 = 1.75 MB (OTA partition size)
          if [ "$SIZE" -gt "$MAX" ]; then
            echo "FAIL: firmware ${SIZE} bytes > OTA partition ${MAX} bytes"
            exit 1
          fi

  companion-app-lint:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: app
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck

  backend-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
        ports: ["5432:5432"]
      redis:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: |
          cd backend
          pip install -e ".[test]"
          pytest -v
```

### Release Flow

```
feature branch ──► PR ──► CI green ──► merge to main
                                            │
                                    tag vX.Y.Z (firmware)
                                            │
                              GitHub Release with signed .bin
                                            │
                              OTA server picks up new version
                                            │
                    Pendants check /firmware/check on next sync
                                            │
                              Download + verify + flash OTA-1
                                            │
                              Reboot into new firmware ✓
```

---

## Layer 5 — Mechanical & Enclosure

| Need | Tool / Approach | Notes |
|------|-----------------|-------|
| 3D modeling | **Fusion 360** or **FreeCAD** | 50 mm diameter × 15 mm thick puck shape |
| Silicone mold design | 2-part mold (3D printed master → silicone cast) | Shore A 30–40 for wearable comfort |
| Acoustic port | Open channel through silicone aligned with IM73D122 bottom port | Must not attenuate > 3 dB at speech frequencies |
| Button caps | Raised silicone bumps over tactile switches | Clearly tactile through overmold |
| LED light pipe | Translucent silicone over WS2812B ring area | Diffuse enough to avoid individual pixel visibility |
| Lanyard / clip attachment | Integrated silicone loop or magnetic clasp | Must support ~20 g pendant weight |
| Waterproofing | Silicone overmold provides IPX4 (splash-proof) naturally | USB-C port needs a silicone flap or gasket |
| Thermal management | Copper pad on PCB under TPS63031 + via stitching to ground plane | Conduct heat away from LiPo cell |

---

## Full Dependency Map

```
┌──────────────────────────────────────────────────────────────┐
│                      CLOUD (optional, opt-in)                 │
│                                                                │
│   FastAPI ←→ PostgreSQL ←→ S3/R2 blob store                  │
│      ↑              ↑                                          │
│      │ HTTPS         │ Dramatiq                                │
│      ↓               ↓                                         │
│   JWT Auth      Whisper/Deepgram (STT)                        │
│      ↑                                                         │
│      │ HTTPS (TLS 1.3)                                        │
└──────┼─────────────────────────────────────────────────────────┘
       │
┌──────▼─────────────────────────────────────────────────────────┐
│                    COMPANION APP                                │
│                                                                  │
│   React Native + Expo (or Flutter)                              │
│   ├── BLE pairing (one-time Wi-Fi credential exchange)          │
│   ├── mDNS discovery (_honestpuck._tcp)                         │
│   ├── HTTPS sync (chunked .opus download)                       │
│   ├── Opus playback (expo-av / just_audio)                      │
│   └── SQLite (recording index, sync state)                      │
│      ↑                                                           │
│      │ Wi-Fi LAN (HTTPS / mDNS)                                │
└──────┼───────────────────────────────────────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────────────┐
│                    FIRMWARE (ESP-IDF v5.5+)                       │
│                                                                    │
│   FreeRTOS tasks on dual-core Xtensa LX7:                        │
│    Core 0: PDM audio capture (I2S DMA) → Opus encode → NAND     │
│    Core 1: Wi-Fi STA (mDNS + HTTPS) / LED (RMT) / buttons       │
│                                                                    │
│   Key components:                                                 │
│    • espressif/spi_nand_flash v0.17+ (W25N02KV, Dhara FTL)      │
│    • espressif/led_strip (WS2812B via RMT)                       │
│    • libopus (Xtensa-optimized, 16 kHz mono)                     │
│    • esp_https_server (local API for companion app)               │
│    • esp_https_ota (signed firmware updates)                      │
│                                                                    │
│   Privacy: IO4 PMOS gate = hardware mic/LED interlock             │
│   Power: 66 µA deep sleep, wake on IO0 (BTN_MAIN)                │
└──────┬─────────────────────────────────────────────────────────────┘
       │ runs on
┌──────▼─────────────────────────────────────────────────────────────┐
│                    HARDWARE (PCB v3)                                │
│                                                                      │
│   SKiDL netlist → KiCad 8 → Gerber → JLCPCB                       │
│                                                                      │
│   ESP32-S3-WROOM-1 (brain)                                         │
│    ├── W25N02KV 2 Gb NAND (QSPI, 416 Mbps) ← FIX-003             │
│    ├── IM73D122 MEMS mic (PDM, privacy-interlocked) ← Honest Mic  │
│    ├── 8× WS2812B (PMOS load-switched) ← FIX-002                  │
│    ├── 3× tactile buttons (10 kΩ HW pull-ups)                     │
│    ├── TPS63031 buck-boost (3.0–4.2 V → 3.3 V, >90% eff) ←FIX-001│
│    ├── TP4056 LiPo charger (300 mA, 0.75 C)                       │
│    └── USB-C (5 V input, 5.1 kΩ CC pull-downs)                    │
│                                                                      │
│   Form factor: 50 mm ⌀ × 15 mm, translucent silicone overmold     │
│   Battery: 400 mAh LiPo, ~252 days standby                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Development Environment Setup

### Hardware Design (what exists today)

```bash
# 1. Clone
git clone <repo-url> && cd pendant

# 2. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install skidl pytest

# 3. Generate netlist
python3 netlist/honest_puck_v3.py     # → honest_puck_v3.net

# 4. Run design validation
python3 -m pytest tests/ -v           # 91 tests, all passing

# 5. Open in KiCad 8
# File → Import → Netlist → honest_puck_v3.net
# Create HonestPuck_Lib symbols (see ARCHITECTURE.md)
# Route PCB (50 mm circular, 4-layer recommended)
# Run DRC → export Gerbers
```

### Firmware Development

```bash
# 1. Install ESP-IDF v5.5+
mkdir -p ~/esp && cd ~/esp
git clone --recursive https://github.com/espressif/esp-idf.git -b v5.5
cd esp-idf && ./install.sh all && source export.sh

# 2. Create and configure project
cd ~/pendant
mkdir -p firmware && cd firmware
idf.py create-project honest_puck_fw
cd honest_puck_fw
idf.py set-target esp32s3
idf.py add-dependency "espressif/spi_nand_flash^0.17"
idf.py add-dependency "espressif/led_strip"

# 3. Build + Flash
idf.py build
idf.py -p /dev/ttyACM0 flash monitor

# 4. Debug (optional, via built-in USB-JTAG)
idf.py openocd
# In another terminal:
idf.py gdb
```

### Companion App (Flutter — recommended)

```bash
# 1. Create project
flutter create --org com.honestpuck --platforms ios,android honest_puck_app
cd honest_puck_app

# 2. Add dependencies
flutter pub add flutter_blue_plus just_audio just_audio_background drift \
    bonsoir dio riverpod path_provider

# 3. Generate drift database code
dart run build_runner build

# 4. Development
flutter run              # debug on connected device
flutter build apk        # Android release
flutter build ios        # iOS release
```

### Companion App (React Native + Expo — alternative)

```bash
# 1. Create project
npx create-expo-app@latest HonestPuckApp --template tabs
cd HonestPuckApp

# 2. Install dependencies
npx expo install react-native-ble-plx react-native-zeroconf
npm install react-native-track-player zustand op-sqlite axios

# 3. Development (BLE requires dev build, not Expo Go)
npx expo run:ios       # or run:android
npx expo start         # Expo Go for non-native-module screens

# 4. Build for testing
eas build --platform ios --profile preview
eas build --platform android --profile preview
```

### Backend (if using cloud features)

```bash
# 1. Create project
mkdir -p backend && cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install "fastapi[standard]" asyncpg dramatiq[redis] pyjwt boto3

# 2. Database
docker run -d --name hp-pg -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16
docker run -d --name hp-redis -p 6379:6379 redis:7

# 3. Run API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. Run worker (transcription jobs)
dramatiq app.workers
```

---

## Key Technical Constraints

These constraints must be respected across every layer of the stack.
Violating any of them breaks the product.

| # | Constraint | Impact | Layer |
|---|------------|--------|-------|
| 1 | **66 µA deep sleep budget** | Firmware must gate IO4 and IO7 HIGH (via Hi-Z + pull-ups) before `esp_deep_sleep_start()` | Firmware |
| 2 | **Privacy interlock (IO4)** | Mic VDD and red LED share HONEST_MIC_PWR copper trace. Firmware must never bypass this — no separate LED control. | Firmware |
| 3 | **WS2812B at 3.3 V** (spec min 3.5 V) | Blue/green channels dim or flicker below 3.4 V. Limit brightness to ≤ 30%. Use warm white / red / amber for reliable status indication. | Firmware + App |
| 4 | **QSPI init sequence** | Must start in Standard SPI mode → write W25N02KV SR3[1]=1 → switch to Quad mode. Reversing this bricks the flash session. | Firmware driver |
| 5 | **1.61 W thermal ceiling** | Never run TP4056 charging + ESP32 Wi-Fi TX simultaneously at full power. Firmware must check VBUS presence and throttle Wi-Fi or pause charging. LiPo must stay < 60 °C. | Firmware |
| 6 | **400 mAh / 0.75 C charge rate** | Charge current is fixed at 300 mA by the 4 kΩ PROG resistor (hardware). Not configurable in firmware. | Hardware |
| 7 | **50 mm × 15 mm form factor** | PCB must fit circular outline. Silicone mold limits heat dissipation (k ≈ 0.2 W/m·K). All components on one side preferred for thinner build. | Hardware / Mechanical |
| 8 | **2 Gb NAND = 256 MB** | At 16 kHz 16-bit mono raw PCM: ~2.2 hours. With Opus @ 16 kbps: ~35 hours. Firmware must manage storage, delete oldest recordings when full (or warn user). | Firmware + App |
| 9 | **OTA partition size: 1.75 MB** | Firmware binary must stay under 1.75 MB to fit in an OTA partition. Monitor binary size in CI. | Firmware + CI |
| 10 | **IM73D122 bottom-port acoustic** | PCB needs an unobstructed acoustic hole aligned with the mic port. Silicone overmold must not block this path. | Hardware / Mechanical |

---

## Bill of Materials (Key ICs)

| Ref | Part | Package | Supplier | Approx. Cost (1 pc) |
|-----|------|---------|----------|---------------------|
| U1 | ESP32-S3-WROOM-1 (N16R8) | RF Module | Digi-Key / LCSC | ~$3.50 |
| U2 | W25N02KVZEIR | WSON-8 | Digi-Key / Mouser | ~$2.00 |
| U3 | TP4056 | SOIC-8 | LCSC | ~$0.10 |
| U4 | TPS63031DSKR | WSON-10 | Digi-Key / Mouser | ~$2.50 |
| M1 | IM73D122V01 | Bottom-port MEMS | Digi-Key / Mouser | ~$1.50 |
| Q1, Q2 | BSS84 (PMOS) | SOT-23 | LCSC | ~$0.03 ea |
| D1–D8 | WS2812B | PLCC-4 | LCSC | ~$0.05 ea |
| J1 | USB-C receptacle (HRO TYPE-C-31-M-12) | SMD | LCSC | ~$0.15 |
| J2 | JST SH 1×02 | 1.0 mm pitch | LCSC | ~$0.08 |
| L1 | 2.2 µH inductor (Isat ≥ 1.5 A) | 2520 / 6332 | Digi-Key | ~$0.30 |
| — | LiPo cell 400 mAh | Pouch | AliExpress / Adafruit | ~$3.00 |
| | | | **Total BOM (ICs only)** | **~$14** |

---

## Summary & Roadmap

| Layer | Status | What's Needed | Estimated Effort |
|-------|--------|---------------|------------------|
| **Hardware netlist** | **Done** (v3, 91 tests passing) | — | — |
| **PCB layout** | Not started | KiCad routing on 4-layer 50 mm circular board, DRC, Gerber export, fab order | 2–3 days (experienced), 1–2 weeks (learning KiCad) |
| **Firmware** | Not started | ESP-IDF project, 8+ modules (audio, storage, privacy, UI, power, network, OTA) | Core functionality: 3–4 weeks. Polish + OTA: 2 more weeks. |
| **Companion app** | Not started | React Native or Flutter, 4 screens, BLE pairing, mDNS sync, audio playback | 2–3 weeks |
| **Cloud backend** | Not started (optional) | FastAPI + PostgreSQL + S3 + transcription worker | 1–2 weeks (MVP) |
| **CI/CD** | Not started | GitHub Actions: netlist tests, firmware build, app lint, backend tests | 1–2 days |
| **Mechanical / enclosure** | Not started | Silicone mold design, acoustic port, button caps, light pipe, lanyard | 1–2 weeks (iterative prototyping) |
| **Certification** | Not started | FCC Part 15 (intentional radiator — ESP32 Wi-Fi/BLE), CE RED. May use ESP32-S3-WROOM-1 modular approval. | 4–8 weeks (with test lab) |

### Recommended Build Order

```
1. PCB layout + fab order           (while waiting for boards:)
2. Firmware skeleton on ESP32-S3    ← start with dev board (ESP32-S3-DevKitC)
   devkit                            a. Audio capture (PDM → PCM)
                                     b. Privacy interlock (IO4)
                                     c. NAND storage (SPI NAND component)
                                     d. LED ring (RMT)
                                     e. Buttons + deep sleep
3. Companion app MVP                ← can develop against firmware on devkit
   (pairing + recording list +        over Wi-Fi (no PCB needed yet)
    playback)
4. Integrate on real PCB            ← boards arrive, flash firmware
5. Cloud backend (if desired)       ← optional, can add post-launch
6. Polish, OTA, CI/CD              ← harden for shipping
7. Enclosure + certification       ← final steps before production
```
