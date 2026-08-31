#!/usr/bin/env bash
# Build the zero-dependency chat export reader wheel for a macOS resource directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPANION="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE="$(cd "$COMPANION/../wechat-reader" && pwd)"
ARCH="${1:-aarch64}"
DEST="${2:-$COMPANION/src-tauri/resources/mac-$ARCH}"
STAGE="/tmp/catfish-wechat-reader-dist-$ARCH"
ARCHIVE="$DEST/catfish-wechat-reader-dist.tar.gz"

rm -rf "$STAGE"
mkdir -p "$STAGE" "$DEST"
python3 "$SOURCE/scripts/build_wheel.py" --output-dir "$STAGE" >/dev/null

WHEEL_COUNT="$(find "$STAGE" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')"
if [ "$WHEEL_COUNT" != "1" ]; then
    echo "ERROR: expected exactly one catfish-wechat-reader wheel, got $WHEEL_COUNT" >&2
    exit 1
fi
tar czf "$ARCHIVE" -C "$STAGE" .
tar tzf "$ARCHIVE" | grep -q '\.whl$'
mkdir -p "$COMPANION/src-tauri/resources/windows"
cp "$ARCHIVE" "$COMPANION/src-tauri/resources/windows/catfish-wechat-reader-dist.tar.gz"
rm -rf "$STAGE"
echo "  ✓ $(basename "$ARCHIVE") ($(du -h "$ARCHIVE" | cut -f1))"
