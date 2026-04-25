#!/usr/bin/env bash
# 打包 macOS 版本（输出 .dmg + .app）
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d node_modules ]; then
    npm install
fi

# 通过 --target 同时打包 Intel + Apple Silicon (universal)
exec npm run tauri:build -- --target universal-apple-darwin
