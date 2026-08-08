#!/usr/bin/env bash
# 修 hermes venv 链接的 SQLite 3.50.4 WAL-reset 漏洞 —— **不动源码树**。
#
# ## 病
#
# 2026-08-08 升到 v2026.8.3 (v0.20.0) 之后, hermes 新加的检测报:
#
#     state.db (async_delegation): linked SQLite 3.50.4 is vulnerable to the
#     WAL-reset corruption bug — is already in WAL mode — leaving WAL in place.
#     Upgrade to SQLite 3.51.3+ (or backports 3.50.7 / 3.44.6);
#     Hermes-managed installs can repair the embedded runtime with `hermes update`.
#
# 受影响版本 (hermes_cli/sqlite_runtime.py:24 `is_sqlite_wal_reset_vulnerable`):
# `>=3.7.0` 且 **不**在 `[3.50.7, 3.51.0)` / `[3.44.6, 3.45.0)` 且 `< 3.51.3`。
# 我们是 3.50.4 —— 正中。
#
# ## 为什么 hermes 自带的缓解盖不住我们
#
# `hermes_state.py:647` 在 SQLite 有洞时走 `_apply_delete_for_wal_reset_bug`,
# 但那只是**拒绝给新库开 WAL**。同文件注释写得很死:
#
#     Never downgrades to DELETE if the on-disk DB header reports WAL.
#
# 我们的 1.1G `state.db` **早就是 WAL 了** (升级日志原话 "is already in WAL
# mode — leaving WAL in place")。所以缓解对我们是空的 —— 那 1.1G 一直跑在
# 有洞的 SQLite 上。
#
# ## 为什么不能听 doctor 的话跑 `hermes update`
#
# 我们的装法是 `git clone --depth 1 --branch v2026.8.3` —— **detached HEAD**,
# 停在 `3c27eb6 chore: release v0.20.0 (2026.8.3)`。
# 而 `update_cmd.py:_cmd_update_impl` (~3777 行):
#
#     current_branch = git rev-parse --abbrev-ref HEAD   # detached 时返回 "HEAD"
#     if current_branch != branch:                        # branch 缺省 "main"
#         print("⚠ Currently on detached HEAD — switching to main for update...")
#         git checkout main   (失败则 git checkout -B main origin/main)
#     ...
#     git merge --ff-only origin/main   (失败则 git reset --hard origin/main)
#
# 跑一次 `hermes update`, 源码树就从 v2026.8.3 被拽到 main 的 tip —— 把刚
# 钉好的版本、刚过的 54 项兼容审计全废掉。**所以这条路封死。**
#
# `hermes doctor --fix` 也修不了: doctor.py 里 `repair_vulnerable_runtime`
# 出现 **0 次**, doctor.py:855 的注释明说是故意的 ——
# "Warn-only ... runtime repair remains best-effort"。
#
# ## 这个脚本走的路
#
# `repair_vulnerable_runtime()` 本身**完全不碰 git** —— 它只做:
# 装一个 SQLite 已修的私有 Python generation → 在 `runtime/` 下 stage 一个
# 候选 venv → `uv sync --extra all --locked` → import 冒烟 → 原子换名切过去,
# 旧 venv 停在 `venv.stale.runtime-*`。切换前任何一步失败, 活着的 venv 一根
# 汗毛都不动 (managed_uv.py:1089 的 docstring 承诺)。
#
# 它平时只从 `ensure_uv()` / `update_managed_uv()` 里被调 —— 而那两个是
# `hermes update` 流程的一部分。我们绕开 `hermes update`, 直接调它。
#
# ## 代价: venv 里的 editable 装会丢
#
# `_stage_candidate_venv` 用的是 `uv sync --extra all --locked` —— 只装
# hermes 自己 `uv.lock` 里的东西。我们 venv 里有一个**不在 lock 里**的:
#
#     __editable__.catfish_email-0.1.0.pth → catfish/edge/email-agent/src
#
# 切过去之后它就没了。Phase 3 负责补回来。如果以后往 hermes venv 里塞了
# 别的 editable 包, **要加进 Phase 1 的清单和 Phase 3 的补装**。
#
# ## 8/8 实跑记录 (两个坑, 都已在脚本里堵上)
#
# 1. 第一次 provision 报 `error: unexpected argument '--no-bin' found` ——
#    机器上的 managed uv 是 0.4.30 (2024-11-04), 太老不认这个参数。
#    hermes 自己的 `_refresh_managed_uv_catalog()` 兜住了: 刷新 uv 后重试
#    一次就成了。**不用手动升 uv。**
# 2. Phase 3 挂在 `venv/bin/pip: No such file or directory` —— 新 venv 是
#    `uv venv` 造的, **默认不带 pip**; 旧 venv 带, 是因为它当年是
#    `python -m venv` 建的。改走 `uv pip install`。
#
# 结果: SQLite 3.50.4 → **3.53.1**, 旧 venv 停在
# `venv.stale.runtime-1786166593-79869-d1690e8e` (确认没问题后可删, 约 1G)。

