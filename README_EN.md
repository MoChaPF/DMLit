# DMLit

[中文](README.md) | **English**

Lighting control tool for the **WEIKAV VEGA108** keyboard. Works over USB and 2.4G wireless, with real-time color readback, firmware effects, music-reactive lighting and a live preview.

![Screenshot](screenshot.png)

## Features

- **Firmware effects** (19): solid, single-key glow, single-key fade, breathing, starfield, snowfall, blooming, spectrum cycle, colorful spring, multicolor, wave flow, rainbow windmill, touch trigger, one stone two birds, ripple spread, endless flow, layered peaks, slanting rain, ping-pong
- **Music reactive**: captures system audio, four modes (bars / volcano / rainbow / RGB pulse)
- **Real-time color readback**: reads per-key colors (USB `cmd 51`, wireless `cmd 59`)
- **Battery level**: works on both USB and 2.4G
- **Per-key coloring**: pick a color first, then click a keycap to paint it; select all / WASD / number row / arrow keys to recolor in bulk (keyboard mode 20, persisted via `SET_CUSTOM_LED_DATA`, so it no longer rolls back after a few seconds)
- **Key remapping**: click **改键**, select a key on the keyboard layout, then pick a new keycode — written straight to the keyboard (`SET_KEY=34` full-table write, `GET_KEY=18` readback). Don't remap the Fn key (`0xAF`), it's a firmware special key
- Color, brightness, speed and direction controls
- **State memory**: saves the last effect/color/brightness to `state.json` and restores it on launch
- Splash screen, frosted-glass UI
- Auto-detects USB / 2.4G and shows the connection type
- **System tray**: closing the window keeps it running in the tray
- **Launch on startup**: enabled by default, toggle from the tray menu
- **Auto-pause when minimized or hidden to the tray**: both real-time readback and UI repainting (30fps polling + preview redraw) stop, dropping background CPU from ~12% to under 0.2%; resumes automatically when the window returns

## Changelog

See [CHANGELOG_EN.md](CHANGELOG_EN.md) for per-version changes.

## Download

Grab **DMLit.exe** and double-click — no Python required:

