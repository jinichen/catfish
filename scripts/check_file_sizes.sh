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

# ⚠ 8/13: 实现 CLAUDE.md §1 例外清单里的 **schema / 数据文件** 那条。
#
# 原文: 「schema / 数据文件 (`*_schemas.py` 单个 OpenAI tools list 这种)」——
# 规矩早就写在军规里, 但脚本从来没实现, 于是 `catfish_tool_schemas.py`
# (3319 行) 一直被算进"必拆"。
#
# 后果不只是数字难看: 一个明文豁免的文件天天出现在必拆名单里, 会让人要么
# 去拆一个不该拆的数据文件, 要么学会无视整张名单 —— 后者更可能, 而那正是
# 7/30 那次 `.venv*` 漏 prune 造成的局面 (341 个"必拆"里绝大多数是第三方包)。
#
# 判据是**文件名 + 内容双条件**, 不是光看文件名:
#   · 文件名匹配 *_schema.py / *_schemas.py
#   · 且 AST 顶层**没有任何函数 / 类定义** (纯数据字面量)
#
# 只看文件名不行 —— 哪天有人往 schemas 文件里塞逻辑, 豁免就成了藏污纳垢的地方。
# 实测 catfish_tool_schemas.py: 3301/3319 行是一个 list 字面量, 0 个函数 0 个类。
SCHEMA_EXEMPT=0
if command -v python3 >/dev/null 2>&1; then
  KEPT_S=$(mktemp)
  while IFS= read -r f; do
    case "$(basename "$f")" in
      *_schema.py|*_schemas.py)
        if python3 - "$f" <<'PYCHK'
import ast, sys
try:
    tree = ast.parse(open(sys.argv[1], encoding="utf-8", errors="replace").read())
except SyntaxError:
    sys.exit(1)          # 解析不了就不豁免, 照常检查
sys.exit(0 if not any(
    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    for n in tree.body
) else 1)
PYCHK
        then
          SCHEMA_EXEMPT=$((SCHEMA_EXEMPT + 1)); continue
        fi
        ;;
    esac
    printf '%s\n' "$f" >> "$KEPT_S"
  done < "$TMPFILE"
  mv "$KEPT_S" "$TMPFILE"
fi

# ⚠ 8/13: 再扣掉 **git 明确忽略** 的文件。
#
# 起因跟上面 7/30 那段是同一个病的第二次发作。归档 25 个 daosheng 历史 PPT
# builder 到 `deck/archive/builders/`(该目录在 .gitignore 里) 之后, 报告数字
# **一个没少** —— 因为这个脚本走的是文件系统, 不是 git。24 个 git 根本不跟踪
# 的历史产物, 在报告里跟真正要拆的源文件混在一起, 占了"必拆"总数的三分之一。
#
# prune 清单治不了这个: 它得逐个把目录名硬编码进来, 而 .gitignore 里已经把
# "什么不算源码"说清楚了。让两处各说各的, 早晚再漂移一次。
#
# 边界:
#   · 只扣 **ignored**, 不扣 untracked —— 新写还没 git add 的文件是真源码,
#     必须照查。这两者的区别就是这条规则的全部风险控制。
#   · 不在 git 仓里 / 没有 git 命令 → 静默跳过这一步, 行为退回原样。
#   · 扣掉多少条会打印出来, 不做静默过滤 —— 万一有人 gitignore 了真源码目录,
#     数字会不对劲, 而不是无声消失。
IGNORED_COUNT=0
if command -v git >/dev/null 2>&1 && \
   git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  KEPT=$(mktemp)
  # check-ignore 对"有忽略项"返 0、"全都不忽略"返 1，两种都是正常结果
  git -C "$REPO_ROOT" check-ignore --stdin < "$TMPFILE" > "$TMPFILE.ign" 2>/dev/null || true
  if [ -s "$TMPFILE.ign" ]; then
    grep -Fxv -f "$TMPFILE.ign" "$TMPFILE" > "$KEPT" || true
    IGNORED_COUNT=$(wc -l < "$TMPFILE.ign" | tr -d ' ')
    mv "$KEPT" "$TMPFILE"
  else
    rm -f "$KEPT"
  fi
  rm -f "$TMPFILE.ign"
fi

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
# 扣掉的数量要看得见 —— 万一有人 gitignore 了真源码目录, 这里数字会不对劲,
# 而不是无声消失。
[ "$IGNORED_COUNT" -gt 0 ] && \
  echo " （另有 $IGNORED_COUNT 个文件被 .gitignore 排除，不计入）"
[ "${SCHEMA_EXEMPT:-0}" -gt 0 ] && \
  echo " （另有 $SCHEMA_EXEMPT 个纯数据 schema 文件按 CLAUDE.md §1 例外，不计入）"
echo "───────────────────────────────────────────────────────────"

if [ "$STRICT" -eq 1 ] && [ "$FAIL_COUNT" -gt 0 ]; then
  echo ""
  echo "❌ STRICT 模式下检测到必拆文件，退出码 1"
  exit 1
fi

exit 0
