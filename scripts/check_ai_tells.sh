#!/bin/bash
# 检测代码里常见的 AI 生成痕迹
#
# 目的：为软件著作权申报做准备，代码需要呈现"人写"的自然特征。
# 本脚本扫描可疑 pattern，人工审核后修正。
#
# 用法：
#   ./check_ai_tells.sh                    # 扫当前目录（若含 src/）或脚本父目录
#   ./check_ai_tells.sh /path/to/project   # 扫指定目录
#   ./check_ai_tells.sh . --strict         # 当前目录 + 严格模式
#
# 说明：Unicode 模式用 printf 字节序列生成，避免被 sed 自伤。
# 本脚本可用于任意项目，不限于鲶鱼。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STRICT=0
REPO_ROOT=""
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --help|-h)
      grep '^#' "$0" | head -15 | sed 's/^# \?//'
      exit 0
      ;;
    -*) ;;
    *) REPO_ROOT="$arg" ;;
  esac
done

if [ -n "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
else
  # 默认扫当前目录（用户在哪执行就扫哪）
  REPO_ROOT="$(pwd)"
fi

# --- Unicode 模式（用 printf 字节序列保护，免得 sed 自伤）---
EM_DASH=$(printf '\xe2\x80\x94')      # 破折号
ARROW_R=$(printf '\xe2\x86\x92')      # 右箭头
ARROW_L=$(printf '\xe2\x86\x90')      # 左箭头
DOUBLE_AR_R=$(printf '\xe2\x87\x92')  # 双右箭头
DOUBLE_AR_L=$(printf '\xe2\x87\x90')  # 双左箭头
CHECK=$(printf '\xe2\x9c\x93')        # 勾
CHECK_BOX=$(printf '\xe2\x9c\x85')    # 勾（方框）
XMARK=$(printf '\xe2\x9d\x8c')        # 叉
WARN=$(printf '\xe2\x9a\xa0')         # 警告
STAR=$(printf '\xe2\x98\x85')         # 星
ROCKET=$(printf '\xf0\x9f\x9a\x80')   # 火箭
FISH=$(printf '\xf0\x9f\x90\x9f')     # 鱼
SIREN=$(printf '\xf0\x9f\x9a\xa8')    # 警报
PARTY=$(printf '\xf0\x9f\x8e\x89')    # 庆祝
LOCK=$(printf '\xf0\x9f\x94\x92')     # 锁
PUSHPIN=$(printf '\xf0\x9f\x93\x8c')  # 图钉

HEAVY_H=$(printf '\xe2\x94\x80')      # 重水平线
HEAVY_H2=$(printf '\xe2\x94\x81')     # 更重的水平线
DOUBLE_H=$(printf '\xe2\x95\x90')     # 双水平线

# 要扫描的文件
FILES=$(find "$REPO_ROOT" \
  \( -path "*/venv" -o -path "*/.venv" -o -path "*/node_modules" \
     -o -path "*/dist" -o -path "*/build" -o -path "*/target" \
     -o -path "*/__pycache__" -o -path "*/.git" -o -path "*/.pytest_cache" \
     -o -path "*/.ruff_cache" \) -prune -o \
  -type f \
  \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" \
     -o -name "*.js" -o -name "*.rs" -o -name "*.go" \) \
  -print)

TOTAL_HITS=0
declare -a HITS

check_pattern() {
  local label="$1"
  local pattern="$2"

  while IFS= read -r file; do
    [ -z "$file" ] && continue
    matches=$(grep -nE "$pattern" "$file" 2>/dev/null || true)
    if [ -n "$matches" ]; then
      relative="${file#$REPO_ROOT/}"
      while IFS= read -r match_line; do
        [ -z "$match_line" ] && continue
        HITS+=("$label|$relative|$match_line")
        TOTAL_HITS=$((TOTAL_HITS + 1))
      done <<< "$matches"
    fi
  done <<< "$FILES"
}

echo "==========================================================="
echo " 鲶鱼 · AI 生成痕迹检测"
echo " 目录：$REPO_ROOT"
echo "==========================================================="
echo ""
echo "扫描中..."

# 硬痕迹
check_pattern "EM_DASH" "$EM_DASH"
check_pattern "ARROW" "$ARROW_R|$ARROW_L|$DOUBLE_AR_R|$DOUBLE_AR_L"
check_pattern "DECO" "$CHECK|$CHECK_BOX|$XMARK|$WARN|$SIREN|$STAR|$PARTY|$FISH|$PUSHPIN|$LOCK|$ROCKET"
check_pattern "HEAVY_SEP" "${HEAVY_H2}{3,}|${HEAVY_H}{10,}|${DOUBLE_H}{3,}"

# 典型 Claude/GPT 英文短语
check_pattern "CLAUDE_PHRASE" \
  "we'd rather|rather than wait|factored out because|gracefully falls? back"

# 结构化 docstring 头
check_pattern "DOCSTRING_HEADER" \
  "^[[:space:]]*(Core responsibilities|Key points|Key concepts|Main features):"

# 编号列表（1. 做 X / 2. 做 Y）
check_pattern "NUMBERED_DOC" \
  "^[[:space:]]+[0-9]+\.[[:space:]]+[A-Z]"

# 多 --- 分隔符密度
count_block_separators() {
  local limit=10
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    count=$(grep -cE "^#[[:space:]]*-{10,}" "$file" 2>/dev/null || true)
    count=${count:-0}
    count=$(echo "$count" | head -1 | tr -dc '0-9')
    count=${count:-0}
    if [ "$count" -gt "$limit" ]; then
      relative="${file#$REPO_ROOT/}"
      HITS+=("MANY_SEP|$relative|$count 处 # --- 分隔线（>$limit 可疑）")
      TOTAL_HITS=$((TOTAL_HITS + 1))
    fi
  done <<< "$FILES"
}
count_block_separators

echo ""
echo "-----------------------------------------"
echo " 扫描结果"
echo "-----------------------------------------"
echo ""

if [ "$TOTAL_HITS" -eq 0 ]; then
  echo "未检出明显 AI 痕迹。"
else
  echo "共 $TOTAL_HITS 处可疑命中："
  echo ""
  # bash 3.2 兼容：不用 declare -A，改用临时文件计数
  TMPCAT=$(mktemp)
  trap 'rm -f "$TMPCAT"' EXIT
  for hit in "${HITS[@]}"; do
    echo "${hit%%|*}" >> "$TMPCAT"
  done
  sort "$TMPCAT" | uniq -c | sort -rn | \
    awk '{printf "  [%s] %d 处\n", $2, $1}'

  echo ""
  echo "-----------------------------------------"
  echo " 详情（前 30 条）"
  echo "-----------------------------------------"
  printf '%s\n' "${HITS[@]}" | head -30 | \
    awk -F'|' '{printf "  %-18s %s\n    %s\n", $1, $2, $3}'
  if [ ${#HITS[@]} -gt 30 ]; then
    echo ""
    echo "  （还有 $((${#HITS[@]} - 30)) 条省略）"
  fi
fi

echo ""
echo "-----------------------------------------------------------"
echo " 总计：$TOTAL_HITS 处"
echo "-----------------------------------------------------------"

if [ "$STRICT" -eq 1 ] && [ "$TOTAL_HITS" -gt 0 ]; then
  exit 1
fi
exit 0
