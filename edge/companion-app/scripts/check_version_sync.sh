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

# P3.5.157 (7/1): 老 \s 元字符 GNU 支持 macOS BSD 不支持 → 本机跑挂, CI 上一直
# PASS 是因为 Linux GNU 支持. 改用 POSIX [[:space:]] 跨平台通用.
# 抓 package.json "version": "x.y.z"
PKG_VER="$(grep -E '^[[:space:]]*"version"[[:space:]]*:' "$PACKAGE_JSON" | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
# 抓 Cargo.toml 顶部 [package] 段的 version = "x.y.z" (注意不要抓到 dependencies 段的)
CARGO_VER="$(awk '/^\[package\]/{f=1} f && /^version[[:space:]]*=/{gsub(/.*version[[:space:]]*=[[:space:]]*"/, ""); gsub(/".*/, ""); print; exit}' "$CARGO_TOML")"
# 抓 tauri.conf.json "version": "x.y.z"
TAURI_VER="$(grep -E '^[[:space:]]*"version"[[:space:]]*:' "$TAURI_CONF" | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"

if [ -z "$PKG_VER" ] || [ -z "$CARGO_VER" ] || [ -z "$TAURI_VER" ]; then
    echo "❌ 版本号解析失败" >&2
    echo "  package.json: '$PKG_VER'" >&2
    echo "  Cargo.toml:   '$CARGO_VER'" >&2
    echo "  tauri.conf:   '$TAURI_VER'" >&2
    exit 2
fi

if [ "$PKG_VER" = "$CARGO_VER" ] && [ "$CARGO_VER" = "$TAURI_VER" ]; then
    echo "✓ Companion 版本一致: $PKG_VER"

    # P3.5.157 (7/1 鸿波 catch "版本一直没跟 hermes 同步"): 加 .hermes-target-version
    # 强 check. 5/18 → 6/21 P3.5.47 (hermes v0.17 audit) → 7/1 都漂着 0.15.2 没人
    # 发现 — 原因是 BL-CATFISH-HERMES-VERSION-SYNC-B 只 warn 不 fail + CI runner 上没
    # hermes 直接跳过. 改成: 仓库里维护一份 .hermes-target-version, CI 上也强制校.
    #
    # 每次 hermes upgrade audit (docs/HERMES-*-UPGRADE-RUNBOOK.md) 完成后, 顺手
    # update .hermes-target-version + bump 3 处 companion. CI 强制拦不同步.
    TARGET_VER_FILE="$COMPANION_DIR/.hermes-target-version"
    if [ -f "$TARGET_VER_FILE" ]; then
        TARGET_VER="$(head -1 "$TARGET_VER_FILE" | tr -d '[:space:]')"
        if [ -n "$TARGET_VER" ]; then
            if [ "$TARGET_VER" = "$PKG_VER" ]; then
                echo "✓ 跟 hermes target 一致: $TARGET_VER (.hermes-target-version)"
            else
                echo "❌ Companion 版本 $PKG_VER != hermes target $TARGET_VER"
                echo "   ($TARGET_VER_FILE)"
                echo ""
                echo "   两种修法:"
                echo "   A. bump 3 处 companion 版本 → $TARGET_VER (推荐)"
                echo "      sed -i '' 's/\"$PKG_VER\"/\"$TARGET_VER\"/' package.json"
                echo "      sed -i '' 's/\"$PKG_VER\"/\"$TARGET_VER\"/' src-tauri/tauri.conf.json"
                echo "      sed -i '' 's/\"$PKG_VER\"/\"$TARGET_VER\"/' src-tauri/Cargo.toml"
                echo "   B. update .hermes-target-version → $PKG_VER (如果 hermes 还没升)"
                exit 1
            fi
        fi
    fi

    # BL-CATFISH-HERMES-VERSION-SYNC-B (6/1 鸿波): 本机有 hermes 时额外 sanity
    # check — 本机装的 hermes 跟 target 是不是也一致. 不 fail (dev 本地可能刻意
    # 装老 hermes 做兼容 test), 只 warn.
    HERMES_PYPROJECT="${HOME}/.hermes/hermes-agent/pyproject.toml"
    if [ -f "$HERMES_PYPROJECT" ]; then
        HERMES_VER="$(awk -F'"' '/^version[[:space:]]*=/ {print $2; exit}' "$HERMES_PYPROJECT" 2>/dev/null)"
        if [ -n "$HERMES_VER" ] && [ "$HERMES_VER" != "$PKG_VER" ]; then
            echo "⚠ 本机装的 hermes 跟 companion 不一致 (不 fail, 仅提醒):"
            echo "  catfish: $PKG_VER"
            echo "  hermes : $HERMES_VER  ($HERMES_PYPROJECT)"
            echo "  如果是新 hermes → update .hermes-target-version 触发 CI red 提醒 bump."
        fi
    fi

    exit 0
fi

echo "❌ Companion 三处版本号不一致:"
echo "  package.json    : $PKG_VER"
echo "  Cargo.toml      : $CARGO_VER"
echo "  tauri.conf.json : $TAURI_VER"
echo ""
echo "改这三处都到同一个 version (一般跟 hermes upstream 同步, 见 docs/HERMES-014-UPGRADE-RUNBOOK.md)."
exit 1
