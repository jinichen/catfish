#!/usr/bin/env bash
# 开发模式：前端 Vite 热更新 + Rust 后端热编译
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null 2>&1; then
    echo "[catfish-companion] 需要 Node.js + npm" >&2
    exit 1
fi

if [ ! -d node_modules ]; then
    echo "[catfish-companion] 首次启动，npm install ..."
    npm install
fi

exec npm run tauri:dev
