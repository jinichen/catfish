#!/usr/bin/env bash
# 全仓扫「用了但没定义的名字」(pyflakes F821)。
#
# ══════════════════════════════════════════════════════════════════
# 为什么要有这个脚本
# ══════════════════════════════════════════════════════════════════
#
# 8/8 那个提交 (`4cdf122` 拆 plugin.py 第一轮 —— 微信两块出去) 一次造了两个洞,
# 都是同一个形状: **函数搬进新模块了, 它在老模块作用域里用的东西没搬**。
#
#   plugin_weixin_zh.py   用 @functools.wraps 但没 import functools
#                         → P28 每次启动都失败, 19 次, 从 8/9 到 8/13 四天。
#                            表现是微信那边一直看到未翻译的英文串。
#
#   plugin_wechat_qr.py   用 web.json_response / os.getpid, 而 _wechat_qr_sessions
#                         的定义还留在 plugin.py 里
#                         → P30 三个 handler 一跑就 NameError, **五天没人发现**,
#                            因为 patch 只注册路由(注册成功, 日志 19 次 ✓),
#                            handler 要等员工点「微信扫码」才第一次执行。
#
# 这类错的共同点:
#   · 不在 import 期暴露 —— 名字在函数体里, Python 到调用时才解析
#   · 调用可能很久之后才发生, 甚至从来不发生
#   · 于是"装载成功 ✓"的日志照打
#
# pyflakes 一秒就能看出来。所以钉在这里, 而不是靠人记得。
#
# ══════════════════════════════════════════════════════════════════
# 两类命中, 严重性完全不同 —— 不要混为一谈
# ══════════════════════════════════════════════════════════════════
#
# A. **可执行语句里的未定义名字** → 真 bug, 运行到那行就 NameError
#    P28 / P30 都是这类。必须修。
#
# B. **只出现在字符串注解里** → 运行时不查找, 不会炸
#    例: `def f() -> "SkillManifest":` 而 SkillManifest 在函数体内 import。
#    加上 `from __future__ import annotations` 之后所有注解都成字符串, 更不会炸。
#    只有人调 `typing.get_type_hints()` 时才会暴露。
#
#    8/13 全仓扫的 5 处生产代码命中**全是 B 类**, 一个真 bug 都没有 ——
#    也就是说 8/8 那次的伤害局限在那一个提交, 其它地方在这条轴上是干净的。
#
# 这个脚本默认只让 **A 类** 影响退出码。B 类照常打印(它们是真实的整洁度问题,
# 比如 test_screenshot.py 补了 Optional 的 import 却漏了 List/Dict), 但不拦提交。
#
# 用法:
#   bash scripts/check_undefined_names.sh            # 报告
#   bash scripts/check_undefined_names.sh --strict   # A 类存在则退出 1
set -uo pipefail

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! python3 -m pyflakes --version >/dev/null 2>&1; then
  echo "⚠ 没装 pyflakes —— pip install pyflakes。跳过检查。"
  exit 0
fi

TMP_FILES=$(mktemp); TMP_HITS=$(mktemp)
trap 'rm -f "$TMP_FILES" "$TMP_HITS"' EXIT

# 排除跟 check_file_sizes.sh 对齐 + archive (历史留档不该被卡)
find . \
  \( -path "*/venv" -o -path "*/.venv*" -o -path "*/node_modules" \
     -o -path "*/build" -o -path "*/dist" -o -path "*/target" \
     -o -path "*/site-packages" -o -path "*/__pycache__" -o -path "*/.git" \
     -o -path "*/archive" -o -path "*/.pytest_cache" \) -prune -o \
  -type f -name "*.py" -print > "$TMP_FILES"

xargs -a "$TMP_FILES" python3 -m pyflakes 2>/dev/null \
  | grep "undefined name" > "$TMP_HITS" || true

TOTAL=$(wc -l < "$TMP_HITS" | tr -d ' ')
if [ "$TOTAL" -eq 0 ]; then
  echo "✅ $(wc -l < "$TMP_FILES" | tr -d ' ') 个 Python 文件, 无 undefined name"
  exit 0
fi

# 用 AST 把每条命中分到 A / B 类。判据是**那个名字有没有出现在可执行语句里**,
# 而不是"文件里有没有这个名字" —— 后者会把函数内 import 之后的正常使用误判成
# 真 bug (8/13 我的第一版判据就是这么错的)。
python3 - "$TMP_HITS" <<'PY'
import ast, re, sys
from collections import defaultdict
from pathlib import Path

hits = defaultdict(set)
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    m = re.match(r"^(.*?):(\d+):\d+: undefined name '([^']+)'$", line)
    if m:
        hits[m.group(1)].add((int(m.group(2)), m.group(3)))

real, annot = [], []
for path, entries in sorted(hits.items()):
    try:
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        continue

    # 收集所有「注解位置」的行号 —— 这些行上的名字不会在运行时查找
    # (要么本来就是字符串字面量, 要么被 from __future__ import annotations 延迟)
    annot_lines = set()
    for n in ast.walk(tree):
        for ann in ():
            pass
        if isinstance(n, ast.AnnAssign) and n.annotation is not None:
            annot_lines.update(range(n.annotation.lineno, (n.annotation.end_lineno or n.annotation.lineno) + 1))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.returns is not None:
                annot_lines.update(range(n.returns.lineno, (n.returns.end_lineno or n.returns.lineno) + 1))
            for a in list(n.args.args) + list(n.args.kwonlyargs) + list(n.args.posonlyargs):
                if a.annotation is not None:
                    annot_lines.update(range(a.annotation.lineno, (a.annotation.end_lineno or a.annotation.lineno) + 1))

    for lineno, name in sorted(entries):
        (annot if lineno in annot_lines else real).append((path, lineno, name))

print()
if real:
    print(f"🔴 A 类 — 可执行语句里的未定义名字 ({len(real)} 处) —— 跑到就 NameError")
    for p, l, n in real:
        print(f"     {p}:{l}  {n}")
    print("     这就是 P28 / P30 那类。修法: 把漏搬的 import / 模块级状态补回去。")
else:
    print("🟢 A 类 (可执行语句里) — 无")

if annot:
    print()
    print(f"🟡 B 类 — 只在类型注解里 ({len(annot)} 处) —— 运行时不查找, 不会炸")
    for p, l, n in annot:
        print(f"     {p}:{l}  {n}")
    print("     只有 typing.get_type_hints() 才会碰到。可以补 import 或 TYPE_CHECKING, 不拦提交。")

Path("/tmp/_undef_real_count").write_text(str(len(real)))
PY

REAL=$(cat /tmp/_undef_real_count 2>/dev/null || echo 0)
rm -f /tmp/_undef_real_count
echo
if [ "$STRICT" = "1" ] && [ "${REAL:-0}" -gt 0 ]; then
  echo "❌ STRICT: A 类 $REAL 处, 退出 1"
  exit 1
fi
exit 0
