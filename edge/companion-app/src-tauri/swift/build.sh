#!/bin/bash
# catfish-calendar Swift binary 编译脚本 (5/21 BL-CALENDAR-EVENTKIT-FFI).
#
# 用法:
#   bash build.sh              # 编译 release
#   bash build.sh --debug      # 编译 debug
#
# 输出: ./catfish-calendar (macOS arm64 native)
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

echo "▶ swiftc catfish-calendar.swift ${FLAGS[*]} -o catfish-calendar"
swiftc catfish-calendar.swift "${FLAGS[@]}" -o catfish-calendar

# 给可执行权限
chmod +x catfish-calendar

echo "✓ 编译完成: $(pwd)/catfish-calendar"
ls -lh catfish-calendar

# 简单 smoke test
echo ""
echo "▶ smoke test: ./catfish-calendar (无参数应报 usage 出错)"
./catfish-calendar 2>&1 || echo "  (exit 非 0 正常, 说明 binary 跑得起来)"
