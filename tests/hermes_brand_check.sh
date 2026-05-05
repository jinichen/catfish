#!/usr/bin/env bash
#
# hermes_brand_check.sh — 升级 hermes 后自动验证 brand patch 没断 (B.2 五一 sprint 5/5)
#
# 跑法:
#     bash tests/hermes_brand_check.sh             # 默认看 ~/.hermes/hermes-agent
#     HERMES_DIR=/tmp/hermes-012 bash tests/...    # 跑别的 hermes 安装位置
#
# 退出码:
#     0 = 全过, brand patch 完好
#     1 = 有泄漏, 详见输出
#
# 跟 apply_brand_patch.py 的关系:
#     - apply_brand_patch.py 跑前/跑后调它都对:
#       - 跑前 (没 patch): 全部黑名单字串都在 → 输出会列一堆, 期望非 0 退出
#       - 跑后 (patch 完): 全部黑名单字串都没了 → 0 退出
#     - 升级 hermes 后, 重跑 apply_brand_patch.py, 然后这个脚本 0 退出 = 升级成功
#
# 跟 wrapper subprocess 的关系 (B.3):
#     - 这脚本只检查源码层 (apply_brand_patch.py 应有的范围)
#     - 实际员工看到的是 wrapper subprocess + source patch 双重保险
#     - 即使源码 patch 漏一个, wrapper subprocess 兜住, 员工不会看到. 但 CI 仍要红
#       (源码 patch 是第一道防线, 不能依赖 wrapper 当唯一保障)

set -uo pipefail  # 不要 -e: 某些 grep 没命中算成功 (反向逻辑), 需要手工管 exit code

HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"

# 颜色 (非 tty 关掉)
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BOLD=''; RESET=''
fi

PASS=0
FAIL=0
WARN=0

print_pass() { printf "  ${GREEN}PASS${RESET}  %s\n" "$1"; PASS=$((PASS+1)); }
print_fail() { printf "  ${RED}FAIL${RESET}  %s\n" "$1"; FAIL=$((FAIL+1)); }
print_warn() { printf "  ${YELLOW}WARN${RESET}  %s\n" "$1"; WARN=$((WARN+1)); }
print_skip() { printf "  ${YELLOW}SKIP${RESET}  %s\n" "$1"; }

echo "${BOLD}=== Catfish brand check ===${RESET}"
echo "目标: $HERMES_DIR"
echo

if [ ! -d "$HERMES_DIR" ]; then
    echo "${RED}错误: HERMES_DIR 不存在: $HERMES_DIR${RESET}"
    echo "提示: HERMES_DIR=<path> bash tests/hermes_brand_check.sh"
    exit 2
fi

# ============================================================
# Layer 1: 源文件 grep (apply_brand_patch.py 已应用范围)
# ============================================================

echo "${BOLD}[Layer 1] 源文件残留字串扫描${RESET}"

