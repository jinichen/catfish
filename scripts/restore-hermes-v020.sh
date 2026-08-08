#!/usr/bin/env bash
# 把 Companion 冲掉的 v2026.8.3 树挪回来 (8/8).
#
# ## 病
#
# 8/8 手动升级 hermes 到 v2026.8.3 (3c27eb6), audit pass=54 fail=0, SQLite
# 也修到 3.53.1。然后开了一次 Companion —— 整棵树被改名
# `.catfish-hermes-broken-82831-1786167197302782000`, 位置让给 Companion
# 自带的 v0.19.0 (v2026.7.20 / 3ef6bbd2)。SQLite 修好的那个 runtime
# generation 在树里面, 也跟着搬走了。
#
# 机制见 `edge/companion-app/src-tauri/src/commands/hermes_install.rs`:
#
#     match installed_hermes_commit_at(dir) {
#         Some(c) if c == hermes_pinned_commit() => {}
#         Some(c) => problems.push("版本不匹配: 已装 {c}, 需要 {...}"),
#
# `hermes_pinned_commit()` 是 `include_str!("../../../.hermes-git-commit")`
# —— **编译期烤进二进制**。所以只要跑着的那个 Companion 是用旧 pin 编译的,
# 挪回来多少次都会被再冲一次。
#
# ## 顺序要求 (**很重要**)
#
#   1. 仓库里的 pin 已改 (commit 8e8b3a8 做完了)
#   2. **重新构建 Companion** ← 没做完这步, 跑这个脚本等于白挪
#   3. 跑这个脚本
#
# 脚本会检查第 1 步, 但**检查不了第 2 步** —— 仓库里的 .hermes-git-commit
# 跟你正在跑的 .app 里烤的那个值没有任何关系。所以它只能警告。
#
# 如果你就是想先挪回来验证一下 (不重建), 那就**别开 Companion**。

set -euo pipefail

TS=$(date +%Y%m%d-%H%M%S)
HERMES_HOME=~/.hermes
HERMES_DIR=$HERMES_HOME/hermes-agent
CATFISH=~/person_task/catfish
PIN_FILE=$CATFISH/edge/companion-app/.hermes-git-commit
AUDIT_SCRIPT=$CATFISH/edge/catfish-cli/scripts/audit_hermes_compat.sh
AUDIT_LOG=/tmp/audit.restore-$TS.log

TARGET_SHA=$(head -1 "$PIN_FILE" | tr -d '[:space:]')

echo "════════ Phase 0: 找到那棵树 ════════"
echo "  目标 commit (来自 $PIN_FILE):"
echo "    $TARGET_SHA"

echo "→ [0.1] 扫 ~/.hermes 下所有被判 broken 的树, 找 commit 对得上的..."
CAND=""
for d in "$HERMES_HOME"/.catfish-hermes-broken-*; do
    [ -d "$d" ] || continue
    sha=$(sed -n '2p' "$d/.catfish-hermes-version" 2>/dev/null | tr -d '[:space:]')
    ver=$(grep -m1 '^version' "$d/pyproject.toml" 2>/dev/null | cut -d'"' -f2)
    mark="  "
    if [ "$sha" = "$TARGET_SHA" ]; then mark="→ "; CAND="$d"; fi
    echo "  ${mark}$(basename "$d")  ver=${ver:-?}  sha=${sha:0:12}"
done
[ -n "$CAND" ] || { echo "  ✗ 没找到 commit = $TARGET_SHA 的树。手工确认后再来。"; exit 1; }
echo "  ✓ 选中 $(basename "$CAND")"

echo "→ [0.2] 这棵树是完整的吗 (缺哪样 Companion 都会再判 broken)..."
MISS=0
for p in pyproject.toml venv/bin/hermes .install_method .catfish-hermes-version; do
    if [ -e "$CAND/$p" ]; then echo "  ✓ $p"; else echo "  ✗ 缺 $p"; MISS=1; fi
done

