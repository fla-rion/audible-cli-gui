#!/usr/bin/env python3
"""Erstellt icon.png + icon.icns für das macOS .app Bundle."""

import os
import subprocess
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow nicht gefunden – wird installiert...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pillow"], check=True)
    from PIL import Image, ImageDraw

SIZE = 1024
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Abgerundetes Hintergrundrechteck (Amber #f59e0b)
R = SIZE // 5
draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=R, fill="#f59e0b")

# ── Kopfhörer (weiss) ─────────────────────────────────────────
m   = SIZE // 8       # Rand
sw  = SIZE // 16      # Strichbreite (headband)
cup = SIZE // 10      # Ohrhörer-Breite

# Bügel (Halbbogen oben)
draw.arc([m, m + sw, SIZE - m, SIZE // 2 + m], start=180, end=360,
         fill="white", width=sw)

# Linker Ohrhörer
lx = m - sw // 2
draw.rounded_rectangle(
    [lx, SIZE // 2 - sw // 2, lx + cup, SIZE // 2 + cup * 2],
    radius=cup // 3, fill="white",
)

# Rechter Ohrhörer
rx = SIZE - m - cup + sw // 2
draw.rounded_rectangle(
    [rx, SIZE // 2 - sw // 2, rx + cup, SIZE // 2 + cup * 2],
    radius=cup // 3, fill="white",
)

img.save("icon.png")
print("✓ icon.png")

# ── ICNS via iconutil ─────────────────────────────────────────
iconset = "AppIcon.iconset"
os.makedirs(iconset, exist_ok=True)

for s in [16, 32, 64, 128, 256, 512]:
    img.resize((s,     s),     Image.LANCZOS).save(f"{iconset}/icon_{s}x{s}.png")
    img.resize((s * 2, s * 2), Image.LANCZOS).save(f"{iconset}/icon_{s}x{s}@2x.png")

r = subprocess.run(
    ["iconutil", "-c", "icns", iconset, "-o", "icon.icns"],
    capture_output=True,
)
if r.returncode == 0:
    print("✓ icon.icns")
else:
    print(f"iconutil Fehler: {r.stderr.decode().strip()}")

# Aufräumen
import shutil
shutil.rmtree(iconset, ignore_errors=True)
