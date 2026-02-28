# Honest Puck — Software Stack

What you need to build, flash, test, and ship the pendant.

---

## What Exists Today

```
pendant/
├── ARCHITECTURE.md              ← Hardware reference (power, GPIO, thermals)
├── netlist/
│   └── honest_puck_v3.py        ← SKiDL netlist — generates KiCad .net
└── tests/
    └── test_honest_puck_v3.py   ← 91 validation tests (AST-based, no hardware needed)
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

## What's Needed for a Working Product

The netlist defines the PCB. Everything below is what turns that PCB into a
shipping device.

### Layer 0 — PCB Fabrication & Assembly

| Need | Tool / Service | Notes |
|------|----------------|-------|
| Schematic → PCB layout | **KiCad 7/8** | Route traces from the generated netlist |
| Custom symbol library | **HonestPuck_Lib** (KiCad) | ESP32-S3-WROOM-1, IM73D122, W25N02KV, TP4056, TPS63031 |
| Gerber / drill export | KiCad → Gerber | Standard fab output (RS-274X) |
| BOM generation | KiCad or `skidl` export | Map refs to Digi-Key / LCSC / Mouser part numbers |
| PCB fabrication | JLCPCB / PCBWay / OSH Park | 2-layer or 4-layer, 50mm circular board |
| Assembly (SMT) | JLCPCB SMT / hand solder | All components are SMD (0402/0603, WSON, SOIC, PLCC-4) |

### Layer 1 — Firmware (ESP32-S3)

This is the largest missing piece. The pendant needs firmware running on the
ESP32-S3 to do anything.

#### Framework

| Option | Recommendation |
|--------|----------------|
| **ESP-IDF** (Espressif IoT Development Framework) | **Recommended.** Full hardware access, FreeRTOS, mature QSPI/PDM drivers |
| Arduino-ESP32 | Simpler API but weaker QSPI NAND and deep-sleep control |
| MicroPython / CircuitPython | Prototyping only — too slow for PDM audio capture at 3.072 MHz |

**ESP-IDF version:** v5.2+ (ESP32-S3 support mature, QSPI NAND drivers stable)

#### Firmware Modules Needed

```
firmware/
├── main/
│   ├── app_main.c              ← Entry point, task orchestration
│   ├── audio_capture.c         ← PDM mic → I2S DMA → ring buffer
│   ├── flash_storage.c         ← W25N02KV QSPI NAND driver + wear leveling
│   ├── privacy_interlock.c     ← IO4 (MIC_ENABLE_N) control, LED-tied guarantee
│   ├── led_ring.c              ← WS2812B via RMT peripheral (IO8)
│   ├── button_handler.c        ← GPIO ISR + debounce for IO0/IO1/IO9
│   ├── power_manager.c         ← Deep sleep entry/exit, wake sources (IO0 RTC)
│   ├── wifi_sync.c             ← Connect to AP, upload audio blobs via HTTPS
│   └── battery_monitor.c       ← ADC reading of VBAT (if divider added) or fuel gauge
├── components/
│   └── w25n02kv/               ← Custom SPI NAND driver (QSPI init sequence)
├── partitions.csv              ← Flash partition table (NVS, app, OTA)
├── sdkconfig.defaults          ← ESP-IDF Kconfig overrides
└── CMakeLists.txt
```

#### Critical Driver Details

| Subsystem | ESP-IDF API | GPIO | Notes |
|-----------|-------------|------|-------|
| PDM Microphone | `i2s_pdm_rx` (I2S PDM mode) | IO5 (CLK), IO6 (DATA) | 16-bit @ 16 kHz or 48 kHz. DMA double-buffer to avoid glitches |
| QSPI NAND Flash | `spi_device_queue_trans` | IO10–IO13 (SPI), IO2–IO3 (QSPI) | Init in SPI mode first, write SR3[1]=1 for Quad mode, then switch bus width |
| WS2812B LEDs | `rmt_transmit` (RMT peripheral) | IO8 | 800 kbps NRZ. RMT handles timing in hardware. Drive IO7 LOW first to power the ring |
| Buttons | `gpio_isr_handler_add` + `esp_timer` debounce | IO0, IO1, IO9 | IO0 is RTC-capable — use `esp_sleep_enable_ext0_wakeup` |
| Mic load switch | `gpio_set_level(IO4, 0)` to enable | IO4 | Active-low PMOS gate. Pull-up forces OFF during sleep |
| LED load switch | `gpio_set_level(IO7, 0)` to enable | IO7 | Same topology as mic switch |
| Deep sleep | `esp_deep_sleep_start()` | — | Wake on IO0 (BTN_MAIN). Budget: 66 µA total |

#### Build & Flash

```bash
# Install ESP-IDF
git clone --recursive https://github.com/espressif/esp-idf.git -b v5.2
cd esp-idf && ./install.sh && source export.sh

