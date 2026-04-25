#!/usr/bin/env bash
# 打包 Windows 版本（在 Windows 主机或 Linux+wine 环境跑）
# 输出 .msi 安装包 + .exe 可执行
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d node_modules ]; then
    npm install
fi

exec npm run tauri:build -- --target x86_64-pc-windows-msvc
