#!/usr/bin/env bash
# BL-HERMES-UPGRADE-V020 (8/8): hermes v0.19.0 (v2026.7.20) → v0.20.0 (v2026.8.3 · The Herald).
#
# 沿用 scripts/upgrade-hermes-v019.sh 的骨架, 但**阶段 2 换了做法** —— 原因见下。
#
# ## 为什么不能照抄 v019 的 `git fetch && git checkout`
#
# v019 那版假设 ~/.hermes/hermes-agent 是个正常 checkout。8/8 实测**不是**:
#
#   · .git 在, 但 `main` 上一个 commit 都没有, 也没有任何 tag
#   · 85 个顶层条目**全是未跟踪状态** (?? ) —— 这棵树不是 git 拉下来的
#   · .catfish-bootstrap-complete.json 写着 installer=catfish-companion-bootstrap-v2
#   · .catfish-hermes-version = v2026.7.20 / 3ef6bbd2
#   · edge/companion-app/scripts/pre-tauri-build.sh:111 明写
#     「事实源是 .catfish-hermes-version, **不是 git**」
#
# 也就是说 hermes 源码是**随 Companion 的 runtime bundle 打包发下来的**,
# .git 只是个空壳。对着空壳跑 `git checkout <tag>`:
#   · 85 个未跟踪文件会挡住 checkout (untracked would be overwritten)
#   · 就算 -f 强推, **旧版删掉的文件会留在原地** —— 这次上游删了 405,000 行,
#     残留的 .py 会被 import 到, 是最难查的一类故障
#
# 所以阶段 2 改成**整棵树换掉**: 旁边全新 clone tag, 把运行时状态搬过去, 再原子换名。
# 删除的文件自然不存在, 回滚就是把目录名换回来。
#
# ## 哪些是"运行时状态"必须保留 (8/8 实测确认)
#
#   venv/          577M, Python 3.11.14 (新版要求 >=3.11,<3.14 ✓)
#   node_modules/  hermes 自带前端依赖
#   .catfish-*     bootstrap 标记 3 个
#
# 员工自己的 skills / plugins 在 ~/.hermes/skills 和 ~/.hermes/plugins,
# **跟 hermes-agent/skills 那 19 个自带的是分开的**, 换树不碰它们。
#
# ## 升级前必读的两条 (audit 覆盖不到)
#
#   ① 默认工具迭代上限 90 → 500 (gateway/run.py:1784 实测)。
#      8/8 advisor 那个 agent loop 转 7 轮就烧光了百炼一周配额。
#      本脚本阶段 1 把 HERMES_MAX_ITERATIONS 显式 pin 回 90。
#   ② P28 中文化: 审批文案构造函数多了 allow_session 参数。
#      默认路径逐字未变 (现有 _P28_REPLACEMENTS 仍命中), 但 allow_session=false
#      时会渲染出一个新变体, 中文 IM 会漏英文。见阶段 3.4 的检查。
#
# 用法: bash scripts/upgrade-hermes-v020.sh

set -uo pipefail

NEW_TAG="v2026.8.3"
TS=$(date +%Y%m%d-%H%M%S)
HERMES_DIR=~/.hermes/hermes-agent
BAK_DIR=~/.hermes/hermes-agent.pre-v020-$TS
NEW_DIR=~/.hermes/hermes-agent.new-$TS
CONFIG_BAK=~/.hermes/config.yaml.pre-v020-$TS.bak
DB_BAK=~/.hermes/state.db.pre-v020-$TS.bak
AUDIT_OLD=/tmp/audit.v019-$TS.log
AUDIT_NEW=/tmp/audit.v020-$TS.log
AUDIT_SCRIPT=~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh

fail() {
    echo "❌ $*"
    echo ""
    echo "🔙 回滚 (树还没换的话什么都不用做, 换了就跑这个):"
    echo "  [ -d $BAK_DIR ] && rm -rf $HERMES_DIR && mv $BAK_DIR $HERMES_DIR"
    exit 1
}
ok() { echo "✅ $*"; }

echo "════════════════════════════════════════════"
echo " hermes v0.19.0 → v0.20.0 ($NEW_TAG) · $TS"
echo "════════════════════════════════════════════"

# ==============================================================================
# 阶段 0 · Pre-flight
# ==============================================================================
echo ""
echo "═══ 阶段 0 · Pre-flight ═══"

