#!/bin/bash
# catfish-calendar Swift binary 编译脚本 (5/21 BL-CALENDAR-EVENTKIT-FFI).
#
# 用法:
#   bash build.sh              # 编译 release
#   bash build.sh --debug      # 编译 debug
#
# 输出: ./catfish-calendar (macOS 通用二进制: x86_64 + arm64)
#
# 10/14: 原来只编本机架构 (arm64), 而 Apple 芯片包和 Intel 包共用这一个文件、build.rs 只在源码变了
# 才重编 —— 打 Intel 包时塞进去的是 arm64 helper, verify-app-arch.sh 拦下。直接出通用二进制, 两种包都对。
#
# Tauri build 时 build.rs 会自动调本脚本 + 把 binary 拷到 Resources/.

set -euo pipefail

cd "$(dirname "$0")"

MODE="${1:-release}"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "⚠️ catfish-calendar 只 macOS 编译. 当前: $(uname). 跳过."
  exit 0
fi

if ! command -v swiftc >/dev/null 2>&1; then
  echo "✗ swiftc 未装. brew install swift 或装 Xcode Command Line Tools (xcode-select --install)."
  exit 1
fi

FLAGS=(-framework EventKit -framework Foundation)
if [[ "$MODE" == "--debug" || "$MODE" == "debug" ]]; then
  FLAGS+=(-g -Onone)
else
  FLAGS+=(-O)
fi

echo "▶ swiftc catfish-calendar.swift ${FLAGS[*]} (x86_64 + arm64) → lipo"
# Xcode 27 的命令行工具不再带 x86_64 版的 Swift 旧系统兼容库 (libswiftCompatibility5x.a 只剩 arm64),
# 按 macOS 10.15 链会缺符号。这个 helper 没用 async / 并发 (只有 DispatchSemaphore + #available),
# 不需要那些回补, 所以 Intel 这一半不链兼容库, 最低系统保持跟 App 一样的 10.15。
swiftc catfish-calendar.swift "${FLAGS[@]}" -target x86_64-apple-macos10.15 -runtime-compatibility-version none \
  -o catfish-calendar-x64
swiftc catfish-calendar.swift "${FLAGS[@]}" -target arm64-apple-macos11 -o catfish-calendar-arm
lipo -create -output catfish-calendar catfish-calendar-x64 catfish-calendar-arm
rm -f catfish-calendar-x64 catfish-calendar-arm

# 给可执行权限
chmod +x catfish-calendar

echo "✓ 编译完成: $(pwd)/catfish-calendar"
ls -lh catfish-calendar

# 简单 smoke test
echo ""
echo "▶ smoke test: ./catfish-calendar (无参数应报 usage 出错)"
./catfish-calendar 2>&1 || echo "  (exit 非 0 正常, 说明 binary 跑得起来)"
