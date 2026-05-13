#!/usr/bin/env bash
# 把 catfish 的 SOUL.md 接管 ~/.hermes/SOUL.md，让 Hermes 加载鲶鱼/小鲶身份。
#
# 安全设计：
#   - 如果 ~/.hermes/SOUL.md 已存在且不是我们的软链 -> 备份成 SOUL.md.before-catfish
#   - 用软链而不是复制：catfish 仓库改 SOUL.md 立刻生效，方便迭代
#   - 幂等：重复跑只会刷新软链不会重复备份
#
# 卸载：bash uninstall.sh
#   会删软链；如果有 SOUL.md.before-catfish 备份会还原

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOUL_SRC="$SCRIPT_DIR/SOUL.md"
SOUL_DST="$HOME/.hermes/SOUL.md"
BACKUP="$HOME/.hermes/SOUL.md.before-catfish"

# 5/13 拆分: 客户特定段独立文件 (CATFISH_CUSTOMER env 决定 gateway 注入哪份)
# 默认 ffcs (兼容现有). 别家客户加 SOUL_BYD.md / SOUL_MEITUAN.md 同模式.
# bash 3.2 (mac 默认) 不支持 ${VAR^^}, 用 tr 大写
CUSTOMER="${CATFISH_CUSTOMER:-ffcs}"
CUSTOMER_UPPER="$(echo "$CUSTOMER" | tr '[:lower:]' '[:upper:]')"
SOUL_CUSTOMER_SRC="$SCRIPT_DIR/SOUL_${CUSTOMER_UPPER}.md"   # SOUL_FFCS.md
SOUL_CUSTOMER_DST="$HOME/.hermes/SOUL_${CUSTOMER_UPPER}.md"

GREEN='\033[32m'; YELLOW='\033[33m'; RESET='\033[0m'
ok()   { echo -e "    ${GREEN}OK${RESET} $*"; }
warn() { echo -e "    ${YELLOW}警告${RESET} $*"; }

echo "=== Catfish identity (SOUL.md) installer ==="

if [ ! -f "$SOUL_SRC" ]; then
    echo "源文件不存在：$SOUL_SRC"
    exit 1
fi

mkdir -p "$(dirname "$SOUL_DST")"

# 备份现有 SOUL.md（仅当不是我们的软链时）
if [ -L "$SOUL_DST" ]; then
    target="$(readlink "$SOUL_DST")"
    if [ "$target" = "$SOUL_SRC" ]; then
        ok "已经是 catfish SOUL，无需重装"
        exit 0
    fi
    # 是其他软链，删掉准备替换
    rm "$SOUL_DST"
elif [ -f "$SOUL_DST" ]; then
    if [ ! -f "$BACKUP" ]; then
        cp "$SOUL_DST" "$BACKUP"
        ok "已备份原 SOUL.md → $BACKUP"
    else
        warn "$BACKUP 已存在，不覆盖（保留你最早一次的原始 SOUL.md）"
    fi
    rm "$SOUL_DST"
fi

ln -s "$SOUL_SRC" "$SOUL_DST"
ok "$SOUL_DST → $SOUL_SRC"

# 5/13: 同步装客户特定 SOUL (CATFISH_CUSTOMER=ffcs 装 SOUL_FFCS.md)
if [ -f "$SOUL_CUSTOMER_SRC" ]; then
    if [ -L "$SOUL_CUSTOMER_DST" ] && [ "$(readlink "$SOUL_CUSTOMER_DST")" = "$SOUL_CUSTOMER_SRC" ]; then
        ok "客户特定 SOUL 已是最新软链 ($CUSTOMER)"
    else
        rm -f "$SOUL_CUSTOMER_DST"
        ln -s "$SOUL_CUSTOMER_SRC" "$SOUL_CUSTOMER_DST"
        ok "$SOUL_CUSTOMER_DST → $SOUL_CUSTOMER_SRC (客户=$CUSTOMER)"
    fi
else
    warn "客户特定 SOUL 不存在: $SOUL_CUSTOMER_SRC (CATFISH_CUSTOMER=$CUSTOMER), 跳过"
fi

# heredoc 里混中文 + $var 在某些 bash 版本下会踩 set -u 的 Unicode 边界 bug，
# 临时关掉 -u，EOF 后恢复。
set +u
cat <<EOF

=== 装好了 ===

下一步：
    SOUL.md 是每条消息现读的，不用重启 hermes
    在 hermes 里发：你是谁？
    看回复是不是以"我是小鲶"开头

预期：
    回复以"我是小鲶"开头，不再出现 "Hermes" / "Nous Research" / "AI 助手"
    语气直接、不堆套话、跟你像同事讨论

不满意改 SOUL.md：
    编辑 $SOUL_SRC（catfish 仓库内，软链立即生效）
    下一条消息就用新 SOUL

卸载：
    bash $SCRIPT_DIR/uninstall.sh
EOF
set -u
