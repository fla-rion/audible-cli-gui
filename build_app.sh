#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Audible Downloader – macOS .app Bundle bauen
# Ergebnis: dist/Audible Downloader.app
# ─────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

APPNAME="Audible Downloader"
BUNDLE_ID="de.local.audible-downloader"
PY=".venv/bin/python"
PIP=".venv/bin/pip"

echo "═══════════════════════════════════════════"
echo "  $APPNAME – App bauen"
echo "═══════════════════════════════════════════"

# 1 · Abhängigkeiten
echo "→ Abhängigkeiten installieren..."
$PIP install -q pywebview pyinstaller pillow

# 2 · Icon
echo "→ App-Icon erstellen..."
$PY create_icon.py

# 3 · Alte Builds entfernen
rm -rf build dist "__pycache__" "*.spec"

# 4 · ffmpeg/ffprobe suchen (Homebrew Apple Silicon oder Intel)
FFMPEG_BIN=""
FFPROBE_BIN=""
for candidate in \
    "$(command -v ffmpeg 2>/dev/null)" \
    /opt/homebrew/bin/ffmpeg \
    /usr/local/bin/ffmpeg; do
    [ -f "$candidate" ] && { FFMPEG_BIN=$(realpath "$candidate"); break; }
done
for candidate in \
    "$(command -v ffprobe 2>/dev/null)" \
    /opt/homebrew/bin/ffprobe \
    /usr/local/bin/ffprobe; do
    [ -f "$candidate" ] && { FFPROBE_BIN=$(realpath "$candidate"); break; }
done

BINARY_FLAGS=""
if [ -n "$FFMPEG_BIN" ]; then
    echo "→ Bündele ffmpeg: $FFMPEG_BIN"
    BINARY_FLAGS="$BINARY_FLAGS --add-binary \"$FFMPEG_BIN:.\""
else
    echo "⚠ ffmpeg nicht gefunden – wird nicht gebündelt"
fi
if [ -n "$FFPROBE_BIN" ]; then
    echo "→ Bündele ffprobe: $FFPROBE_BIN"
    BINARY_FLAGS="$BINARY_FLAGS --add-binary \"$FFPROBE_BIN:.\""
fi

# 5 · PyInstaller
echo "→ Baue .app Bundle (das dauert 2–3 Minuten)..."

ICON_FLAG=""
[ -f icon.icns ] && ICON_FLAG="--icon icon.icns"

eval $PY -m PyInstaller \
    --windowed \
    --name "\"$APPNAME\"" \
    --add-data "templates:templates" \
    --hidden-import "webview" \
    --hidden-import "webview.platforms.cocoa" \
    --hidden-import "jinja2" \
    --hidden-import "jinja2.ext" \
    --hidden-import "flask" \
    --hidden-import "flask.templating" \
    --hidden-import "audible" \
    --hidden-import "click" \
    --hidden-import "rich" \
    --hidden-import "httpx" \
    --hidden-import "h11" \
    --hidden-import "h2" \
    --noconfirm \
    $ICON_FLAG \
    $BINARY_FLAGS \
    --osx-bundle-identifier "\"$BUNDLE_ID\"" \
    run_app.py

echo ""
echo "✓ App fertig: dist/$APPNAME.app"
echo ""
echo "Starten:"
echo "  open 'dist/$APPNAME.app'"
echo ""
echo "Installieren (optional):"
echo "  cp -r 'dist/$APPNAME.app' /Applications/"