# venv/bin/python 要单独判 —— 它现在**必然是断的**, 而且这是正常的。
#
# SQLite 修复 (repair_vulnerable_runtime) 把私有 Python 装在
# <树>/.hermes-runtime/python/generation-*/ 下, 然后留了**两跳绝对软链**,
# 两跳都写死了 `~/.hermes/hermes-agent/`:
#
#   venv/bin/python
#     → …/hermes-agent/.hermes-runtime/python/generation-X/cpython-3.11-…/bin/python3.11
#   generation-X/cpython-3.11-…            (注意是 3.11, 没有 patch 号)
#     → …/hermes-agent/.hermes-runtime/python/generation-X/cpython-3.11.15-…
#
# 树一旦被改名成 .broken-*, 这两跳指的都是**现在占着 hermes-agent 位置的那棵**
# (Companion 铺的 v0.19), 那里没有 .hermes-runtime, 于是整条链悬空。
# 挪回 hermes-agent 的那一刻两跳同时复原。
#
# 所以这里不追软链 (追了也只会掉进那条断链), 直接看**实体解释器在不在这棵树里**。
# 真正的"通不通"留到 Phase 2.0 归位之后验。
echo "  · venv/bin/python (两跳绝对软链, 改名期间必然悬空 — 单独判):"
REAL_PY=$(ls "$CAND"/.hermes-runtime/python/generation-*/cpython-*/bin/python3.11 2>/dev/null \
          | while read -r f; do [ -f "$f" ] && [ -x "$f" ] && echo "$f"; done | head -1)
if [ -x "$CAND/venv/bin/python" ]; then
    echo "    ✓ 现在就是通的"
elif [ -n "$REAL_PY" ]; then
    echo "    ✓ 悬空但可预期 —— 实体解释器就在这棵树里, 归位后自然接上"
    echo "      ${REAL_PY#$CAND/}"
elif [ -L "$CAND/venv/bin/python" ]; then
    echo "    ✗ 软链在但这棵树里找不到实体解释器 —— runtime 真丢了"
    echo "      指向 $(readlink "$CAND/venv/bin/python")"
    MISS=1
else
    echo "    ✗ 连软链都没有"
    MISS=1
fi
[ $MISS -eq 0 ] || { echo "  ✗ 树不完整, 挪回去也会被再冲。停。"; exit 1; }

echo "→ [0.3] Companion 二进制的 pin (**脚本查不了, 只能问你**)..."
echo "  ╔════════════════════════════════════════════════════════════╗"
echo "  ║ 仓库里的 pin 已经是 ${TARGET_SHA:0:12} 了, 但你正在跑的     ║"
echo "  ║ Companion.app 里烤的是**编译时**那个值。                    ║"
echo "  ║                                                            ║"
echo "  ║ 没重新构建过 → 挪回来之后**别开 Companion**, 一开就再冲一次 ║"
echo "  ╚════════════════════════════════════════════════════════════╝"

