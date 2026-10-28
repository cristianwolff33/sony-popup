#!/bin/bash
# Buduje "Sony Popup.app" i SonyPopup-macOS.dmg w macos/build/.
# Wymaga Xcode albo Command Line Tools (xcode-select --install).
#   ./macos/build_app.sh [wersja]
set -euo pipefail
cd "$(dirname "$0")"
VERSION="${1:-1.0.0}"
APP="build/Sony Popup.app"

echo "==> swift build (release)"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/SonyPopup"

echo "==> składam $APP"
rm -rf build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/SonyPopup"
sed "s/__VERSION__/$VERSION/g" Info.plist > "$APP/Contents/Info.plist"
cp ../wh-*.png "$APP/Contents/Resources/"

echo "==> ikona"
ICONSET="build/AppIcon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s AppIcon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) AppIcon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET"

echo "==> podpis ad-hoc"
codesign --force --deep --sign - "$APP"

echo "==> DMG"
STAGE="build/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Sony Popup" -srcfolder "$STAGE" -ov -format UDZO "build/SonyPopup-macOS.dmg" >/dev/null
rm -rf "$STAGE"

echo "Gotowe:"
echo "  $(pwd)/$APP"
echo "  $(pwd)/build/SonyPopup-macOS.dmg"
