# StackChan Firmware

The factory firmware now uses the GrokBot expression atlas for the main AI avatar.

## GrokBot avatar

- 53 GrokBot states are baked into `main/assets/assets_bin/grokbot.atlas`.
- Frames are 200x200, 4-bit grayscale, XOR-delta encoded and zlib compressed.
- LVGL renders the packed I4 image directly with a palette; frames are not
  expanded to a full RGB565 copy.
- The image is invalidated as a full frame to prevent residual pixels when a
  large expression shrinks. The LCD path uses a 20-line double buffer so
  rendering and SPI transfer can overlap; animation buffers stay in PSRAM so
  Wi-Fi and audio retain enough internal RAM.
- Playback uses exact reverse playback for smooth ping-pong loops.
- AI emotions and assistant states map to matching expression groups.
- Idle mode randomly rotates through calm, active, reactive and rare brand states.
- The atlas lives in the `assets` partition; the partition is expanded to
  `0x5F0000` so dual OTA slots and the factory applications remain available.
- If the atlas is missing or invalid at runtime, the firmware falls back to the
  original vector avatar.

The avatar is image-backed rather than LVGL-vector-backed. It keeps the existing
speech bubble and touch-to-talk behavior. Head-pet reactions and idle servo
motion remain enabled; the original eye/mouth blink modifiers are skipped for
the GrokBot avatar.

## Build

### Fetch dependencies

```bash
python3 ./fetch_repos.py
```

### Toolchain

[ESP-IDF v6.1](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32s3/index.html)

### Build

```bash
idf.py build
```

### Host-side tests

The motion coordinate helpers can be tested without ESP-IDF hardware:

```bash
cmake -S tests -B build-host-tests
cmake --build build-host-tests
ctest --test-dir build-host-tests --output-on-failure
```

### Flash

```bash
idf.py flash
```

For a single web-burner image, run this after a successful build.
`idf.py build` also generates and checks the assets image, so no prior flash is required:

```bash
idf.py merge-bin -o build/stackchan-grokbot-full.bin
```

## Regenerate the GrokBot atlas

The baker and the upstream GrokBot player/recipes are included under
`tools/grokbot/` and `third_party/grokbot/`.

```powershell
cd tools/grokbot
npm install
cd ../..
$env:CHROME_BIN = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$env:SIZE = "200"
node tools/grokbot/bake.mjs
```

On macOS/Linux use `CHROME_BIN=/path/to/chrome SIZE=200 node tools/grokbot/bake.mjs`.
The generated file is written to
`firmware/main/assets/assets_bin/grokbot.atlas`. The baker can also use an
existing Playwright installation via
`PLAYWRIGHT_MODULE=/path/to/playwright`.

## Prebuilt firmware

`build/stackchan-grokbot-full.bin` is the merged 16 MB factory image.

- Size: `16,450,520` bytes
- SHA256: `4F0C2BB94FE8DB15627A85B79FBCDB6240D94895FD5A266C9DA40339CF0F24AC`
- Hardware verification: M5Stack StackChan, ESP32-S3 v0.2, 16 MB Flash
- Runtime verification: 53 clips loaded, audio and camera initialized, no crash.
  Idle playback targets 30 FPS. Listening/speaking use 24 FPS for smoother
  expressions, with the Opus decoder above display priority and a 360 ms playback
  queue. After an underrun, audio output prebuffers about 180 ms before resuming.

Flash the merged image at offset `0x0`:

```powershell
esptool.py --chip esp32s3 --port COMx --baud 460800 write_flash `
  --flash_mode dio --flash_freq 80m --flash_size 16MB `
  0x0 build/stackchan-grokbot-full.bin
```
