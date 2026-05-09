#!/usr/bin/env bash
set -e

echo "=== Audible Downloader – Installation ==="

# Python3 prüfen
if ! command -v python3 &>/dev/null; then
  echo "FEHLER: Python 3 nicht gefunden." >&2
  exit 1
fi

# ffmpeg prüfen
if ! command -v ffmpeg &>/dev/null; then
  echo "FEHLER: ffmpeg nicht gefunden. Bitte installieren (z.B. 'brew install ffmpeg')." >&2
  exit 1
fi

# Virtuelle Umgebung erstellen
if [ ! -d ".venv" ]; then
  echo "→ Erstelle virtuelle Umgebung (.venv)..."
  python3 -m venv .venv
fi

# Abhängigkeiten installieren
echo "→ Installiere Abhängigkeiten..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo ""
echo "✓ Installation abgeschlossen!"
echo ""
echo "Verwendung:"
echo "  .venv/bin/python main.py login"
echo "  .venv/bin/python main.py library"
echo "  .venv/bin/python main.py download <ASIN>"
echo "  .venv/bin/python main.py download --format m4b <ASIN>"
echo "  .venv/bin/python main.py download-all --format m4b"
echo "  .venv/bin/python main.py --help"
