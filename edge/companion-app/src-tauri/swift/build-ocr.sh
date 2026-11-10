#!/bin/bash
# catfish-ocr Swift binary 编译 (11/10, 本地搜索读图片 / 扫描件里的字)。跟 build.sh (catfish-calendar) 同一套:
# 出 x86_64 + arm64 通用二进制, Apple 芯片包和 Intel 包共用; build.rs 在源码比二进制新时调本脚本。
# Vision 文字识别要 macOS 10.15 才有 API、11 才认中文: 两半都按 App 的最低系统 10.15 链,
# 运行时 #available(macOS 11) 判断, 老系统上报 "needs macOS 11"。
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname)" != "Darwin" ]]; then echo "⚠️ catfish-ocr 只 macOS 编译, 跳过"; exit 0; fi
FLAGS=(-O -framework Vision -framework AppKit -framework Foundation)
swiftc catfish-ocr.swift "${FLAGS[@]}" -target x86_64-apple-macos10.15 -runtime-compatibility-version none -o catfish-ocr-x64
swiftc catfish-ocr.swift "${FLAGS[@]}" -target arm64-apple-macos11 -o catfish-ocr-arm
lipo -create -output catfish-ocr catfish-ocr-x64 catfish-ocr-arm
rm -f catfish-ocr-x64 catfish-ocr-arm
chmod +x catfish-ocr
echo "✓ $(pwd)/catfish-ocr"; lipo -archs catfish-ocr
