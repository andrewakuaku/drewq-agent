#!/usr/bin/env bash
# Build the DREWQ Reader Agent for macOS
# Run: bash build.sh
# Output: dist/DREWQ\ Reader.app

set -e
cd "$(dirname "$0")"

echo "==> Installing dependencies…"
pip install -r requirements.txt pyinstaller

echo "==> Generating placeholder icons (replace with real assets before release)…"
python3 - <<'EOF'
from PIL import Image, ImageDraw

def make_icon(size, path):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size-4, size-4], fill="#1a1a1a")
    draw.ellipse([size//4, size//4, size*3//4, size*3//4], fill="#22c55e")
    img.save(path)

import os; os.makedirs("assets", exist_ok=True)
make_icon(256, "assets/icon.png")
make_icon(256, "assets/icon.ico")
try:
    # icns requires multiple sizes — use png as fallback
    import shutil; shutil.copy("assets/icon.png", "assets/icon.icns")
except: pass
print("Icons generated.")
EOF

echo "==> Building with PyInstaller…"
pyinstaller drewq.spec --clean --noconfirm

echo ""
echo "✓ Build complete."
echo "  macOS: dist/DREWQ Reader.app"
echo ""
echo "To run: open 'dist/DREWQ Reader.app'"
