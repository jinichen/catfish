#!/usr/bin/env bash
# 把 Hermes Agent 源码里的 "Nous Research" 品牌字样替换成鲶鱼 / 小鲶。
#
# 工作模式：
#   1. 备份要改的文件到 ~/.hermes/hermes-agent/<file>.catfish-backup
#   2. 用 sed 做字面量替换（不是正则，避免误伤）
#   3. 写一个 ~/.hermes/.catfish-rebrand-manifest 记录所有改过的文件
#   4. unrebrand.sh 读 manifest 还原所有备份
#
# 每次 hermes update 后需要重新跑（hermes update 会把源码拉成原版）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_AGENT_DIR="$HOME/.hermes/hermes-agent"
MANIFEST="$HOME/.hermes/.catfish-rebrand-manifest"
ASCII_PATH="$SCRIPT_DIR/assets/catfish-ascii.txt"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
ok()   { echo "    ${GREEN}OK${RESET} $*"; }
warn() { echo "    ${YELLOW}警告${RESET} $*"; }
err()  { echo "    ${RED}错误${RESET} $*" >&2; }
step() { echo "${BOLD}[$1]${RESET} $2"; }

if [ ! -d "$HERMES_AGENT_DIR" ]; then
    err "找不到 $HERMES_AGENT_DIR"
    exit 1
fi

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
    esac
done

# --------------------------------------------------------
# 工具：替换 + 备份 + 记 manifest
# --------------------------------------------------------

# replace_in_file <file> <from> <to>
replace_in_file() {
    local file="$1"
    local from="$2"
    local to="$3"

    if [ ! -f "$file" ]; then
        return 0
    fi

    # 用 grep -F (固定字符串，非正则) 检查是否含 from
    if ! grep -qF -- "$from" "$file" 2>/dev/null; then
        return 0
    fi

    # 备份（如果还没备份过这个文件）
    local backup="${file}.catfish-backup"
    if [ ! -f "$backup" ]; then
        if [ "$DRY_RUN" = "0" ]; then
            cp "$file" "$backup"
        fi
        # 记到 manifest 里
        if [ "$DRY_RUN" = "0" ]; then
            mkdir -p "$(dirname "$MANIFEST")"
            grep -qxF "$file" "$MANIFEST" 2>/dev/null || echo "$file" >> "$MANIFEST"
        fi
    fi

    if [ "$DRY_RUN" = "1" ]; then
        echo "    [DRY] 会替换 $(basename "$file"): $from → $to"
        return 0
    fi

    # 用 python 替换避免 sed 在不同 OS / 不同字符集下的兼容坑
    python3 - "$file" "$from" "$to" <<'PYEOF'
import sys
path, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "r", encoding="utf-8") as f:
    txt = f.read()
new = txt.replace(src, dst)
if new != txt:
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    sys.exit(0)
sys.exit(1)
PYEOF
    if [ $? -eq 0 ]; then
        ok "改 $(basename "$file"):  $from → $to"
    fi
}

# --------------------------------------------------------
# 1. 字符串替换（精确）
# --------------------------------------------------------
# 这些 file_globs 等 grep 输出后填精准的，目前先填几个最可能的位置。
# rebrand.sh 即使 file 不存在也只是 skip，安全。

step "1/3" "替换品牌字符串"

# 候选位置（基于 Hermes 0.10 源码经验猜测，等 grep 结果再校准）
CANDIDATES=(
    "$HERMES_AGENT_DIR/hermes_cli/branding.py"
    "$HERMES_AGENT_DIR/hermes_cli/banner.py"
    "$HERMES_AGENT_DIR/hermes_cli/cli.py"
    "$HERMES_AGENT_DIR/hermes_cli/main.py"
    "$HERMES_AGENT_DIR/hermes_cli/__init__.py"
    "$HERMES_AGENT_DIR/agent/agent.py"
    "$HERMES_AGENT_DIR/ui-tui/src/components/branding.tsx"
    "$HERMES_AGENT_DIR/ui-tui/src/components/banner.tsx"
    "$HERMES_AGENT_DIR/ui-tui/src/lib/banner.ts"
    "$HERMES_AGENT_DIR/ui-tui/src/lib/welcome.ts"
)

# 替换映射 from|||to|||comment
REPLACEMENTS=(
    "Welcome to Hermes Agent!|||🐟 鲶鱼来了。直接说事。"
    "Goodbye! ⚕|||再见 🐟"
    "Nous Research|||鲶鱼平台"
    "Hermes Agent v|||Catfish v0.1.0  ·  hermes-"
    "hermes sessions browse|||catfish sessions browse"
    "hermes update|||catfish update"
    "hermes plugins|||catfish plugins"
    "hermes mcp|||catfish mcp"
    "hermes chat|||catfish chat"
    "⚕|||🐟"
)

for f in "${CANDIDATES[@]}"; do
    for rule in "${REPLACEMENTS[@]}"; do
        IFS='|||' read -r from _ to <<<"$rule"
        # 上一行 read 因为 IFS 多字符的兼容问题用 awk 拆更稳：
        from="$(echo "$rule" | awk -F '\\|\\|\\|' '{print $1}')"
        to="$(echo "$rule" | awk -F '\\|\\|\\|' '{print $2}')"
        replace_in_file "$f" "$from" "$to"
    done
done

# --------------------------------------------------------
# 2. ASCII art 替换（待 grep 定位后填）
# --------------------------------------------------------

step "2/3" "ASCII art 替换"
warn "ASCII art 替换需要先用 grep 定位它在源码里的位置（见 string-map.yaml），跳过"

# --------------------------------------------------------
# 3. 总结
# --------------------------------------------------------

step "3/3" "完成"
if [ "$DRY_RUN" = "1" ]; then
    echo "    DRY-RUN 模式，没真改任何文件。去掉 -n 实跑。"
else
    if [ -f "$MANIFEST" ]; then
        n=$(wc -l < "$MANIFEST" | tr -d ' ')
        ok "$n 个文件被改过，备份在 .catfish-backup"
    else
        warn "manifest 为空 —— 可能候选文件路径都没命中（hermes 版本不同？）"
    fi
fi

cat <<EOF

预期看到的 Hermes 启动 banner 变化：
    Hermes Agent v0.10.0       →  Catfish v0.1.0  ·  hermes-0.10.0
    Welcome to Hermes Agent!   →  🐟 鲶鱼来了。直接说事。
    Goodbye! ⚕                 →  再见 🐟
    Nous Research              →  鲶鱼平台
    ⚕（医蛇符号）               →  🐟

如果改了但启动还看到原 Hermes 字样：
    1. 这次的候选文件路径可能不对，跑 string-map.yaml 里 grep 命令找出真路径，
       加到 rebrand.sh 的 CANDIDATES 数组
    2. UI 可能从配置文件 / 编译产物读，要找 dist / build 目录

卸载（还原所有备份）：
    bash $SCRIPT_DIR/unrebrand.sh

每次 hermes update 后需要重新跑这个脚本（update 会冲掉所有改动）。
EOF
