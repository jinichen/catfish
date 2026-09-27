#!/usr/bin/env bash
# 构建 catfish-email 资源包 (wheel + hermes skill) —— build-mac-resources.sh 调用。
#
# 9/28 从 build-mac-resources.sh 拆出来: 那个文件 799 行, 再加一步 AppleScript
# 编译检查就越过 800 行红线。拆这一块是因为它自成一段 (跟隔壁
# build-wechat-reader-resource.sh 同一个形状), 下面的注释原样搬过来。
#
# 用法: bash build-email-resource.sh <arch> <resources 目录>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPANION="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCH="${1:-aarch64}"
RESOURCES="${2:-$COMPANION/src-tauri/resources/mac-$ARCH}"
mkdir -p "$RESOURCES"

# ─── catfish-email 源码包 ──────────────────────────────────────────
#
# 7/30 达华现场: 员工装完 Companion, 邮件 tab 挂, 界面提示
#     "CLI 没装 (bash edge/email-agent/install.sh)"
# —— 而员工手里只有一个 dmg, **根本没有 edge/email-agent/ 这个目录**。
#
# 查下来这东西从来没进过交付链路:
#   - hermes-agent-bundle.tar.gz 里搜 catfish-email → 0 条
#   - 本脚本里搜 email / pip install → 0 处
#   - 而 Companion 的 link_catfish_email_bin() 只负责建软链, 前提是
#     venv/bin/catfish-email 已存在 —— 于是它永远走 warn 分支静默跳过
#
# 为什么一直没人发现: edge/email-agent/install.sh 用的是
# `pip install -e "$SCRIPT_DIR"` (editable), 要求源码目录长期在 ——
# 那是开发机的装法, 天生进不了一个要分发的包。它只在自己机器上跑过。
#
# 为什么打成 tar 而不是塞进 hermes bundle:
#   bundle 是 hermes 上游的源码快照 (装机时才现建 venv), 往里塞我们自己的
#   东西会污染"这份 bundle == 那个 pin 的 commit"这个契约, 也会让
#   .catfish-hermes-version 的核对失去意义。单独一个 tar, 来路清楚。
#
# ⚠ 为什么装的是 **wheel** 而不是源码目录:
#   hermes 的 venv 是 `uv venv` 建的, **不带 pip / setuptools**。装一个源码
#   目录要先跑构建后端, uv 会去联网拉 setuptools —— 恰好在内网机器上失败,
#   而内网正是这功能要服务的场景。构建期就做成 wheel, 装机时纯解包拷贝,
#   零构建零联网。
#
# 这个包零运行时依赖 (pyproject.toml `dependencies = []`, 只用标准库),
# 所以一个 py3-none-any 的 wheel 走遍两个架构。
echo ""
echo "→ 构建 catfish-email wheel"
EMAIL_SRC="$COMPANION/../email-agent"
EMAIL_TAR="$RESOURCES/catfish-email-dist.tar.gz"
if [ ! -f "$EMAIL_SRC/pyproject.toml" ]; then
    echo "❌ 找不到 email-agent 源码: $EMAIL_SRC"
    echo "   邮件功能会整个缺失 —— 不允许打出这样的包。"
    exit 1
fi
# 9/28: 「邮件」App 用的 AppleScript 先在本机编译一遍 (不运行)。开发和 CI 都在
# Linux 上, 编译不了 —— 1.0.44 的一处语法错一直带到员工机上才暴露, 读信整个挂掉。
python3 "$EMAIL_SRC/scripts/check_applescript.py" || {
    echo "❌ 有 AppleScript 编译不过, 不打包 (输出里标出了出错的位置)"
    exit 1
}
EMAIL_STAGE="/tmp/catfish-email-dist-$ARCH"
rm -rf "$EMAIL_STAGE" && mkdir -p "$EMAIL_STAGE"
# --no-deps: 它本来就零依赖, 显式写死免得哪天有人加了依赖却没人注意到
python3 -m pip wheel --no-deps --wheel-dir "$EMAIL_STAGE" "$EMAIL_SRC" >/dev/null || {
    echo "❌ 构建 catfish-email wheel 失败"
    echo "   本机需要 python3 + pip (只在构建期用, 员工机不需要)"
    exit 1
}
WHEEL_COUNT=$(find "$EMAIL_STAGE" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')
if [ "$WHEEL_COUNT" != "1" ]; then
    echo "❌ 期望正好 1 个 wheel, 实际 $WHEEL_COUNT 个 —— 装机时无法确定装哪个"
    exit 1
fi
# skill 一并带上: 没有它 CLI 装了但模型不知道有这个工具
cp -R "$EMAIL_SRC/hermes-skill" "$EMAIL_STAGE/hermes-skill"
tar czf "$EMAIL_TAR" -C "$EMAIL_STAGE" .
# 校验产出 —— 装机时才发现缺东西就晚了
if ! tar tzf "$EMAIL_TAR" | grep -q '\.whl$'; then
    echo "❌ $EMAIL_TAR 里没有 wheel"
    exit 1
fi
if ! tar tzf "$EMAIL_TAR" | grep -q 'hermes-skill/catfish-email/SKILL.md$'; then
    echo "❌ $EMAIL_TAR 里没有 hermes-skill/catfish-email/SKILL.md"
    exit 1
fi
rm -rf "$EMAIL_STAGE"
echo "  ✓ $(basename "$EMAIL_TAR") ($(du -h "$EMAIL_TAR" | cut -f1))"
