#!/usr/bin/env bash
# Companion 三处版本号一致性检查 (BL-COMPANION-VERSION-SYNC, 5/18).
#
# 背景: Companion 同时被 Tauri (Rust Cargo.toml + tauri.conf.json) 和前端
# (package.json) 引用, 三处版本号必须一致, 否则:
#   - tauri bundle 的 .app / .msi / .dmg 写的版本号不对
#   - 前端关于页 vs 系统"关于本机"对不上
#   - 升级检查脚本误判 (服务器看 package.json, 本地看 Cargo.toml)
#
# 这个脚本不强制要跟 hermes 同步 (那是 release runbook 的事 — hermes 装在
# ~/.hermes/ 不在仓库里, CI 看不到). 只 lint 仓库内 3 处一致.
#
# 用法:
#   bash edge/companion-app/scripts/check_version_sync.sh
#
# 退出码:
#   0 = 三处一致
#   1 = 不一致 (打印差异)
#   2 = 读取失败 (文件缺 / 解析不出)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PACKAGE_JSON="$COMPANION_DIR/package.json"
CARGO_TOML="$COMPANION_DIR/src-tauri/Cargo.toml"
TAURI_CONF="$COMPANION_DIR/src-tauri/tauri.conf.json"

for f in "$PACKAGE_JSON" "$CARGO_TOML" "$TAURI_CONF"; do
    if [ ! -f "$f" ]; then
        echo "❌ 找不到 $f" >&2
        exit 2
    fi
done

# 抓 package.json "version": "x.y.z"
PKG_VER="$(grep -E '^\s*"version"\s*:' "$PACKAGE_JSON" | head -1 | sed -E 's/.*"version"\s*:\s*"([^"]+)".*/\1/')"
# 抓 Cargo.toml 顶部 [package] 段的 version = "x.y.z" (注意不要抓到 dependencies 段的)
CARGO_VER="$(awk '/^\[package\]/{f=1} f && /^version\s*=/{gsub(/.*version\s*=\s*"/, ""); gsub(/".*/, ""); print; exit}' "$CARGO_TOML")"
# 抓 tauri.conf.json "version": "x.y.z"
TAURI_VER="$(grep -E '^\s*"version"\s*:' "$TAURI_CONF" | head -1 | sed -E 's/.*"version"\s*:\s*"([^"]+)".*/\1/')"

if [ -z "$PKG_VER" ] || [ -z "$CARGO_VER" ] || [ -z "$TAURI_VER" ]; then
    echo "❌ 版本号解析失败" >&2
    echo "  package.json: '$PKG_VER'" >&2
    echo "  Cargo.toml:   '$CARGO_VER'" >&2
    echo "  tauri.conf:   '$TAURI_VER'" >&2
    exit 2
fi

if [ "$PKG_VER" = "$CARGO_VER" ] && [ "$CARGO_VER" = "$TAURI_VER" ]; then
    echo "✓ Companion 版本一致: $PKG_VER"

    # BL-CATFISH-HERMES-VERSION-SYNC-B (6/1 鸿波): 本机有 hermes 时也检 hermes
    # 版本, 不一致 warn 但不 fail (CI runner 上没 hermes 跳过, 本机 dev 看到 warn
    # 立刻 bump catfish 跟上).
    # 设计意图: 5/18 BL-COMPANION-VERSION-SYNC 时同步靠 runbook §3b 人手, 5/18→6/1
    # 间 hermes 0.14→0.15.1 但 catfish 没跟. 人手 runbook 必漂, 改成 lint 见效.
    HERMES_PYPROJECT="${HOME}/.hermes/hermes-agent/pyproject.toml"
    if [ -f "$HERMES_PYPROJECT" ]; then
        HERMES_VER="$(awk -F'"' '/^version[[:space:]]*=/ {print $2; exit}' "$HERMES_PYPROJECT" 2>/dev/null)"
        if [ -n "$HERMES_VER" ]; then
            if [ "$HERMES_VER" = "$PKG_VER" ]; then
                echo "✓ hermes 版本同步: $HERMES_VER"
            else
                echo "⚠ hermes 版本漂移 (不 fail, 仅提醒):"
                echo "  catfish: $PKG_VER"
                echo "  hermes : $HERMES_VER  ($HERMES_PYPROJECT)"
                echo "  按 docs/HERMES-014-UPGRADE-RUNBOOK.md §3b 跑 sed bump."
            fi
        fi
    fi
    # 没 hermes (CI runner) → 静默跳过, 不 fail.
    exit 0
fi

echo "❌ Companion 三处版本号不一致:"
echo "  package.json    : $PKG_VER"
echo "  Cargo.toml      : $CARGO_VER"
echo "  tauri.conf.json : $TAURI_VER"
echo ""
echo "改这三处都到同一个 version (一般跟 hermes upstream 同步, 见 docs/HERMES-014-UPGRADE-RUNBOOK.md)."
exit 1
