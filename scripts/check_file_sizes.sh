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

# ⚠ 8/14: 测试代码单独归一档, 不计进"必拆" (CLAUDE.md §1 例外清单新加的一条)。
#
# 鸿波: 「测试文件没必要去拆了吧, 浪费时间」。同意, 而且理由不只是省时间:
# 红线的作用是**逼人重新想清楚职责边界**, 而测试天然是追加式的 —— 一个 bug
# 一条, 拆开只是把同一组断言散到两个文件。
#
# 但不能让它们**消失**: 一个 3000 行的测试文件可能在说明别的事 (那个模块的
# 接口太大)。所以是单独一栏, 不是不看。这跟上面 schema / gitignore 两处的
# 处理一致 —— 扣掉多少条都要打印出来。
#
# 判据只看**文件名**, 不看目录:
#   test_*.py / *_test.py / *.test.ts(x) / *.spec.ts(x)
#
# 有意不豁免整个 `tests/` 目录 —— 那里可能躺着 helpers.py 之类的真代码,
# 按目录豁免就成了藏污纳垢的地方 (跟上面 schema 那条不用光看文件名同理)。
TEST_FILES=$(mktemp)
: > "$TEST_FILES"
KEPT_T=$(mktemp)
while IFS= read -r f; do
  case "$(basename "$f")" in
    test_*.py|*_test.py|*.test.ts|*.test.tsx|*.spec.ts|*.spec.tsx)
      printf '%s\n' "$f" >> "$TEST_FILES"; continue ;;
  esac
  printf '%s\n' "$f" >> "$KEPT_T"
done < "$TMPFILE"
mv "$KEPT_T" "$TMPFILE"

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

# ⚠ 8/15: 再扣掉 **vendored 进来的第三方代码**。
#
# 起因: `skills/creative/huashu-design/scripts/html2pptx.js` (978 行) 一直挂在
# 必拆名单上。那是花叔 (alchaincyf) 的 MIT skill, 整个目录是**抄进来**的,
# 不是我们写的。就地拆它有两个问题:
#   · 下次同步上游, 改动全被冲掉
#   · 改了之后跟上游 diff 不上, 想跟进对方的 bugfix 就得手动挑
#
# 这跟 test_outputs_dir_convention.py 里对 guizang submodule 的处理是同一条
# 理由 —— 那边原话是「git submodule 里的是第三方代码, 改不了, 就地打补丁推
# 不上去, 下次 submodule 更新还会被冲掉」。区别只是 huashu 是 vendored 不是
# submodule, 所以那条按 .gitmodules 判的规则漏了它。
#
# 判据: **某个祖先目录里有 LICENSE 文件, 且那个目录不是仓根**。
#
# 为什么这个判据够窄:
#   · 得真的有一个 LICENSE 文件躺在那 —— 光把目录名改成 "vendor/" 骗不到豁免
#     (跟上面 schema 那条"不能光看文件名"是同一个考虑)
#   · 仓根那份是我们自己的 Apache 2.0, 显式排掉
#   · 实测全仓只有两处嵌套 LICENSE: huashu-design (MIT, alchaincyf) 和
#     guizang-ppt-magazine (submodule)。都是第三方, 没有误伤
#
# 一样要打印出来, 不做静默过滤。
VENDOR_EXEMPT=0
VENDOR_FILES=$(mktemp)
: > "$VENDOR_FILES"
KEPT_V=$(mktemp)
while IFS= read -r f; do
  d="$(dirname "$f")"
  is_vendor=0
  while [ "$d" != "$REPO_ROOT" ] && [ "$d" != "/" ] && [ -n "$d" ]; do
    if [ -f "$d/LICENSE" ] || [ -f "$d/LICENSE.md" ] || [ -f "$d/LICENSE.txt" ]; then
      is_vendor=1; break
    fi
    d="$(dirname "$d")"
  done
  if [ "$is_vendor" -eq 1 ]; then
    VENDOR_EXEMPT=$((VENDOR_EXEMPT + 1))
    printf '%s\n' "$f" >> "$VENDOR_FILES"
    continue
  fi
  printf '%s\n' "$f" >> "$KEPT_V"