# (file, pattern_to_search, description, severity)
# severity: fail = 必须没; warn = 最好没但不致命
check_absent() {
    local file="$1" pattern="$2" desc="$3" severity="${4:-fail}"
    local target="$HERMES_DIR/$file"

    if [ ! -f "$target" ]; then
        # 文件不存在时, 看是不是 0.11+ 才有的 ui-tui (期望 0.10 上不在 = 正常)
        case "$file" in
            ui-tui/*)
                print_skip "$desc (ui-tui 0.10 没该目录)"
                return 0
                ;;
            *)
                print_warn "$desc (文件不存在: $file — hermes 升级删了?)"
                return 0
                ;;
        esac
    fi

    if grep -q "$pattern" "$target" 2>/dev/null; then
        if [ "$severity" = "warn" ]; then
            print_warn "$desc — '$pattern' 仍在 $file"
        else
            print_fail "$desc — '$pattern' 仍在 $file"
        fi
    else
        print_pass "$desc"
    fi
}

# 反向检查: 期望某些 catfish 字符串**已存在** (apply 完后应该有)
check_present() {
    local file="$1" pattern="$2" desc="$3"
    local target="$HERMES_DIR/$file"
    if [ ! -f "$target" ]; then
        print_skip "$desc (文件不存在: $file)"
        return 0
    fi
    if grep -q "$pattern" "$target" 2>/dev/null; then
        print_pass "$desc"
    else
        print_fail "$desc — 期望含 '$pattern' 但没找到"
    fi
}

# ─── theme.ts ───
check_absent "ui-tui/src/theme.ts" "name: 'Hermes Agent'" "theme.ts 不含 'Hermes Agent'"
check_absent "ui-tui/src/theme.ts" "icon: '⚕'" "theme.ts icon 已 ⚕ → 🐟"
check_absent "ui-tui/src/theme.ts" "Goodbye! ⚕" "theme.ts goodbye 已脱敏"

# ─── branding.tsx ───
check_absent "ui-tui/src/components/branding.tsx" "Messenger of the Digital Gods" \
    "branding.tsx tagline 已脱敏"
check_absent "ui-tui/src/components/branding.tsx" "NOUS HERMES" \
    "branding.tsx ASCII 大字已替换"
check_absent "ui-tui/src/components/branding.tsx" " · Nous Research" \
    "branding.tsx model row 已脱敏"

# ─── appLayout.tsx ───
check_absent "ui-tui/src/components/appLayout.tsx" "⚕ {ui.status}" \
    "appLayout.tsx 状态栏 icon 已 ⚕ → 🐟"

# ─── cli.py (含 0.12 新加) ───
check_absent "cli.py" "⚕ NOUS HERMES" "cli.py 不含 NOUS HERMES (含 0.12 新加点)"
check_absent "cli.py" '"Goodbye! ⚕"' "cli.py 不含 Goodbye! ⚕"
check_absent "cli.py" "Welcome to Hermes Agent" "cli.py 不含 Welcome to Hermes Agent"
check_absent "cli.py" "- Nous Research" "cli.py title row 已脱敏"

# ─── hermes_cli/banner.py ───
check_absent "hermes_cli/banner.py" 'f"Hermes Agent v' "banner.py 启动 banner 标题已脱敏"
check_absent "hermes_cli/banner.py" "HERMES-AGENT" "banner.py HERMES-AGENT 大字已清空"
check_absent "hermes_cli/banner.py" "[dim {dim}]Nous Research" \
    "banner.py model row suffix 已脱敏"

# ─── hermes_cli/main.py (0.12 新加 cmd_version) ───
check_absent "hermes_cli/main.py" 'print(f"Hermes Agent v' \
    "main.py --version 输出已脱敏 (0.12 新加点)"

# ─── hermes_cli/skin_engine.py ───
check_absent "hermes_cli/skin_engine.py" "Welcome to Hermes Agent" \
    "skin_engine.py welcome 已脱敏 (5 处)"
check_absent "hermes_cli/skin_engine.py" '"goodbye": "Goodbye! ⚕"' \
    "skin_engine.py goodbye ⚕ 已脱敏 (4 处)"
check_absent "hermes_cli/skin_engine.py" '"Goodbye! \\u2695"' \
    "skin_engine.py goodbye \\u2695 已脱敏"
check_absent "hermes_cli/skin_engine.py" '"agent_name": "Hermes Agent"' \
    "skin_engine.py agent_name 已改鲶鱼"

# ─── hermes_cli/tips.py (反向: 应该含鲶鱼内容) ───
check_present "hermes_cli/tips.py" "鲶鱼是你的副手" \
    "tips.py 已替换为鲶鱼 10 条 TIPS"

# ─── rl_cli.py (0.12 新加) ───
check_absent "rl_cli.py" '"\\\\n👋 Goodbye!"' \
    "rl_cli.py 退出语已脱敏 (0.12 新加点)" "warn"

echo

# ============================================================
# Layer 2: 实测 hermes 命令输出 (如果 HERMES_BIN 在 PATH)
# ============================================================

echo "${BOLD}[Layer 2] hermes 命令输出实测${RESET}"

HERMES_BIN="${HERMES_BIN:-hermes}"

if ! command -v "$HERMES_BIN" >/dev/null 2>&1; then
    print_skip "Layer 2 跳过: hermes 命令不在 PATH (HERMES_BIN=$HERMES_BIN)"
else
    # --version
    out_version="$("$HERMES_BIN" --version 2>&1 | head -3)"
    if echo "$out_version" | grep -qE "Hermes Agent v|Nous Research"; then
        print_fail "hermes --version 输出含 'Hermes Agent v' / 'Nous Research'"
        echo "    $out_version" | head -3
    else
        print_pass "hermes --version 输出脱敏"
    fi

    # --help (头 100 行)
    out_help="$("$HERMES_BIN" --help 2>&1 | head -100)"
    if echo "$out_help" | grep -qE "Hermes Agent|Nous Research"; then
        # --help 文案里有些字串原 catfish patch 不动 (cli arg description 等)
        # 暂列 warn 不 fail. 后续如果发现员工看不到这些, 升级到 fail
        print_warn "hermes --help 头 100 行含 'Hermes Agent' / 'Nous Research' (cli arg desc 文案, 员工一般看不到)"
    else
        print_pass "hermes --help 头 100 行脱敏"
    fi
fi

echo

# ============================================================
# 汇总
# ============================================================

echo "${BOLD}=== 汇总 ===${RESET}"
echo "  ${GREEN}PASS${RESET}: $PASS"
echo "  ${RED}FAIL${RESET}: $FAIL"
echo "  ${YELLOW}WARN${RESET}: $WARN"
echo

if [ "$FAIL" -gt 0 ]; then
    echo "${RED}${BOLD}brand check 失败. apply_brand_patch.py 漏 patch 了 $FAIL 条字面量.${RESET}"
    echo
    echo "修法:"
    echo "  1. 重跑 patch:  python3 edge/hermes-fork/apply_brand_patch.py --apply"
    echo "  2. 如果 patch script 不命中, 加新规则到 RULES (参考 docs/HERMES-UPGRADE-PHASE-A-RESULT.md § B.1)"
    echo "  3. revert:      python3 edge/hermes-fork/apply_brand_patch.py --revert"
    exit 1
fi

if [ "$WARN" -gt 0 ]; then
    echo "${YELLOW}brand check 通过, $WARN 条 warn (非阻塞).${RESET}"
    exit 0
fi

echo "${GREEN}${BOLD}brand check 全过. 升级 hermes 安全.${RESET}"
exit 0
