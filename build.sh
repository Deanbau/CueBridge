#!/usr/bin/env bash
# Build CueBridge macOS DMG.
# source .venv/bin/activate && bash build.sh

set -euo pipefail

echo "==> Generating icon…"
pip install -q pillow
python assets/create_icon.py

echo "==> Converting to ICNS…"
rm -rf CueBridge.iconset
mkdir CueBridge.iconset
for size in 16 32 128 256 512; do
  sips -z $size $size assets/icon.png \
    --out CueBridge.iconset/icon_${size}x${size}.png >/dev/null
  sips -z $((size*2)) $((size*2)) assets/icon.png \
    --out CueBridge.iconset/icon_${size}x${size}@2x.png >/dev/null
done
iconutil -c icns CueBridge.iconset -o assets/icon.icns
rm -rf CueBridge.iconset

echo "==> Building app bundle…"
pyinstaller --onedir --windowed --name CueBridge \
  --collect-all nicegui \
  --icon assets/icon.icns \
  --add-data "assets/icon.png:assets" \
  --add-data "assets/icon_launcher.png:assets" \
  main.py

echo "==> Creating DMG…"
mkdir -p dmg_staging
cp -r dist/CueBridge.app dmg_staging/
hdiutil create \
  -volname "CueBridge" \
  -srcfolder dmg_staging \
  -ov -format UDZO \
  dist/CueBridge-macos.dmg
rm -rf dmg_staging

echo "Done: dist/CueBridge-macos.dmg"