# Build firmware
cd firmware/
idf.py set-target esp32s3
idf.py build

# Flash via USB-C (built-in USB-JTAG on ESP32-S3)
idf.py -p /dev/ttyACM0 flash monitor
```

### Layer 2 — Companion App (Phone)

The pendant records audio and stores it on the W25N02KV NAND. A companion app
pulls recordings off the device over Wi-Fi.

#### Recommended Stack

| Component | Technology | Why |
|-----------|------------|-----|
| Cross-platform framework | **React Native** or **Flutter** | Single codebase for iOS + Android |
| Local discovery | **mDNS** (Bonjour/Avahi) | ESP32-S3 advertises `_honestpuck._tcp` on local network |
| Transfer protocol | **HTTPS** (TLS 1.3) | ESP32 runs a lightweight HTTPS server (esp_https_server) or POST to companion |
| Audio format | **Opus** or **FLAC** | Compress on-device before transfer (Opus for size, FLAC for lossless) |
| Storage | **SQLite** (on-phone) | Track recordings, metadata, sync state |
| Auth | **None** (local pairing) or **BLE OOB** | Device is physically owned — Wi-Fi PSK exchange during setup |

#### Key Screens

1. **Pairing** — Scan for pendant on local network (mDNS), exchange Wi-Fi credentials
2. **Recordings** — List synced audio files, playback, delete
3. **Device Status** — Battery level, storage used, firmware version
4. **Settings** — Wi-Fi config, audio quality, auto-sync toggle

### Layer 3 — Cloud Backend (Optional)

Only needed if recordings should sync beyond the phone.

| Component | Technology | Why |
|-----------|------------|-----|
| API | **FastAPI** (Python) or **Express** (Node.js) | REST endpoints for upload, user management |
| Storage | **S3-compatible** (AWS S3, MinIO, R2) | Audio blob storage |
| Database | **PostgreSQL** | User accounts, recording metadata, device registry |
| Auth | **OAuth 2.0 / JWT** | Standard token-based auth |
| Transcription | **Whisper** (OpenAI) or **Deepgram** | Speech-to-text on uploaded audio |
| Hosting | **Fly.io** / **Railway** / **AWS ECS** | Container-based deployment |
| Queue | **Redis + Celery** or **SQS** | Async transcription jobs |

---

## Full Dependency Map

```
┌─────────────────────────────────────────────────────────┐
│                    CLOUD (optional)                       │
│  FastAPI / Express  ←→  PostgreSQL  ←→  S3 blob store    │
│         ↑                                   ↑            │
│         │ HTTPS                              │            │
│         ↓                                    │            │
│  ┌─────────────────┐                         │            │
│  │  COMPANION APP  │    Opus/FLAC audio ─────┘            │
│  │  React Native   │                                      │
│  │  or Flutter     │                                      │
│  └────────┬────────┘                                      │
│           │ Wi-Fi (HTTPS / mDNS)                          │
└───────────┼───────────────────────────────────────────────┘
            │
    ┌───────▼───────────────────────────────────────┐
    │              FIRMWARE (ESP-IDF)                │
    │  FreeRTOS tasks:                              │
    │   • PDM audio capture (I2S DMA)               │
    │   • QSPI NAND read/write (W25N02KV)           │
    │   • Wi-Fi STA mode (sync on demand)            │
    │   • WS2812B LED feedback (RMT)                 │
    │   • Deep sleep power management                │
    │   • Button wake + debounce                     │
    └───────┬───────────────────────────────────────┘
            │ runs on
    ┌───────▼───────────────────────────────────────┐
    │              HARDWARE (PCB)                    │
    │  SKiDL netlist → KiCad → Gerber → Fab         │
    │                                                │
    │  ESP32-S3  ←→  W25N02KV (QSPI)                │
    │     ├── IM73D122 mic (PDM, privacy-interlocked)│
    │     ├── 8× WS2812B (load-switched)             │
    │     ├── 3× buttons (HW pull-up)                │
    │     └── TPS63031 buck-boost ← TP4056 ← USB-C  │
    └───────────────────────────────────────────────┘
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
python3 netlist/honest_puck_v3.py

