#!/bin/bash
# 批量清理代码里的 AI 生成痕迹（em-dash, Unicode 箭头, emoji, 重分隔线）
#
# 用法：
#   cd /path/to/your/project
#   bash ~/.local/bin/clean-ai-tells        # 当前目录
#   bash ~/.local/bin/clean-ai-tells /path  # 指定目录
#
# 注意：
#   1. 会直接改文件，请先 git commit 留存档
#   2. 不处理 NUMBERED_DOC / MANY_SEP（这些需要人工判断语境）
#   3. Mac sed 与 Linux sed 语法不同，本脚本两者兼容

set -e

ROOT="${1:-$(pwd)}"
ROOT="$(cd "$ROOT" && pwd)"

echo "==========================================================="
echo " AI 痕迹批量清理"
echo " 目录：$ROOT"
echo "==========================================================="

# 检测 sed 版本
if sed --version >/dev/null 2>&1; then
  SED_INPLACE=(-i)      # GNU sed
else
  SED_INPLACE=(-i '')   # BSD/Mac sed
fi

FILE_TYPES=(
  "*.py" "*.ts" "*.tsx" "*.js" "*.jsx" "*.vue"
  "*.css" "*.html" "*.md" "*.sh" "*.rs" "*.go"
)

# 构建 find 命令
FIND_ARGS=()
for t in "${FILE_TYPES[@]}"; do
  FIND_ARGS+=(-o -name "$t")
done
FIND_ARGS=("${FIND_ARGS[@]:1}")  # 去掉首个 -o

# 收集要处理的文件
TMPFILES=$(mktemp)
trap 'rm -f "$TMPFILES"' EXIT

find "$ROOT" \
  \( -path "*/venv" -o -path "*/.venv" -o -path "*/node_modules" \
     -o -path "*/dist" -o -path "*/build" -o -path "*/target" \
     -o -path "*/__pycache__" -o -path "*/.git" -o -path "*/.pytest_cache" \
     -o -path "*/.ruff_cache" \) -prune -o \
  -type f \( "${FIND_ARGS[@]}" \) -print > "$TMPFILES"

COUNT=$(wc -l < "$TMPFILES")
echo "扫描到 $COUNT 个文件待处理"
echo ""

# 逐类替换
echo "[1/3] em-dash, 箭头..."
while IFS= read -r f; do
  [ -z "$f" ] && continue
  sed "${SED_INPLACE[@]}" \
    -e $'s/\xe2\x80\x94/--/g' \
    -e $'s/\xe2\x86\x92/->/g' \
    -e $'s/\xe2\x86\x90/<-/g' \
    -e $'s/\xe2\x87\x92/=>/g' \
    -e $'s/\xe2\x87\x90/<=/g' \
    "$f"
done < "$TMPFILES"

echo "[2/3] emoji, 装饰符..."
while IFS= read -r f; do
  [ -z "$f" ] && continue
  sed "${SED_INPLACE[@]}" \
    -e $'s/\xe2\x9c\x93/[OK]/g' \
    -e $'s/\xe2\x9c\x85/[OK]/g' \
    -e $'s/\xe2\x9d\x8c/[FAIL]/g' \
    -e $'s/\xe2\x9a\xa0\xef\xb8\x8f/[WARN]/g' \
    -e $'s/\xe2\x9a\xa0/[WARN]/g' \
    -e $'s/\xf0\x9f\x9a\xa8/[ALERT]/g' \
    -e $'s/\xe2\x98\x85//g' \
    -e $'s/\xf0\x9f\x8e\x89//g' \
    -e $'s/\xf0\x9f\x9a\x80//g' \
    -e $'s/\xf0\x9f\x90\x9f//g' \
    -e $'s/\xf0\x9f\x93\x8c//g' \
    -e $'s/\xf0\x9f\x94\x92//g' \
    "$f"
done < "$TMPFILES"

echo "[3/3] 重分隔线 ━━━ ═══ ───..."
while IFS= read -r f; do
  [ -z "$f" ] && continue
  sed "${SED_INPLACE[@]}" \
    -e $'s/\xe2\x94\x81\xe2\x94\x81\xe2\x94\x81*/---/g' \
    -e $'s/\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90*/---/g' \
    -e $'s/\xe2\x94\x80\xe2\x94\x80\xe2\x94\x80\xe2\x94\x80*/---/g' \
    "$f"
done < "$TMPFILES"

echo ""
echo "==========================================================="
echo " 批量清理完成"
echo "==========================================================="
echo ""
echo "剩下这些要人工处理："
echo "  - NUMBERED_DOC  docstring 里的 1. 2. 3. 编号"
echo "  - MANY_SEP      单文件 # --- 分隔线过多（>10）"
echo "  - CLAUDE_PHRASE 特征性英文短语"
echo "  - DOCSTRING_HEADER 'Core responsibilities:' 等标题"
echo ""
echo "运行 'check-ai-tells' 看剩余详情。"