- [Gitee Releases](https://gitee.com/Anran512/dmlit/releases/latest)
- [GitHub Releases](https://github.com/MoChaPF/DMLit/releases)

## Repositories

- Gitee (primary): https://gitee.com/Anran512/dmlit
- GitHub (mirror): https://github.com/MoChaPF/DMLit

## Requirements (running from source)

- Windows 10 / 11
- Python 3.8+
- WebView2 runtime (bundled with Win10/11 in most cases)

## Install and run

```bash
pip install -r requirements.txt
```

Then double-click `启动.bat`, or:

```bash
python app.py
```

## Usage

- Works over USB or with the 2.4G dongle; the status bar shows `有线连接` (USB) or `2.4G 无线` (wireless)
- After plugging/unplugging, click **刷新键盘连接状态** to re-detect
- **Real-time readback uses keyboard bandwidth** — turn it off when you don't need live colors
- While music mode is on, speed/brightness and the color palette are locked (the software takes over)
- Per-key coloring: click **自定义** to enter the mode, pick a color first, then click a keycap. For bulk edits tick all / WASD / number row / arrow keys, then click a color
- **Key remapping**: click **改键**, pick a key on the keyboard, then pick a new keycode — written straight to the keyboard (`SET_KEY=34`, full-table write)
- **Performance panel**: live CPU / memory plus per-thread CPU and HID read/write counters
- Closing the app returns the keyboard to its built-in lighting

## Files

| File | Description |
|---|---|
| `app.py` | Main program (HID, effect engines, audio analysis, WebView bridge) |
| `index.html` | UI (HTML/CSS/JS) |
| `vega108_layout.json` | Key layout (6 rows / 106 keys with LED ids) |
| `splash.png` | Splash screen image |
| `启动.bat` | Windows launcher |
| `state.json` | Generated at runtime, stores the last settings |

## Protocol notes

Reverse-engineered from the vendor's web driver:

- Frame header `0xAA`, response header `0x55`; commands: `GET_LED_EFFECT=19`, `SET_LED_EFFECT=35`, `SET_CUSTOM_LED_DATA=36`, `GET_ALL_LIGHTS_RGB=51`
- Wireless (2.4G, usagePage `0xFF60`) has its own command subset: `GET_ALL_LIGHTS_RGB_24G_64_BYTE=59` (colors + battery), `SET_MUSIC_DATA_24G_64_BYTE=60` (push frames)
- Firmware effects must include `driverSetting=255`, otherwise the wireless channel drops the whole packet
- Per-key coloring uses keyboard **mode 20** (the firmware's own "custom" effect) with `SET_CUSTOM_LED_DATA=36`: 512 bytes = 128 LEDs × `[ledId, r, g, b]`. Writing it as temporary data (cmd 50) gets overwritten by the firmware animation within seconds
- Key remapping: `SET_KEY=34` full-table write (512 bytes = 128 keys × `[pageType, p1, p2, p3]`), `GET_KEY=18` readback. The reply uses a custom header `AA 12 04 addrLo addrHi 00 00 00` with data at `r[8..11]`; skip `cmd=51` RGB noise packets when parsing

## Known limitations

- Reading the current effect (`cmd 19`) blocks for a long time on wireless; the app skips it and relies on saved state
- Effect tables may differ between firmware revisions
- Software-driven full-frame updates are limited by the USB endpoint; firmware effects are free of that cost

## Adapting to other keyboards

Only VEGA108 is supported right now. To port this tool to another keyboard, see **[docs/适配指南.md](docs/适配指南.md)** (Chinese) — written for an AI assistant: protocol reverse-engineering, code map, a 10-step checklist and the known pitfalls.

## Using an AI assistant

This project was written from scratch by an AI assistant (DSH) in a single conversation. To work the same way:

### What you need

| Item | Notes |
|---|---|
| **DeepSeek Harness (DSH)** | Desktop AI coding client with file and shell access |
| **`dsh-plugin-memory`** (optional) | Long-term memory, so the assistant remembers project context, protocol findings and pitfalls |
| **Not needed** | No lighting or keyboard plugins — the assistant captures the HID traffic itself |

File access, shell commands and web search are built into DSH — no plugins required.

### Copy-paste install request

```
Install this DSH plugin and tell me if a restart is needed:
dsh-plugin-memory — long-term memory for project context, protocol findings and past pitfalls

If it's not in the plugin marketplace, install it by npm package name dsh-plugin-memory.
Then read docs/适配指南.md and help me adapt DMLit.
```

### Hand the project over

1. Clone the repo locally (it needs `app.py`, `index.html`, `docs/`, `vega108_layout.json`)
2. Set that folder as the working directory in DSH
3. Start a new conversation and paste this prompt:

```
Read docs/适配指南.md and docs/键盘手册.md, then read the Keyboard and Api classes in app.py.
I want to adapt DMLit to <my keyboard model>.
Follow the 10-step checklist in the guide, and verify each step before moving on.
Protocol conclusions must be backed by packet captures or real tests — no guessing.
```

### Three rules for the user

- **Don't let it skip packet capture** — code written from guesses will be wrong
- **Verify after every change** instead of batching them up
- **Let it stop and ask** when the protocol is unclear; slow beats guessing

## License

[MIT License](LICENSE)

## Credits

- Fonts and images: see [CREDITS.md](CREDITS.md)
- Splash artwork from [蓝鲸档案馆 / Blue Whale Archive](https://fisharchive.cc/) under **CC BY-NC-SA 4.0**

---

Made by **抹茶泡芙 (MoChaPF)** and **DSH大肥鱼 (DeepSeek-chan)**. Open source.