# 4. Run tests
python3 -m pytest tests/ -v

# 5. Open in KiCad (manual)
# Import honest_puck_v3.net → schematic → layout → Gerbers
```

### Firmware Development

```bash
# 1. Install ESP-IDF v5.2+
git clone --recursive https://github.com/espressif/esp-idf.git -b v5.2
cd esp-idf && ./install.sh && source export.sh

# 2. Create firmware project
idf.py create-project honest_puck_fw
cd honest_puck_fw
idf.py set-target esp32s3

# 3. Configure
idf.py menuconfig
# → Component config → ESP32-S3 → set Flash SPI mode to QIO
# → Component config → I2S → enable PDM RX
# → Component config → Wi-Fi → enable STA mode

# 4. Build + Flash
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

### Companion App

```bash
# React Native
npx react-native init HonestPuckApp --template react-native-template-typescript
cd HonestPuckApp
npx react-native run-android   # or run-ios

# OR Flutter
flutter create honest_puck_app
cd honest_puck_app
flutter run
```

---

## CI/CD Pipeline (Recommended)

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
      - run: python3 netlist/honest_puck_v3.py  # verify netlist generates cleanly

  firmware-build:
    runs-on: ubuntu-latest
    container: espressif/idf:v5.2
    steps:
      - uses: actions/checkout@v4
      - run: idf.py set-target esp32s3
        working-directory: firmware
      - run: idf.py build
        working-directory: firmware
```

---

## Key Technical Constraints

These constraints must be respected across every layer of the stack:

| Constraint | Impact | Layer |
|------------|--------|-------|
| 66 µA deep sleep budget | Firmware must gate IO4 and IO7 HIGH before sleep | Firmware |
| Privacy interlock (IO4) | Mic and red LED are physically wired together — never software-disable the LED independently | Firmware |
| WS2812B at 3.3 V (spec min 3.5 V) | Limit brightness to ~30%; blue/green may dim at low battery | Firmware + App |
| QSPI init sequence | Must start in SPI mode, set SR3[1]=1, then switch to Quad mode | Firmware driver |
| 1.61 W thermal ceiling | Never charge (TP4056) and Wi-Fi TX simultaneously at full power — firmware must throttle | Firmware |
| 400 mAh / 0.75 C | Charge current fixed at 300 mA by 4 kΩ PROG resistor (hardware, not configurable) | Hardware |
| 50 mm × 15 mm form factor | PCB must fit circular outline; silicone mold limits heat dissipation | Hardware / Mechanical |
| 2 Gb NAND (256 MB) | At 16 kHz 16-bit mono = ~32 KB/s → ~2.2 hours of raw audio; Opus compression extends to ~20+ hours | Firmware |

---

## Summary

| Layer | Status | Effort |
|-------|--------|--------|
| **Hardware netlist** | Done (v3, 91 tests passing) | — |
| **PCB layout** | Not started | KiCad routing, DRC, Gerber export |
| **Firmware** | Not started | ESP-IDF project, 8 core modules |
| **Companion app** | Not started | React Native or Flutter, 4 screens |
| **Cloud backend** | Not started (optional) | FastAPI + S3 + Whisper |
| **CI/CD** | Not started | GitHub Actions for tests + firmware build |
| **Mechanical / enclosure** | Not started | Silicone mold design, waterproofing |