[ -d "$HERMES_DIR" ] || fail "$HERMES_DIR 不存在"
[ -f "$AUDIT_SCRIPT" ] || fail "$AUDIT_SCRIPT 不存在"

echo "→ [0.1] 当前版本..."
cat "$HERMES_DIR/.catfish-hermes-version" 2>/dev/null || echo "  (无 .catfish-hermes-version)"
grep -m1 '^version' "$HERMES_DIR/pyproject.toml"

echo "→ [0.2] baseline audit (必须 fail=0 才准往下走)..."
HERMES_ROOT="$HERMES_DIR" bash "$AUDIT_SCRIPT" > "$AUDIT_OLD" 2>&1 || true
BASE_PASS=$(grep -oE 'pass=[0-9]+' "$AUDIT_OLD" | tail -1 | cut -d= -f2)
BASE_FAIL=$(grep -oE 'fail=[0-9]+' "$AUDIT_OLD" | tail -1 | cut -d= -f2)
echo "  baseline: pass=$BASE_PASS fail=$BASE_FAIL"
[ "${BASE_FAIL:-99}" = "0" ] || fail "升级前基线就有 fail · 先修再升 · log=$AUDIT_OLD"
ok "baseline clean"

echo "→ [0.3] 停 hermes (免 backup 时 db 半写)..."
launchctl unload ~/Library/LaunchAgents/*hermes* 2>/dev/null || true
pkill -f "hermes serve" 2>/dev/null || true
pkill -f "hermes gateway" 2>/dev/null || true
sleep 3

echo "→ [0.4] backup config.yaml + state.db..."
cp ~/.hermes/config.yaml "$CONFIG_BAK" 2>/dev/null && echo "  config.yaml → $CONFIG_BAK"
# state.db 实测 1.0 GB —— 用 sqlite3 .backup 而不是 cp, 保证一致性快照
if [ -f ~/.hermes/state.db ]; then
    echo "  state.db 备份中 (1GB 量级, 要等一会)..."
    sqlite3 ~/.hermes/state.db ".backup '$DB_BAK'" || fail "state.db 备份失败 · 不带备份不许升"
    echo "  state.db → $DB_BAK ($(du -h "$DB_BAK" | cut -f1))"
fi
ok "backup 完"

# ==============================================================================
# 阶段 1 · Config 加固
# ==============================================================================
echo ""
echo "═══ 阶段 1 · Config 加固 ═══"

CONFIG_YAML=~/.hermes/config.yaml
touch "$CONFIG_YAML"

add_config_key() {
    local section=$1 key=$2 value=$3 desc=$4
    if grep -qE "^\s*${key}:" "$CONFIG_YAML" 2>/dev/null; then
        echo "  · $key 已存在 · 不动 ($desc)"
    elif grep -qE "^${section}:" "$CONFIG_YAML" 2>/dev/null; then
        awk -v s="$section:" -v k="  $key: $value" '{print} $0==s{print k}' \
            "$CONFIG_YAML" > "$CONFIG_YAML.tmp" && mv "$CONFIG_YAML.tmp" "$CONFIG_YAML"
        echo "  + $section.$key = $value ($desc)"
    else
        printf "\n%s:\n  %s: %s\n" "$section" "$key" "$value" >> "$CONFIG_YAML"
        echo "  + $section.$key = $value ($desc)"
    fi
}

# v019 那三条继续保
add_config_key "approvals" "smart" "false" "关 LLM auto-approve · 保 P14/P15 中文批准闭环"
add_config_key "display"   "show_reasoning" "false" "关 TUI reasoning · Companion 已单独渲染"
add_config_key "session"   "auto_reset" "true" "显式保老行为"

# 8/8 新增: 迭代上限 pin 回 90
#
# v0.20 把默认从 90 抬到 500 (gateway/run.py:1784 `os.getenv("HERMES_MAX_ITERATIONS", "500")`)。
# 8/8 实盘: advisor Call 1 走 hermes agent loop, **7 轮就把百炼一周配额烧光**,
# 当时日志里是 budget=1/90 —— 90 这个上限是当天唯一没让它继续往下转的东西。
# 500 意味着同样的失控循环贵 5.5 倍。等 agent loop 的工具集收敛了再放开。
echo "→ [1.4] pin HERMES_MAX_ITERATIONS=90 (v0.20 默认 500)..."
HERMES_ENV=~/.hermes/.env
if grep -qE "^HERMES_MAX_ITERATIONS=" "$HERMES_ENV" 2>/dev/null; then
    echo "  · 已有 · 不动: $(grep -E '^HERMES_MAX_ITERATIONS=' "$HERMES_ENV")"
else
    printf '\n# 8/8 upgrade-hermes-v020: v0.20 默认 500, pin 回 90 —— 见脚本头注释\nHERMES_MAX_ITERATIONS=90\n' >> "$HERMES_ENV"
    echo "  + HERMES_MAX_ITERATIONS=90"
fi
ok "config 加固完"

# ==============================================================================
# 阶段 2 · 换树 (不是 git checkout · 原因见脚本头)
# ==============================================================================
echo ""
echo "═══ 阶段 2 · 换树 → $NEW_TAG ═══"

echo "→ [2.1] clone $NEW_TAG 到 $NEW_DIR (depth=1)..."
rm -rf "$NEW_DIR"
git clone --depth 1 --branch "$NEW_TAG" \
    https://github.com/NousResearch/hermes-agent.git "$NEW_DIR" \
    || fail "clone 失败 · 确认 tag 存在且网络通"

NEW_VER=$(grep -m1 '^version' "$NEW_DIR/pyproject.toml")
echo "  $NEW_VER"
echo "$NEW_VER" | grep -q '0\.20' || fail "clone 下来的 pyproject 版本不是 0.20 · 拿到 : $NEW_VER"
ok "clone 完"

echo "→ [2.2] 把运行时状态搬进新树..."
# venv 是 577M, 这里**刻意用 cp 不用 mv**: 拷完旧树仍然完整可用, 到 [2.4]
# 换名之前的任何一步失败, 都只需 rm -rf 新树, 旧树一个字节没动过。
# mv 能省 577M 的拷贝时间, 但会让"审计没过"这种正常分支也变成需要回滚的状态。
for item in venv node_modules .catfish-bootstrap-complete.json .catfish-hermes-version .catfish-stage-ready .install_method; do
    if [ -e "$HERMES_DIR/$item" ]; then
        cp -R "$HERMES_DIR/$item" "$NEW_DIR/$item" 2>/dev/null || fail "搬 $item 失败"
        echo "  · $item"
    fi
done
ok "运行时状态已在新树里"

echo "→ [2.3] 新树上跑 audit (换名之前, 挂了就不换)..."
HERMES_ROOT="$NEW_DIR" bash "$AUDIT_SCRIPT" > "$AUDIT_NEW" 2>&1 || true
NEW_PASS=$(grep -oE 'pass=[0-9]+' "$AUDIT_NEW" | tail -1 | cut -d= -f2)
NEW_FAIL=$(grep -oE 'fail=[0-9]+' "$AUDIT_NEW" | tail -1 | cut -d= -f2)
echo "  v0.19 baseline: pass=$BASE_PASS fail=$BASE_FAIL"
echo "  v0.20 new     : pass=$NEW_PASS fail=$NEW_FAIL"
if [ "${NEW_FAIL:-99}" != "0" ]; then
    echo ""
    grep -E "✗" "$AUDIT_NEW" | head -20
    echo ""
    diff "$AUDIT_OLD" "$AUDIT_NEW" | head -40
    rm -rf "$NEW_DIR"
    fail "新版 audit fail=$NEW_FAIL · **旧树没动过, 什么都不用回滚** · 需修 plugin.py 对应 _patch_pXX_*"
fi
ok "新树 audit clean: pass=$NEW_PASS"

echo "→ [2.4] 原子换名..."
mv "$HERMES_DIR" "$BAK_DIR" || fail "旧树改名失败"
mv "$NEW_DIR" "$HERMES_DIR" || {
    mv "$BAK_DIR" "$HERMES_DIR"
    fail "新树改名失败 · 已自动换回旧树"
}
ok "已切到 $NEW_TAG · 旧树在 $BAK_DIR"

echo "→ [2.5] 记版本 + pip install -e . (venv 复用, 只升 deps)..."
cd "$HERMES_DIR"
printf '%s\n%s\n' "$NEW_TAG" "$(git rev-parse HEAD 2>/dev/null || echo unknown)" > .catfish-hermes-version
"$HERMES_DIR/venv/bin/pip" install -e . 2>&1 | tail -15
[ ${PIPESTATUS[0]} -eq 0 ] || fail "pip install -e . 失败"
ok "deps 装完"

# ==============================================================================
# 阶段 3 · Post-flight
# ==============================================================================
echo ""
echo "═══ 阶段 3 · Post-flight ═══"

echo "→ [3.1] 版本 verify..."
grep -m1 '^version' pyproject.toml
"$HERMES_DIR/venv/bin/python" -c "import run_agent; print('  import run_agent OK')" \
    || fail "新树 import run_agent 失败 · deps 没装全?"

echo "→ [3.2] SOUL 注入路径..."
grep -qE '^def load_soul_md' agent/prompt_builder.py || fail "prompt_builder.py load_soul_md 消失 · SOUL 注入挂"
grep -qE 'load_soul_md' agent/system_prompt.py || fail "system_prompt.py load_soul_md 引用消失"
ok "SOUL 注入路径在"

echo "→ [3.3] plugin reload verify..."
RELOAD=~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/verify-plugin-reload.sh
[ -f "$RELOAD" ] && bash "$RELOAD" 2>&1 | tail -8 || echo "  ⚠ verify-plugin-reload.sh 不在 · 手工验"

# 3.4 P28 新变体检查 —— playbook Step 2, v0.18→v0.19 就是跳了这步栽的
echo "→ [3.4] P28 审批文案 diff..."
if grep -q "allow_session" gateway/run.py 2>/dev/null; then
    echo "  ⚠ v0.20 的审批文案构造函数带 allow_session 参数 (tools/approval.py 用"
    echo "     allow_session = not smart_denied_for_owner 传 false)。"
    echo "     allow_session=false 时渲染成:"
    echo "       Reply \`/approve\` to execute this one operation, or \`/deny\` to cancel."
    echo "     这条 _P28_REPLACEMENTS 里**没有** → 中文 IM 会漏英文。"
    echo "     阶段 4 第 5 步验中文化时若看到英文, 按 'P28 miss' 日志补这一条。"
fi

echo ""
echo "════════════════════════════════════════════"
echo " ✅ 阶段 0-3 完成 · hermes = $NEW_TAG"
echo "════════════════════════════════════════════"
echo ""
echo "📋 backup:"
echo "  hermes-agent: $BAK_DIR"
echo "  config.yaml:  $CONFIG_BAK"
echo "  state.db:     $DB_BAK"
echo "  audit log:    $AUDIT_OLD / $AUDIT_NEW"
echo ""
echo "🧪 阶段 4 · smoke test (手工 · 任一 fail 就回滚):"
echo "  1. nohup hermes serve > ~/.hermes/serve-$TS.log 2>&1 &"
echo "     tail -f ~/.hermes/serve-$TS.log · 看无 Traceback"
echo "     ⚠ state.db 1GB · 这版有 FTS v23 布局迁移, 首启可能要等"
echo "  2. grep 'delayed install ✓' ~/.hermes/logs/gateway.log · plugin 装上了"
echo "  3. Companion chat '你好' · 收到流式回复 (上游 LLM 真返 + picker 生效)"
echo "  4. Companion 问'你是谁' · 含鲶鱼人设关键词 (SOUL 注入)"
echo "  5. 微信发'帮我 ls ~/Documents' 触发 approval · **应全中文**"
echo "     若见英文 → grep 'P28 miss' ~/.hermes/logs/gateway.log 拿原文补 _P28_REPLACEMENTS"
echo "  6. Companion 弹窗点批准 · P15 SSE 桥解 block"
echo "  7. 早安页跑一次 · 看 agent loop 轮数 (应受 HERMES_MAX_ITERATIONS=90 约束)"
echo "  8. launchctl load ~/Library/LaunchAgents/*hermes*"
echo ""
echo "🔙 回滚:"
echo "  launchctl unload ~/Library/LaunchAgents/*hermes* 2>/dev/null"
echo "  pkill -f 'hermes serve'; pkill -f 'hermes gateway'"
echo "  rm -rf $HERMES_DIR && mv $BAK_DIR $HERMES_DIR"
echo "  cp $CONFIG_BAK ~/.hermes/config.yaml"
echo "  [ -f $DB_BAK ] && sqlite3 ~/.hermes/state.db \".restore '$DB_BAK'\""
echo "  launchctl load ~/Library/LaunchAgents/*hermes*"
