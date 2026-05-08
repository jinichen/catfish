#!/usr/bin/env bash
# BL-WIN1 (5/8) — 在 macOS 上 cross-compile Companion Tauri app 到 Windows .exe.
#
# 不出 MSI installer (那个需要 Windows + WiX). 出 raw .exe, 拷到 Windows 真机
# 跑能验证 codebase 是否在 Windows target 下编得过 + 启动得起来. demo 5/14 真要
# 用还是建议直接在 Windows 真机 build.
#
# 前提:
#   - macOS (Apple Silicon 或 Intel 都行)
#   - rustup (curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh)
#   - Node 20+ + npm (vite build 前端)
#   - Homebrew (装 mingw-w64)
#
# 用法:
#   cd ~/person_task/catfish/edge/companion-app
#   ./scripts/build-windows.sh
#
# 输出:
#   src-tauri/target/x86_64-pc-windows-gnu/release/catfish-companion-app.exe
#
# 已知限制:
#   - 不能签名 (signtool 只 Windows)
#   - 不能出 MSI / NSIS (cross-bundler 在 mac 上不全)
#   - WebView2 runtime 装 / 检测留给真机首启
#   - speech (whisper.cpp 录音) Windows 是 stub, 不会工作 — demo 用不上没事

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "🐟 catfish Companion Windows cross-build"
echo "===================================="
echo "工作目录: $ROOT"
echo

# ── 1. 检查 rustup + 加 target ──────────────────────────────────
if ! command -v rustup >/dev/null 2>&1; then
    echo "❌ 缺 rustup. 装一下:"
    echo "   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

if ! rustup target list --installed | grep -q '^x86_64-pc-windows-gnu$'; then
    echo "📦 加 Rust target x86_64-pc-windows-gnu..."
    rustup target add x86_64-pc-windows-gnu
fi

# ── 2. 检查 mingw-w64 ────────────────────────────────────────────
if ! command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "📦 装 mingw-w64 (cross linker)..."
        brew install mingw-w64
    else
        echo "❌ 缺 mingw-w64 + 没找到 brew. 手动装一下"
        exit 1
    fi
fi

# ── 3. 装 frontend deps + 编 ────────────────────────────────────
echo
echo "📦 装 npm deps..."
npm install --silent

echo
echo "🔨 编 frontend (vite build → dist/)..."
npm run build

# ── 4. cargo cross-build ────────────────────────────────────────
echo
echo "🔨 cargo cross-build to Windows .exe..."
echo "    (第一次会装 ~500MB cross-compiled deps, 慢, 喝杯咖啡)"
echo
cd src-tauri

cargo build --release --target x86_64-pc-windows-gnu

# ── 5. 报告 ──────────────────────────────────────────────────────
EXE="target/x86_64-pc-windows-gnu/release/catfish-companion-app.exe"
echo
echo "===================================="
if [ -f "$EXE" ]; then
    SIZE="$(du -h "$EXE" | cut -f1)"
    echo "✓ 编译成功!"
    echo "  路径: $ROOT/src-tauri/$EXE"
    echo "  大小: $SIZE"
    echo
    echo "下一步:"
    echo "  1. 拷到 Windows 真机 (parallels / vmware / 同事机)"
    echo "  2. 装 WebView2 Runtime (Win11 自带, Win10 装一下: https://developer.microsoft.com/microsoft-edge/webview2)"
    echo "  3. 直接双击 .exe 跑, 看启动 + 登录 + 仪表盘有没有界面"
    echo "  4. gateway / tool-bridge 这两个 Python 服务 Windows 真机上单独跑 (跟 mac 一样 pip install -e .)"
else
    echo "✗ 没找到 .exe, 看上面错误"
    echo "常见错误:"
    echo "  - linking 失败 → mingw-w64 没装好"
    echo "  - rusqlite bundled C 编不过 → mingw 工具链版本太旧, brew upgrade mingw-w64"
    echo "  - tauri-build 失败 → frontend dist/ 没出, npm run build 先跑"
    exit 1
fi
