#!/usr/bin/env bash
# 应用 catfish 品牌补丁到 ~/.hermes/hermes-agent/ 源码。
#
# 流程：
#   1. dry-run 看会改哪些
#   2. 员工确认后 --apply
#   3. 如果 hermes 升级过，老字符串找不到，提醒员工核对
#   4. TS 文件改完**可能需要 rebuild ui-tui**（看 hermes 运行方式）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_PY="$SCRIPT_DIR/apply_brand_patch.py"
HERMES_ROOT="$HOME/.hermes/hermes-agent"

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; BOLD='\033[1m'; RESET='\033[0m'

echo -e "${BOLD}=== Catfish brand patch installer ===${RESET}"
echo

if [ ! -d "$HERMES_ROOT" ]; then
    echo -e "${RED}错误${RESET}：找不到 $HERMES_ROOT"
    exit 1
fi

AUTO_YES=0
for a in "$@"; do
    [ "$a" = "-y" ] || [ "$a" = "--yes" ] && AUTO_YES=1
done

# 1. dry-run
echo -e "${BOLD}[1/3] Dry-run：看会改什么${RESET}"
python3 "$PATCH_PY"
echo

# 2. 确认
if [ "$AUTO_YES" = "0" ]; then
    read -r -p "    继续 apply 吗? [Y/n] " reply
    case "$reply" in
        ""|Y|y|Yes|yes) ;;
        *) echo "取消"; exit 0 ;;
    esac
fi

# 3. apply
echo
echo -e "${BOLD}[2/3] 应用补丁${RESET}"
python3 "$PATCH_PY" --apply
echo

# 4. 检查是否需要 rebuild ui-tui
echo -e "${BOLD}[3/3] 检查 TS 是否需要 rebuild${RESET}"
UI_TUI="$HERMES_ROOT/ui-tui"
if [ -d "$UI_TUI/dist" ]; then
    echo -e "    ${YELLOW}警告${RESET}：$UI_TUI/dist 存在，说明 hermes 跑的是编译产物"
    echo "    TS 源码改了，dist 没改 → TUI 显示不会变"
    echo "    两条路："
    echo "        a. 重新 build： cd $UI_TUI && npm install && npm run build"
    echo "        b. 忽略 TS 改动，只看 Python 端的变化（status 栏 'Nous Research'）"
elif [ -d "$UI_TUI/node_modules" ]; then
    echo -e "    ${GREEN}可能 OK${RESET}：有 node_modules 但没 dist，可能是 ts-node/tsx 直运行，改了源码直接生效"
    echo "    重启 hermes 看看"
else
    echo -e "    ${YELLOW}不确定${RESET}：无法判断 ui-tui 运行方式，重启 hermes 观察"
fi

echo
cat <<EOF
=== 装好了 ===

下一步：
    退出当前 hermes → 重新运行 catfish
    观察：
        启动 banner 的 "⚕ Nous Research · Messenger..."
            → 应变成 "🐟 鲶鱼平台 · 员工的数字副手"
        模型名行的 "catfish-private-main · Nous Research"
            → 应变成 "catfish-private-main · 鲶鱼平台"
        退出 Goodbye! ⚕
            → 应变成 再见 🐟
        ⚕ 状态栏图标
            → 应变成 🐟

如果 UI 没变化：
    很可能 ui-tui 是预编译 dist，需要 rebuild：
        cd $UI_TUI && npm install && npm run build

卸载（还原所有 .before-catfish 备份）：
    python3 $PATCH_PY --revert
    # 或者整个用 uninstall.sh：
    bash $SCRIPT_DIR/uninstall.sh
EOF
