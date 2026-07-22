#!/usr/bin/env bash
# BL-HERMES-UPGRADE-V019 (7/21 Task #3): hermes v0.18.0 → v0.19.0 (Quicksilver · v2026.7.20).
#
# 达华 POC 未交付 · 客户要求最新 hermes · 本机先升 · 军规 audit 完 P0 修 4 处 (C/D/I/J).
#
# 阶段:
#   0. Pre-flight backup (hermes-agent · config.yaml · state.db · audit baseline)
#   1. Config 加固 (approvals.smart=false · display.show_reasoning=false · session.auto_reset=true)
#   2. 升级 (git fetch v2026.7.20 · venv pip install -e .)
#   3. Post-flight audit (audit_hermes_compat.sh diff · SOUL 注入 grep · plugin reload)
#
# 阶段 4 (smoke test 8 步) 用户 UI 手工做.
#
# 任一阶段 fail · exit 1 · 用户按 backup 回滚:
#   rm -rf ~/.hermes/hermes-agent
#   mv ~/.hermes/hermes-agent.pre-v019-YYYYMMDD-HHMMSS ~/.hermes/hermes-agent
#   cp ~/.hermes/config.yaml.pre-v019.bak ~/.hermes/config.yaml
#   sqlite3 ~/.hermes/state.db ".restore ~/.hermes/state.db.pre-v019.bak"

set -uo pipefail

TS=$(date +%Y%m%d-%H%M%S)
HERMES_DIR=~/.hermes/hermes-agent
BAK_DIR=~/.hermes/hermes-agent.pre-v019-$TS
CONFIG_BAK=~/.hermes/config.yaml.pre-v019-$TS.bak
DB_BAK=~/.hermes/state.db.pre-v019-$TS.bak
AUDIT_V018=/tmp/audit.v018-$TS.log
AUDIT_V019=/tmp/audit.v019-$TS.log
AUDIT_SCRIPT=~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh

fail() { echo "❌ $*"; echo "🔙 回滚: rm -rf $HERMES_DIR && mv $BAK_DIR $HERMES_DIR"; exit 1; }
ok()   { echo "✅ $*"; }

echo "════════════════════════════════════════════"
echo " hermes v0.18 → v0.19 · Task #3 · $TS"
echo "════════════════════════════════════════════"

# ==============================================================================
# 阶段 0 · Pre-flight backup + baseline audit
# ==============================================================================
echo ""
echo "═══ 阶段 0 · Pre-flight backup ═══"

[ -d "$HERMES_DIR" ] || fail "$HERMES_DIR 不存在"
# 军规修 7/21: 用 -f 判存在 · 不判 x · 因用 `bash <script>` 显式跑 · 不需 x bit
[ -f "$AUDIT_SCRIPT" ] || fail "$AUDIT_SCRIPT 不存在"

