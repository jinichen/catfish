#!/bin/bash
# 检查代码文件尺寸，超出阈值时提示或失败
#
# 用法：
#   ./check_file_sizes.sh                    # 扫描当前目录（若含 src/）或脚本父目录
#   ./check_file_sizes.sh /path/to/project   # 扫描指定目录
#   ./check_file_sizes.sh . --strict         # 当前目录 + 严格模式
#
# 阈值：
#   300 行  : 目标（不提示）
#   500 行  : 警戒（黄灯）
#   800 行  : 必须拆分（红灯，strict 模式下 exit 1）
#
# 本脚本可用于任意项目，不限于鲶鱼。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WARN_THRESHOLD=500
FAIL_THRESHOLD=800
STRICT=0
REPO_ROOT=""

for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --help|-h)
      grep '^#' "$0" | head -25 | sed 's/^# \?//'
      exit 0
      ;;
    -*) ;;   # 忽略未识别的选项
    *) REPO_ROOT="$arg" ;;
  esac
done

# 目录选择：参数 > 当前工作目录
if [ -n "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
else
  REPO_ROOT="$(pwd)"
fi

# 扫描范围：catfish 项目内所有代码文件
# 使用 find + 正则匹配扩展名 + 路径排除
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

# ⚠ 7/30: 原来只 prune 了 `*/.venv`, 而仓里真实存在的是 `.venv-test` 和
# `.venv-sandbox` —— 于是 341 个"必拆"里绝大多数是 openpyxl / alembic /
# pydantic 这些第三方包。
#
# 后果不是"数字不好看"。CLAUDE.md 要求"改完代码必跑"这个脚本, 而一个 90%
# 是噪音的报告没人会读第二遍 —— 军规里最硬的那条自检就这么变成了摆设。
# 这跟这两天查出来的那些静默失败是同一类: 护栏存在, 但实际不起作用。
#
# `.venv*` 用通配覆盖所有变体; `.companion-state` / `.next` 是 CLAUDE.md
# 例外清单里点名的构建产物目录。
find "$REPO_ROOT" \
  \( -path "*/venv" -o -path "*/.venv*" -o -path "*/node_modules" \
     -o -path "*/dist" -o -path "*/build" -o -path "*/target" \
     -o -path "*/__pycache__" -o -path "*/.git" -o -path "*/.pytest_cache" \
     -o -path "*/.ruff_cache" -o -path "*/.companion-state" -o -path "*/.next" \
     -o -path "*/site-packages" \) -prune -o \
  -type f \
  \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" \
     -o -name "*.js" -o -name "*.jsx" -o -name "*.rs" \
     -o -name "*.go" -o -name "*.sh" \) \
  -print > "$TMPFILE"

TOTAL_FILES=0
WARN_COUNT=0
FAIL_COUNT=0

declare -a WARN_FILES
declare -a FAIL_FILES

while IFS= read -r file; do
  [ -z "$file" ] && continue
  TOTAL_FILES=$((TOTAL_FILES + 1))
  lines=$(wc -l < "$file" | tr -d ' ')
  relative="${file#$REPO_ROOT/}"

  if [ "$lines" -ge "$FAIL_THRESHOLD" ]; then
    FAIL_FILES+=("$lines|$relative")
    FAIL_COUNT=$((FAIL_COUNT + 1))
  elif [ "$lines" -ge "$WARN_THRESHOLD" ]; then
    WARN_FILES+=("$lines|$relative")
    WARN_COUNT=$((WARN_COUNT + 1))
  fi
done < "$TMPFILE"

# 打印报告
echo "═══════════════════════════════════════════════════════════"
echo " 鲶鱼 · 文件尺寸检查"
echo " 目录：$REPO_ROOT"
echo " 扫描：$TOTAL_FILES 个源文件"
echo " 阈值：⚠️  ≥${WARN_THRESHOLD}行   🚨 ≥${FAIL_THRESHOLD}行"
echo "═══════════════════════════════════════════════════════════"

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo ""
  echo "🚨 必须拆分（≥${FAIL_THRESHOLD} 行）："
  printf '%s\n' "${FAIL_FILES[@]}" | sort -t'|' -k1 -rn | \
    awk -F'|' '{printf "   %6d  %s\n", $1, $2}'
fi

if [ "$WARN_COUNT" -gt 0 ]; then
  echo ""
  echo "⚠️  警戒（≥${WARN_THRESHOLD} 行，考虑拆分）："
  printf '%s\n' "${WARN_FILES[@]}" | sort -t'|' -k1 -rn | \
    awk -F'|' '{printf "   %6d  %s\n", $1, $2}'
fi

if [ "$FAIL_COUNT" -eq 0 ] && [ "$WARN_COUNT" -eq 0 ]; then
  echo ""
  echo "✅ 所有文件都在健康范围内（<${WARN_THRESHOLD} 行）。"
fi

echo ""
echo "───────────────────────────────────────────────────────────"
echo " 汇总：$FAIL_COUNT 个必拆  +  $WARN_COUNT 个警戒  /  共 $TOTAL_FILES 文件"
echo "───────────────────────────────────────────────────────────"

if [ "$STRICT" -eq 1 ] && [ "$FAIL_COUNT" -gt 0 ]; then
  echo ""
  echo "❌ STRICT 模式下检测到必拆文件，退出码 1"
  exit 1
fi

exit 0