done < "$TMPFILE"
mv "$KEPT_V" "$TMPFILE"

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

  # Rust 的测试**长在源文件里面** (`#[cfg(test)] mod tests { ... }`), 不像 Python /
  # TS 那样单独成文件。CLAUDE.md §1 把测试代码列为例外之后, 只按文件名豁免就成了
  # 两套判据: .py/.ts 的测试不算, .rs 的测试算 —— 同一条规矩在不同语言下不一样,
  # 那是最容易积怨的一种不一致。
  #
  # 所以对 .rs 扣掉 #[cfg(test)] 那些 mod 的行数。
  #
  # 只对 ≥ 警戒线的文件跑 (省掉一百多次 python 启动): 扣行数只会让数字**变小**,
  # 本来就 < 500 的怎么扣都还是 OK, 不影响分档。
  if [ "${file##*.}" = "rs" ] && [ "$lines" -ge "$WARN_THRESHOLD" ] && \
     command -v python3 >/dev/null 2>&1; then
    eff=$(python3 - "$file" <<'PYRS'
import sys
# 数 #[cfg(test)] 模块占了多少行 —— 大括号配对, 不靠"文件末尾都是测试"这种假设
# (那个假设一旦不成立就会把真代码也扣掉, 而扣掉之后没人看得出来)。
src = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
test_lines, i, n = 0, 0, len(src)
while i < n:
    if src[i].strip().startswith("#[cfg(test)]"):
        start = i
        # 往下找到第一个 `{`, 然后配对到它的 `}`
        depth, seen = 0, False
        j = i
        while j < n:
            depth += src[j].count("{") - src[j].count("}")
            if "{" in src[j]:
                seen = True
            if seen and depth <= 0:
                break
            j += 1
        if seen:
            test_lines += (j - start + 1)
            i = j + 1
            continue
    i += 1
print(max(0, len(src) - test_lines))
PYRS
)
    if [ -n "$eff" ] && [ "$eff" -lt "$lines" ] 2>/dev/null; then
      relative="$relative (${lines} 行, 扣掉 $((lines - eff)) 行 #[cfg(test)])"
      lines="$eff"
    fi
  fi

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

# vendored 第三方也要看得见 —— 万一哪天有人往自己的目录里放了个 LICENSE,
# 半个仓会悄悄从报告里消失。超线的直接点名, 免得"第三方"变成藏污纳垢的口袋。
if [ -s "${VENDOR_FILES:-/dev/null}" ]; then
  echo " （另有 $VENDOR_EXEMPT 个 vendored 第三方文件按 CLAUDE.md §1 例外，不计入）"
  V_BIG=0; V_TOP=""
  while IFS= read -r vf; do
    vl=$(wc -l < "$vf" | tr -d ' ')
    if [ "$vl" -ge "$FAIL_THRESHOLD" ]; then
      V_BIG=$((V_BIG + 1))
      V_TOP="$V_TOP
      $(printf '%6d  %s' "$vl" "${vf#$REPO_ROOT/}")"
    fi
  done < "$VENDOR_FILES"
  if [ "$V_BIG" -gt 0 ]; then
    echo "   其中 $V_BIG 个超过 $FAIL_THRESHOLD 行 —— 不拆 (改了会被上游冲掉),"
    echo "   要动只能 fork 或提 PR 给上游:$V_TOP"
  fi
fi

# 测试文件不计进"必拆", 但**要看得见**。
#
# 一个 3000 行的测试文件本身不用拆, 却可能在说明别的事 —— 那个模块的接口太大,
# 或者一个函数背了太多分支。看不见就判断不了, 而"看不见"正是我们今天一直在
# 修的那种毛病。
if [ -s "${TEST_FILES:-/dev/null}" ]; then
  T_TOTAL=$(wc -l < "$TEST_FILES" | tr -d ' ')
  T_BIG=0; T_TOP=""
  while IFS= read -r tf; do
    tl=$(wc -l < "$tf" | tr -d ' ')
    if [ "$tl" -ge "$FAIL_THRESHOLD" ]; then
      T_BIG=$((T_BIG + 1))
      T_TOP="$T_TOP
      $(printf '%6d  %s' "$tl" "${tf#$REPO_ROOT/}")"
    fi
  done < "$TEST_FILES"
  echo " （另有 $T_TOTAL 个测试文件按 CLAUDE.md §1 例外，不计入必拆）"
  if [ "$T_BIG" -gt 0 ]; then
    echo "   其中 $T_BIG 个超过 $FAIL_THRESHOLD 行 —— 不用拆, 但值得看一眼是不是"
    echo "   被测的那个模块接口太大了:$T_TOP"
  fi
fi
echo "───────────────────────────────────────────────────────────"

if [ "$STRICT" -eq 1 ] && [ "$FAIL_COUNT" -gt 0 ]; then
  echo ""
  echo "❌ STRICT 模式下检测到必拆文件，退出码 1"
  exit 1
fi

exit 0