# 0.1 停 hermes 服务 · 免 backup 时 db 半写
echo "→ [0.1] 停 hermes 服务..."
launchctl unload ~/Library/LaunchAgents/*hermes* 2>/dev/null || true
pkill -f "hermes serve" 2>/dev/null || true
pkill -f "hermes gateway" 2>/dev/null || true
sleep 3

# 0.2 backup hermes-agent 目录
# 军规修 7/21: 若已有同名 backup (上次跑到这里挂了) · 复用 · 不重建 (避免撞或时间戳错乱)
LAST_BAK=$(ls -td ~/.hermes/hermes-agent.pre-v019-* 2>/dev/null | head -1)
if [ -n "$LAST_BAK" ] && [ -d "$LAST_BAK" ]; then
    BAK_DIR="$LAST_BAK"
    echo "→ [0.2] backup 已存在 · 复用 $BAK_DIR"
    ok "backup (existing): $BAK_DIR"
else
    echo "→ [0.2] backup $HERMES_DIR → $BAK_DIR ..."
    cp -R "$HERMES_DIR" "$BAK_DIR" || fail "cp backup 失败"
    ok "backup: $BAK_DIR"
fi

# 0.3 backup config.yaml
if [ -f ~/.hermes/config.yaml ]; then
    cp ~/.hermes/config.yaml "$CONFIG_BAK" || fail "config.yaml backup 失败"
    ok "config.yaml.bak: $CONFIG_BAK"
else
    echo "  ⚠ ~/.hermes/config.yaml 不存在 · 跳过 (稍后阶段 1 会创建)"
fi

# 0.4 backup state.db (若存在)
if [ -f ~/.hermes/state.db ]; then
    sqlite3 ~/.hermes/state.db ".backup '$DB_BAK'" || fail "state.db backup 失败"
    ok "state.db.bak: $DB_BAK"
else
    echo "  ⚠ ~/.hermes/state.db 不存在 · 跳过"
fi

# 0.5 baseline audit · v0.18
echo "→ [0.5] 跑 audit baseline (v0.18)..."
HERMES_ROOT="$HERMES_DIR" bash "$AUDIT_SCRIPT" > "$AUDIT_V018" 2>&1 || true
BASELINE_PASS=$(grep -oE 'pass=[0-9]+' "$AUDIT_V018" | tail -1 | cut -d= -f2)
BASELINE_FAIL=$(grep -oE 'fail=[0-9]+' "$AUDIT_V018" | tail -1 | cut -d= -f2)
# 军规 7/21: 若 audit script 本身跑不起来 (grep 拿不到 pass/fail) · 提示不阻拦
if [ -z "$BASELINE_PASS" ] && [ -z "$BASELINE_FAIL" ]; then
    echo "  ⚠ audit script 输出无 pass=/fail= · 可能格式不同 · 看 $AUDIT_V018"
    echo "  ⚠ 继续升级 · 但阶段 3 diff 无 baseline 参考"
    BASELINE_PASS="?"
    BASELINE_FAIL="?"
elif [ "${BASELINE_FAIL:-99}" != "0" ]; then
    echo ""
    echo "❌ v0.18 baseline audit 未 clean (fail=$BASELINE_FAIL)"
    echo "🔍 fail 项目:"
    grep -E "✗|FAIL|fail" "$AUDIT_V018" | head -20
    echo ""
    fail "先修 v0.18 audit fail 项 · 再升 · 完整 log: $AUDIT_V018"
fi
ok "baseline v0.18: pass=$BASELINE_PASS fail=$BASELINE_FAIL · log=$AUDIT_V018"

# ==============================================================================
# 阶段 1 · Config 加固 (v0.19 默认翻转 · 保 老行为)
# ==============================================================================
echo ""
echo "═══ 阶段 1 · Config 加固 ═══"

CONFIG_YAML=~/.hermes/config.yaml
touch "$CONFIG_YAML"

# 智能追加 · 不覆盖已有 key
add_config_key() {
    local section=$1
    local key=$2
    local value=$3
    local desc=$4
    if grep -qE "^\s*${key}:" "$CONFIG_YAML" 2>/dev/null; then
        echo "  · $key 已存在 · 不动 ($desc)"
    elif grep -qE "^${section}:" "$CONFIG_YAML" 2>/dev/null; then
        # section 存在 · key 不在 · 在 section 下加
        # 用 awk 精准插入 · 避免 sed 不同发行版差异
        awk -v s="$section:" -v k="  $key: $value" \
            '{print} $0==s{print k}' "$CONFIG_YAML" > "$CONFIG_YAML.tmp" && \
            mv "$CONFIG_YAML.tmp" "$CONFIG_YAML"
        echo "  + $section.$key = $value ($desc)"
    else
        # section 不存在 · 追加整段
        printf "\n%s:\n  %s: %s\n" "$section" "$key" "$value" >> "$CONFIG_YAML"
        echo "  + $section.$key = $value ($desc)"
    fi
}

add_config_key "approvals" "smart" "false" "关 LLM auto-approve · 保 P14/P15 中文/批准闭环"
add_config_key "display"   "show_reasoning" "false" "关 TUI reasoning · Companion 已单独渲染"
add_config_key "session"   "auto_reset" "true" "v0.19 默认翻 false · 显式保老行为"

ok "config 加固完 · 见 $CONFIG_YAML"

# ==============================================================================
# 阶段 2 · 升级 hermes → v2026.7.20 (v0.19.0)
# ==============================================================================
echo ""
echo "═══ 阶段 2 · 升级 hermes v0.18 → v0.19 ═══"

cd "$HERMES_DIR"

echo "→ [2.1] git fetch --tags..."
git fetch --tags origin || fail "git fetch 失败"

echo "→ [2.2] git checkout v2026.7.20..."
# 保当前修改 (若有 · 应该没有 · fork/patch 都在 plugin 里)
if ! git diff --quiet 2>/dev/null; then
    echo "  ⚠ hermes-agent git tree 有本地修改 · stash 保护"
    git stash push -m "pre-v019-upgrade-$TS" || fail "git stash 失败"
fi
git checkout v2026.7.20 || fail "git checkout v2026.7.20 失败 · tag 可能不存在? 手工确认: git tag | grep v2026.7"
ok "已切到 v2026.7.20"

echo "→ [2.3] pip install -e . (venv 复用 · 只装/升 deps)..."
"$HERMES_DIR/venv/bin/pip" install -e . 2>&1 | tail -20
[ ${PIPESTATUS[0]} -eq 0 ] || fail "pip install -e . 失败"

echo "→ [2.4] 版本 verify..."
VERSION=$(grep '^version' pyproject.toml | head -1)
echo "  $VERSION"
echo "$VERSION" | grep -q "0.19" || fail "pyproject.toml 版本非 0.19"
ok "hermes 升到 v0.19.0"

# ==============================================================================
# 阶段 3 · Post-flight audit
# ==============================================================================
echo ""
echo "═══ 阶段 3 · Post-flight audit ═══"

# 3.1 audit_hermes_compat.sh diff
echo "→ [3.1] 跑 audit script (v0.19)..."
HERMES_ROOT="$HERMES_DIR" bash "$AUDIT_SCRIPT" > "$AUDIT_V019" 2>&1 || true
NEW_PASS=$(grep -oE 'pass=[0-9]+' "$AUDIT_V019" | tail -1 | cut -d= -f2)
NEW_FAIL=$(grep -oE 'fail=[0-9]+' "$AUDIT_V019" | tail -1 | cut -d= -f2)
echo "  v0.18 baseline: pass=$BASELINE_PASS fail=$BASELINE_FAIL"
echo "  v0.19 new     : pass=$NEW_PASS fail=$NEW_FAIL"
if [ "${NEW_FAIL:-99}" != "0" ]; then
    echo ""
    echo "❌ audit fail=$NEW_FAIL · 具体挂点:"
    grep -E "✗|FAIL|fail" "$AUDIT_V019" | head -30
    echo ""
    echo "🔍 diff:"
    diff "$AUDIT_V018" "$AUDIT_V019" | head -60
    fail "audit 有 fail · 需修 plugin.py 相应 _patch_pXX_* · 见上文 diff"
fi
ok "audit diff clean · v0.19 pass=$NEW_PASS"

# 3.2 SOUL 注入 grep verify
echo "→ [3.2] SOUL.md 注入路径 grep..."
grep -qE '^def load_soul_md' "$HERMES_DIR/agent/prompt_builder.py" || \
    fail "prompt_builder.py 里 load_soul_md 函数消失 · SOUL 注入挂"
grep -qE 'load_soul_md' "$HERMES_DIR/agent/system_prompt.py" || \
    fail "system_prompt.py 里 load_soul_md 引用消失 · SOUL 未注入"
ok "SOUL 注入路径 grep 命中"

# 3.3 plugin reload
echo "→ [3.3] plugin reload verify..."
if [ -x ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/verify-plugin-reload.sh ]; then
    bash ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/verify-plugin-reload.sh 2>&1 | tail -10
    ok "plugin reload verify"
else
    echo "  ⚠ verify-plugin-reload.sh 不存在 · 跳过 · 手工验"
fi

# ==============================================================================
# 完成 · 交回用户手工 smoke test
# ==============================================================================
echo ""
echo "════════════════════════════════════════════"
echo " ✅ 阶段 0-3 完成"
echo "════════════════════════════════════════════"
echo ""
echo "📋 backup 位置 (若 smoke test fail · 用此回滚):"
echo "  hermes-agent:  $BAK_DIR"
echo "  config.yaml:   $CONFIG_BAK"
echo "  state.db:      $DB_BAK"
echo ""
echo "📋 audit log:"
echo "  v0.18: $AUDIT_V018"
echo "  v0.19: $AUDIT_V019"
echo ""
echo "🧪 阶段 4 · smoke test (你手工 · 8 步 · 任一 fail 就回滚):"
echo "  1. nohup hermes serve > ~/.hermes/serve-$TS.log 2>&1 &"
echo "     tail -f ~/.hermes/serve-$TS.log · 看无 Traceback"
echo "  2. Companion chat '你好' · 收流式回复"
echo "  3. Reasoning display 符合预期"
echo "     (关了 · Companion 只见 assistant text)"
echo "  4. SOUL 注入生效 · Companion 问'你是谁' 含鲶鱼人设关键词"
echo "  5. WeChat '/批准 本次会话' 中文闭环通"
echo "  6. Companion 弹窗点批准 · P15 SSE 桥解 block"
echo "  7. TTFT < 1s · 首个 delta 到达时间"
echo "  8. launchctl load ~/Library/LaunchAgents/*hermes*"
echo ""
echo "🔙 回滚命令 (若需):"
echo "  launchctl unload ~/Library/LaunchAgents/*hermes* 2>/dev/null"
echo "  pkill -f 'hermes serve'"
echo "  rm -rf $HERMES_DIR"
echo "  mv $BAK_DIR $HERMES_DIR"
echo "  cp $CONFIG_BAK ~/.hermes/config.yaml"
echo "  [ -f $DB_BAK ] && sqlite3 ~/.hermes/state.db \".restore '$DB_BAK'\""
echo "  launchctl load ~/Library/LaunchAgents/*hermes*"
