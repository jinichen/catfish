#!/bin/bash
# catfish-audiotap (11/11, 会议录音录线上会议对方的声音): 跟 build-ocr.sh 同一套, 出 x86_64 + arm64 通用二进制。
# process tap 要 macOS 14.2、ScreenCaptureKit 声音要 13: 两半按 App 最低系统链, 运行时 #available 判断。
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname)" != "Darwin" ]]; then echo "⚠️ catfish-audiotap 只 macOS 编译, 跳过"; exit 0; fi
FLAGS=(-O -framework CoreAudio -framework AVFoundation -framework AppKit -framework ScreenCaptureKit -framework CoreMedia -framework Foundation)
swiftc catfish-audiotap.swift "${FLAGS[@]}" -target x86_64-apple-macos10.15 -runtime-compatibility-version none -o catfish-audiotap-x64
swiftc catfish-audiotap.swift "${FLAGS[@]}" -target arm64-apple-macos11 -o catfish-audiotap-arm
lipo -create -output catfish-audiotap catfish-audiotap-x64 catfish-audiotap-arm
rm -f catfish-audiotap-x64 catfish-audiotap-arm
chmod +x catfish-audiotap
echo "✓ $(pwd)/catfish-audiotap"; lipo -archs catfish-audiotap