echo "→ [0.4] 停 Companion 和 hermes..."
pkill -f 'Catfish Companion' 2>/dev/null || true
pkill -f 'hermes serve'      2>/dev/null || true
pkill -f 'hermes gateway'    2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/*hermes* 2>/dev/null || true
sleep 3
if pgrep -f 'Catfish Companion' >/dev/null; then
    echo "  ✗ Companion 还活着 —— 它随时可能再跑一次 bootstrap。手动退出再来。"
    exit 1
fi
echo "  ✓ 都停了"

echo
echo "════════ Phase 1: 换回来 ════════"
SIDELINE=$HERMES_HOME/hermes-agent.companion-reinstalled-$TS
echo "→ [1.1] 先把 Companion 铺的那棵挪开 (不删, 留证据)..."
if [ -d "$HERMES_DIR" ]; then
    cur=$(grep -m1 '^version' "$HERMES_DIR/pyproject.toml" 2>/dev/null | cut -d'"' -f2)
    echo "  现役 ver=${cur:-?} → $(basename "$SIDELINE")"
    mv "$HERMES_DIR" "$SIDELINE"
else
    echo "  (当前没有 hermes-agent, 直接挪)"
fi

echo "→ [1.2] v0.20 树归位..."
mv "$CAND" "$HERMES_DIR" || {
    echo "  ✗ 挪回失败, 把刚才那棵放回去"
    mv "$SIDELINE" "$HERMES_DIR"
    exit 1
}
echo "  ✓ $(basename "$CAND") → hermes-agent"

echo
echo "════════ Phase 2: 校验 ════════"
PY=$HERMES_DIR/venv/bin/python

echo "→ [2.0] venv/bin/python 归位后是不是接上了..."
[ -x "$PY" ] || {
    echo "  ✗ 还是断的: $PY"
    echo "    指向 $(readlink "$PY" 2>/dev/null)"
    echo "    这条是绝对软链, 只有树在 hermes-agent 这个名字下才成立。"
    exit 1
}
echo "  ✓ $("$PY" -c 'import sys; print(sys.version.split()[0])')"

echo "→ [2.1] 版本 / commit..."
grep -m1 '^version' "$HERMES_DIR/pyproject.toml"
cat "$HERMES_DIR/.catfish-hermes-version"

echo "→ [2.2] SQLite 是修过的那个 (3.53.1, 不是 3.50.4)..."
"$PY" - <<PYEOF
import sys, sqlite3
sys.path.insert(0, "$HERMES_DIR")
from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable
v = is_sqlite_wal_reset_vulnerable(sqlite3.sqlite_version_info)
print("  SQLite", sqlite3.sqlite_version, "漏洞=" + ("yes" if v else "no"))
sys.exit(1 if v else 0)
PYEOF

echo "→ [2.3] import + catfish_email..."
"$PY" -c 'import hermes_cli, hermes_state; print("  ✓ hermes_cli / hermes_state")'
"$PY" -c 'import catfish_email; print("  ✓ catfish_email")' \
  || echo "  ⚠ catfish_email 没了 —— 跑 repair-hermes-sqlite.sh 的 Phase 3 补装"

echo "→ [2.4] 兼容审计..."
bash "$AUDIT_SCRIPT" > "$AUDIT_LOG" 2>&1 || true
P=$(grep -oE 'pass=[0-9]+' "$AUDIT_LOG" | tail -1 | cut -d= -f2)
F=$(grep -oE 'fail=[0-9]+' "$AUDIT_LOG" | tail -1 | cut -d= -f2)
echo "  pass=$P fail=$F  ($AUDIT_LOG)"
[ "${F:-1}" = "0" ] || { echo "  ✗ 审计有 fail, 看日志"; exit 1; }

echo "→ [2.5] state.db..."
sqlite3 "$HERMES_HOME/state.db" "PRAGMA journal_mode; PRAGMA integrity_check;" | head -2

echo
echo "════════ 完成 ════════"
echo "  现役     : v0.20.0 / v2026.8.3 / ${TARGET_SHA:0:12}"
echo "  挪开的   : $(basename "$SIDELINE")  (Companion 铺的 v0.19, 确认没问题后可删)"
echo
echo "  ⚠ 残留风险: 这棵树的 node_modules 是升级时从 v0.19 复用过来的。"
echo "    根 package.json 的运行时依赖只是把 ^X 改成精确 X (版本没变),"
echo "    新增的全是 eslint 类 devDependency —— 所以运行期大概率没事。"
echo "    但 workspace 子包没逐个比对过。真遇到诡异的前端问题, 先怀疑这里,"
echo "    修法是在 $HERMES_DIR 下重跑一次 npm ci (需要 node >= 22.22.0)。"
echo
echo "  下一步:"
echo "    · 没重建 Companion → **别开它**, 先 bash edge/companion-app/scripts/check_version_sync.sh"
echo "      再重打 runtime bundle + 重新构建"
echo "    · 已重建 → nohup hermes serve > ~/.hermes/serve-$TS.log 2>&1 &"
echo "      然后跑 upgrade-hermes-v020.sh 阶段 4 的 8 步冒烟"
