# Audible Downloader GUI

A local macOS app to download and convert your Audible audiobooks (German marketplace) to MP3 — no cloud, no subscription, just your own library.

![macOS](https://img.shields.io/badge/macOS-12%2B-blue) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

> **For personal use only.** Only download audiobooks you have purchased and are licensed to use.

---

## Features

- Browse your full Audible library
- Download audiobooks in AAX/AAXC format
- Convert to MP3 with chapter metadata and cover art
- Clean local web UI (Flask + Alpine.js)
- Runs entirely on your Mac — no data leaves your machine

## Requirements

- macOS 12 or later (Apple Silicon or Intel)
- [ffmpeg](https://ffmpeg.org/) — install via Homebrew: `brew install ffmpeg`
- An Audible account (German marketplace, `audible.de`)

## Installation

1. Download the latest `Audible.Downloader.dmg` from [Releases](https://github.com/fla-rion/audible-cli-gui/releases)
2. Open the DMG and drag **Audible Downloader** into your Applications folder
3. On first launch: right-click → Open (macOS Gatekeeper warning)

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

# Build .app + DMG
chmod +x build_app.sh
./build_app.sh
hdiutil create -volname "Audible Downloader" \
  -srcfolder "dist/Audible Downloader.app" \
  -ov -format UDZO \
  "dist/Audible.Downloader.dmg"
```

## License

MIT — see [LICENSE](LICENSE)
