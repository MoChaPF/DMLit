# Changelog

[中文](CHANGELOG.md) | **English**

## v1.0.6 — 2026-11-10

### Fixed

- **The app hung when clicking the close button** (Windows reported "stopped interacting with Windows and was closed"): introduced in v1.0.5. `on_closing` called `evaluate_js` synchronously to tell the front-end to pause, but at the moment the window is closing the WebView no longer responds, so the main thread blocked — the whole app froze after clicking ×.
  - It now only sets the back-end `paused` flag and lets the front-end pause/resume itself via the per-second poll. No more `evaluate_js` on the close path.
  - Measured: the process stays `responding=True` after closing, background CPU 0.05%.

## v1.0.5 — 2026-11-09

### Fixed

- **High CPU usage while minimized or hidden to the tray**: hiding the window did not stop the front-end 30fps polling and preview repainting, so the GPU and renderer processes kept running at full speed — around 12% on a 12-core machine. The front-end now checks two signals: a back-end `paused` flag polled once per second, plus `visibilitychange`. Both the tray and the minimized states are covered. Measured: 11.7% in the foreground → 0.03% minimized / 0.18% in the tray.
- Also fixed `drawPreview` returning early before the preview was ready, which killed the rAF loop and stopped the preview from ever repainting.

## v1.0.4 — 2026-11-09

### Fixed

- **DMLit could eat up your C: drive over time**: every launch created a new WebView2 profile folder (~22 MB) inside the system temp directory. Because the app exits via `os._exit(0)`, pywebview's cleanup never got a chance to run, so those folders were left behind in `%TEMP%`. Three days of heavy debugging left 330 folders totalling 7.26 GB.
  - The app now reuses a single fixed profile at `%LOCALAPPDATA%\DMLit\WebView2` and no longer creates temp folders.
  - Users upgrading from an older version can reclaim the space manually: in `%TEMP%`, delete folders named like `tmpXXXXXXXX` that contain an `EBWebView` folder.

## v1.0.3 — 2026-11-09

### Added

- **Per-key remapping**: full 108-key table read and write (`SET_KEY(34)` / `GET_KEY(18)`), remap any key, with categories for mouse keys, multimedia keys and lock keys
- **Performance panel**: per-thread CPU usage and HID transaction counters, refreshed every 2 seconds
- **Porting docs**: `docs/适配指南.md` (complete steps for porting to another keyboard) and `docs/键盘手册.md` (full protocol reference)

### Fixed

- Fixed flickering of the locked-state overlay when music reactive mode is turned off
- Fixed the color wheel being dragged around by the background polling data
- Removed the liquid glass effect: the SVG filter conflicted with the locked overlay and caused shaking and misalignment

## v1.0.2 — 2026-11-08

### Added

- Per-key coloring, effects grid consolidated
- New icon

### Fixed

- Frameless dragging reverted to the system title bar
- Fixed the restore-on-startup UI refill

## v1.0.1 — 2026-11-08

- Frameless window with custom title bar
- Tray residency, run at startup, real-time polling paused while minimized to tray

## v1.0.0 — 2026-11-07

- First open-source release: live color reading, firmware effects, music reactive mode, battery level, wired / 2.4G auto-detection
