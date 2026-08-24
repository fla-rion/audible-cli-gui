# Audible Downloader GUI

A local app to download and convert your Audible audiobooks (German marketplace) to MP3 — no cloud, no subscription, just your own library. Runs on macOS, Windows and Linux.

![macOS](https://img.shields.io/badge/macOS-12%2B-blue) ![Windows](https://img.shields.io/badge/Windows-10%2B-blue) ![Linux](https://img.shields.io/badge/Linux-x86__64-blue) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

> **For personal use only.** Only download audiobooks you have purchased and are licensed to use.

---

## Features

- Browse your full Audible library
- Download audiobooks in AAX/AAXC format
- Convert to MP3 with chapter metadata and cover art
- Clean local web UI (Flask + Alpine.js)
- Runs entirely on your machine — no data leaves your computer

## Requirements

- An Audible account (German marketplace, `audible.de`)
- **macOS:** 12 or later (Apple Silicon or Intel) + [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg`)
- **Windows:** 10 (21H2+) or 11 — [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) is required and ships pre-installed on current Windows; ffmpeg is bundled, nothing extra to install
- **Linux:** x86_64, any distro with a desktop session (X11 or XWayland). Qt/WebEngine and ffmpeg are bundled; the only system libraries expected to already be present are the standard X11/XCB libs every desktop environment ships with (`libxcb-cursor0` and friends) — if your distro is unusually minimal, `sudo apt install libxcb-cursor0` (or the distro equivalent)

## Installation

All builds are published automatically on [Releases](https://github.com/fla-rion/audible-cli-gui/releases).

### macOS

1. Download the latest `Audible.Downloader.dmg`
2. Open the DMG and drag **Audible Downloader** into your Applications folder
3. **Gatekeeper entsperren** — einmalig im Terminal ausführen:
   ```bash
   xattr -cr "/Applications/Audible Downloader.app"
   ```
4. App starten

> Die App ist mit einem lokalen Zertifikat signiert, aber nicht von Apple notarisiert (kein Developer Account erforderlich). Der `xattr`-Befehl entfernt die Quarantäne-Flag — danach öffnet sie sich normal.

### Windows

1. Download the latest `AudibleDownloader-windows-x64.zip`
2. Unzip anywhere (e.g. `Documents\Audible Downloader`)
3. Run `Audible Downloader.exe`

> SmartScreen may warn about an unrecognized app on first launch (the build isn't code-signed). Click **More info → Run anyway**.

### Linux

1. Download the latest `AudibleDownloader-linux-x64.tar.gz`
2. Extract and run:
   ```bash
   tar -xzf AudibleDownloader-linux-x64.tar.gz
   cd "Audible Downloader"
   ./Audible\ Downloader
   ```

## First-time Setup

On first launch the app will ask you to authenticate with Audible. Follow the on-screen instructions — your credentials are stored locally in `~/.audible_tool/` and never transmitted anywhere else.

## Build from Source

```bash
# Clone
git clone https://github.com/fla-rion/audible-cli-gui.git
cd audible-cli-gui

# Python env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run in dev mode
python app.py

# Build .app + DMG (macOS only)
chmod +x build_app.sh
./build_app.sh
hdiutil create -volname "Audible Downloader" \
  -srcfolder "dist/Audible Downloader.app" \
  -ov -format UDZO \
  "dist/Audible.Downloader.dmg"
```

### Windows & Linux builds (CI)

Windows and Linux builds are produced by [`.github/workflows/build.yml`](.github/workflows/build.yml) — no local Windows/Linux machine needed.

- **Push a version tag** to build and publish a GitHub Release automatically:
  ```bash
  git tag v1.1.0
  git push origin v1.1.0
  ```
  This builds both platforms and attaches `AudibleDownloader-windows-x64.zip` and `AudibleDownloader-linux-x64.tar.gz` to a new Release named after the tag.
- **Manual run** without a release: open the *Actions* tab → **Build Windows & Linux** → **Run workflow**. The builds are attached as downloadable artifacts on the run, no Release is created.

The macOS `.dmg` is still built locally (see above) and uploaded to the same Release by hand — the workflow only adds to a Release's assets, it never removes existing ones.

## License

MIT — see [LICENSE](LICENSE)