set -euo pipefail

TS=$(date +%Y%m%d-%H%M%S)
HERMES_DIR=~/.hermes/hermes-agent
VENV=$HERMES_DIR/venv
UV_BIN=~/.hermes/bin/uv
PY=$VENV/bin/python
PINNED_SHA=3c27eb6
EXTRA_MANIFEST=/tmp/hermes-venv-extras-$TS.txt
AUDIT_LOG=/tmp/audit.post-sqlite-$TS.log
AUDIT_SCRIPT=~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh
EMAIL_AGENT=~/person_task/catfish/edge/email-agent

echo "════════ Phase 0: 前置检查 ════════"

echo "→ [0.1] 源码树必须还钉在 v2026.8.3..."
# 判据要**先读 .catfish-hermes-version, git 兜底** —— 跟 Rust 侧
# installed_hermes_commit_at() 和 pre-tauri-build.sh 保持一致。
#
# 8/8 踩到: 第一版只用 `git rev-parse HEAD`, 挂在
#     fatal: Needed a single revision
# 因为 Companion bootstrap 铺出来的树, `.git` 是个**空壳** —— HEAD 指向
# refs/heads/main, 而 refs/ 下一个 commit 都没有。那种树的版本只记在
# .catfish-hermes-version 里。
#
# 也就是说 hermes-agent 有两种来路, 判版本的方式不一样:
#   · 手动 git clone --branch <tag>  → detached HEAD, git 认得
#   · Companion bootstrap 解包铺的   → .git 空壳, 只有版本文件
# 只认后者会漏, 只认前者就是现在这个 bug。
current_hermes_sha() {
  local f="$HERMES_DIR/.catfish-hermes-version" line
  if [ -f "$f" ]; then
    # 不认行号, 挑出那行 40 位 hex (跟 Rust 的 parse_version_file 同判据)
    while IFS= read -r line; do
      line="$(printf '%s' "$line" | tr -d '[:space:]')"
      if [ ${#line} -eq 40 ] && [ -z "$(printf '%s' "$line" | tr -d '[:xdigit:]')" ]; then
        printf '%s' "$line"; return 0
      fi
    done < "$f"
  fi
  git -C "$HERMES_DIR" -c safe.directory='*' rev-parse HEAD 2>/dev/null || true
}
HEAD_SHA="$(current_hermes_sha)"
if [ "${HEAD_SHA:0:7}" != "$PINNED_SHA" ]; then
  echo "  ✗ 已装 ${HEAD_SHA:-<读不出>}, 期望 $PINNED_SHA*"
  echo "    ($HERMES_DIR/.catfish-hermes-version, git 兜底)"
  echo "    树已经被动过, 先查清楚再修 SQLite。"
  exit 1
fi
echo "  ✓ ${HEAD_SHA:0:12} (v2026.8.3)"

echo "→ [0.2] 确认 SQLite 真的有洞 (没洞就不折腾)..."
SQLITE_BEFORE=$("$PY" -c 'import sqlite3; print(sqlite3.sqlite_version)')
VULN=$("$PY" -c '
import sys, sqlite3
sys.path.insert(0, "'"$HERMES_DIR"'")
from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable
print("yes" if is_sqlite_wal_reset_vulnerable(sqlite3.sqlite_version_info) else "no")')
echo "  当前 SQLite: $SQLITE_BEFORE  漏洞: $VULN"
# 注意: 这里**不能** early exit。脚本要可重跑 —— Phase 2 成功但 Phase 3 挂了
# (8/8 就是这样: 新 venv 没有 pip) 的话, 重跑时 SQLite 已经是好的, 直接退出
# 就永远补不上 catfish_email。所以只跳过 Phase 2, 继续走补装和校验。
if [ "$VULN" = "yes" ]; then
  NEED_REPAIR=1
else
  NEED_REPAIR=0
  echo "  ✓ SQLite 已安全 —— 跳过 Phase 2, 仍走 Phase 3 补装 + Phase 4 校验。"
fi

echo "→ [0.3] uv 在不在..."
[ -x "$UV_BIN" ] || { echo "  ✗ $UV_BIN 不存在或不可执行"; exit 1; }
echo "  ✓ $("$UV_BIN" --version)"

echo "→ [0.4] 停 hermes (venv 要被整体换掉, 不能有进程占着)..."
pkill -f 'hermes serve' 2>/dev/null || true
sleep 3
if pgrep -f 'hermes serve' >/dev/null; then
  echo "  ✗ hermes 还活着, 手动停掉再跑。"
  exit 1
fi
echo "  ✓ hermes 已停"

echo "→ [0.5] 磁盘余量 (候选 venv + 私有 Python 大约要 2G)..."
df -h ~/.hermes | tail -1

echo
echo "════════ Phase 1: 记录 venv 里的额外装 ════════"
echo "→ [1.1] 列出所有不在 uv.lock 里的 editable 包..."
ls "$VENV"/lib/python3.11/site-packages/ | grep -E '^__editable__' | tee "$EXTRA_MANIFEST" || true
echo "  清单存于 $EXTRA_MANIFEST"
echo "  ⚠ 下面 Phase 3 只会补装 catfish_email。清单里若有别的, 手动补。"

echo
echo "════════ Phase 2: 换 runtime (不碰 git) ════════"
if [ "$NEED_REPAIR" = "0" ]; then
  echo "  (跳过 —— SQLite 已经是安全版本)"
  SQLITE_AFTER=$SQLITE_BEFORE
else
echo "→ [2.1] 调 repair_vulnerable_runtime..."
RC=0
"$PY" - <<PYEOF || RC=$?
import sys
from pathlib import Path
sys.path.insert(0, "$HERMES_DIR")
from hermes_cli.managed_uv import repair_vulnerable_runtime

r = repair_vulnerable_runtime("$UV_BIN", project_root=Path("$HERMES_DIR"))
print()
print(f"  status       = {r.status}")
print(f"  detail       = {r.detail or '(无)'}")
print(f"  sqlite_before= {r.sqlite_before}")
print(f"  sqlite_after = {r.sqlite_after}")
print(f"  backup_venv  = {r.backup_venv}")
# repaired / safe 都算成功; skipped 是"这次没做"; failed 要炸出来
if r.status == "failed":
    sys.exit(1)
if r.status == "skipped":
    print("  ⚠ 被跳过 —— 活着的 venv 未改动。看 detail 决定下一步。")
    sys.exit(2)
PYEOF
[ $RC -eq 0 ] || { echo "  ✗ 修复未完成 (rc=$RC)。旧 venv 未被动过。"; exit $RC; }

echo "→ [2.2] 复核换完之后的 SQLite..."
SQLITE_AFTER=$("$PY" -c 'import sqlite3; print(sqlite3.sqlite_version)')
echo "  $SQLITE_BEFORE → $SQLITE_AFTER"
fi

echo
echo "════════ Phase 3: 补装丢掉的 editable 包 ════════"
echo "→ [3.1] catfish_email (uv sync --locked 装不到它)..."
# 不能用 $VENV/bin/pip —— 新 venv 是 `uv venv` 造的, **默认不带 pip**
# (旧 venv 有, 是因为它当年是 python -m venv 建的)。走 uv 自己的 pip 前端。
# --no-config: 别让 hermes 的 [tool.uv] exclude-newer 卡住 setuptools 构建依赖;
# email-agent 本身零运行时依赖 (pyproject 里 dependencies 是空的), 不需要锁。
"$UV_BIN" pip install --no-config --python "$PY" -e "$EMAIL_AGENT"
"$PY" -c 'import catfish_email; print("  ✓ catfish_email", catfish_email.__file__)'

echo
echo "════════ Phase 4: 校验 ════════"

echo "→ [4.1] SQLite 不再有洞..."
"$PY" - <<PYEOF
import sys, sqlite3
sys.path.insert(0, "$HERMES_DIR")
from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable
v = is_sqlite_wal_reset_vulnerable(sqlite3.sqlite_version_info)
print("  SQLite", sqlite3.sqlite_version, "漏洞=" + ("yes" if v else "no"))
sys.exit(1 if v else 0)
PYEOF

echo "→ [4.2] 源码树还钉在 v2026.8.3 (这条是整件事的意义所在)..."
# 用跟 [0.1] 同一个 current_hermes_sha —— 8/8 第一次修只改了 0.1, 漏了这里,
# 于是 SQLite 已经修好了却死在最后一步校验上, 看着像整个失败了。
HEAD_AFTER="$(current_hermes_sha)"
[ "${HEAD_AFTER:0:7}" = "$PINNED_SHA" ] \
  && echo "  ✓ ${HEAD_AFTER:0:12} 未动" \
  || { echo "  ✗ 变成 ${HEAD_AFTER:-<读不出>} —— 源码树被动了, 严重"; exit 1; }

echo "→ [4.3] hermes 能 import..."
"$PY" -c 'import hermes_cli, hermes_state; print("  ✓ hermes_cli / hermes_state OK")'

echo "→ [4.4] 兼容审计 (要 fail=0, 且 pass 不低于升级后的 54)..."
bash "$AUDIT_SCRIPT" > "$AUDIT_LOG" 2>&1 || true
P=$(grep -oE 'pass=[0-9]+' "$AUDIT_LOG" | tail -1 | cut -d= -f2)
F=$(grep -oE 'fail=[0-9]+' "$AUDIT_LOG" | tail -1 | cut -d= -f2)
echo "  pass=$P fail=$F  (完整日志 $AUDIT_LOG)"
[ "${F:-1}" = "0" ] || { echo "  ✗ 审计有 fail, 停下来看 $AUDIT_LOG"; exit 1; }

echo "→ [4.5] state.db 还能开, journal_mode 仍是 wal..."
sqlite3 ~/.hermes/state.db "PRAGMA journal_mode; PRAGMA integrity_check;" | head -3

echo
echo "════════ 完成 ════════"
echo "  SQLite   : $SQLITE_BEFORE → $SQLITE_AFTER"
echo "  源码树   : $PINNED_SHA (未动)"
echo "  旧 venv  : 见 Phase 2 的 backup_venv, 确认没问题后可删 (约 1G)"
echo
echo "  下一步: nohup hermes serve > ~/.hermes/serve-$TS.log 2>&1 &"
echo "          然后跑 v0.20 升级脚本 Phase 4 的 8 步冒烟。"
