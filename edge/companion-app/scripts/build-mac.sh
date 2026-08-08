#!/usr/bin/env bash
# 打包 macOS 版本 —— 8/9 起改成明确报错的向导, 不再自己猜架构。
#
# 老实现是 `npm run tauri:build -- --target universal-apple-darwin`。那条 npm
# script 早就被停用了 (package.json:18 直接 echo + exit 1, 因为 universal 包
# 不含内嵌运行时), 于是这个脚本只会打出:
#
#     ❌ 这条命令已停用 —— 它打出的包不含内嵌运行时(hermes/python/node).
#     sh: line 0: exit: too many arguments      ← 还多一句莫名其妙的报错
#
# 那句 "too many arguments" 是 `-- --target ...` 被拼到停用脚本的 `exit 1`
# 后面导致的, 跟真实问题毫无关系, 纯粹误导排查方向。
#
# 停用是对的 (universal 包确实不能用), 但留一个必然失败的入口脚本不对 ——
# 它比没有更糟: 有人会照着文档跑, 拿到一条指错方向的报错。
set -euo pipefail
cd "$(dirname "$0")/.."

cat <<'EOF'
本脚本不再直接打包 —— macOS 必须按架构分别打 (universal 包不含内嵌运行时)。

请按目标架构选一条:

  Apple Silicon (M 系列):
    bash scripts/build-mac-resources.sh aarch64
    npm run tauri:build:arm64

  Intel:
    bash scripts/build-mac-resources.sh x64
    npm run tauri:build:x64

  要签名 + 公证 (对外分发):
    npm run tauri:build:sign-notarize:arm64

两步都要跑: build-mac-resources.sh 准备内嵌运行时 (hermes / python / node /
chromium), tauri:build:* 才把它们打进 .app 和 .dmg。
EOF
exit 1
