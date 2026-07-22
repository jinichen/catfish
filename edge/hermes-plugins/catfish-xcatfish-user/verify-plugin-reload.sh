#!/usr/bin/env bash
# BL-VERIFY-PLUGIN-RELOAD (7/19): 深追 · 为啥 hermes 用老 plugin.py.
# 免粘贴 zsh 引号/括号/# 挂.

set -uo pipefail
export PATH=~/.hermes/hermes-agent/venv/bin:$PATH

echo "════════════════════════════════════════════"
echo " hermes plugin reload · 深追 · 7/19"
echo "════════════════════════════════════════════"

# ─── 1. plugin.py 在盘 + fix marker ─────────────
echo ""
echo "→ [1/6] plugin.py 在盘 + 新 fix marker"
PLUGIN_FILE=~/.hermes/plugins/catfish-xcatfish-user/plugin.py
ls -la "$PLUGIN_FILE"
echo ""
echo "  BL-P14-SLASH-ALIAS 数 (期望 >= 2):"
grep -c "BL-P14-SLASH-ALIAS" "$PLUGIN_FILE"
echo "  死码 '_P28_CMD_ALIASES = {' 数 (期望 0):"
grep -c "_P28_CMD_ALIASES = {" "$PLUGIN_FILE"
echo "  '本次会话' alias 数 (期望 >= 5):"
grep -c "本次会话" "$PLUGIN_FILE"

# ─── 2. 清 pyc + __pycache__ ─────────────
echo ""
echo "→ [2/6] 清 pyc + __pycache__"
find ~/.hermes/plugins/catfish-xcatfish-user -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find ~/.hermes/plugins/catfish-xcatfish-user -name "*.pyc" -delete 2>/dev/null
echo "  清完"

# ─── 3. 冷启 hermes ─────────────
echo ""
echo "→ [3/6] 冷启 hermes (真 kill + bootout + install --force + start)"
hermes gateway stop 2>&1 | tail -2
sleep 3
pkill -9 -f "hermes.*gateway" 2>/dev/null || true
pkill -9 -f "hermes-agent" 2>/dev/null || true
sleep 2
launchctl bootout gui/$(id -u)/ai.hermes.gateway 2>/dev/null || true
hermes gateway install --force 2>&1 | tail -2
hermes gateway start 2>&1 | tail -2
sleep 6

# ─── 4. verify hermes 新 pid + etime ─────────────
echo ""
echo "→ [4/6] hermes 8642 pid + etime (期望 etime < 30秒)"
NEW_PID=$(lsof -ti:8642 -sTCP:LISTEN 2>/dev/null)
if [[ -n "$NEW_PID" ]]; then
    ps -p $NEW_PID -o pid,etime,command | head -2
else
    echo "  ❌ hermes 8642 没起"
    hermes gateway status 2>&1 | tail -3
fi

# ─── 5. hermes log · 找 P14 patched ─────────────
echo ""
echo "→ [5/6] hermes log · 找 'P14 chinese approval alias patched'"
LOG_FILES=$(ls -t ~/.hermes/logs/*.log 2>/dev/null | head -3)
if [[ -z "$LOG_FILES" ]]; then
    echo "  ❌ 无 log 文件 · 试 gateway.log:"
    ls ~/.hermes/logs/ 2>/dev/null
else
    echo "  log 文件:"
    ls -lt ~/.hermes/logs/*.log 2>/dev/null | head -3
    echo ""
    echo "  P14 相关 log 尾 20 行:"
    for f in $LOG_FILES; do
        tail -300 "$f" 2>/dev/null | grep -E "P14|xcatfish|patched|approve alias|catfish.*plugin" | tail -10
    done
    if ! tail -300 $LOG_FILES 2>/dev/null | grep -q "P14 chinese approval alias patched"; then
        echo ""
        echo "  ❌ 未找到 'P14 chinese approval alias patched' — plugin 可能没加载或 patch 挂"
        echo ""
        echo "  → log 全尾 40 行 (找线索):"
        tail -40 $(echo "$LOG_FILES" | head -1) 2>/dev/null
    fi
fi

# ─── 6. verify · pyc 新编 ─────────────
echo ""
echo "→ [6/6] verify · pyc 新编 (Python 加载新 .py 编新 .pyc)"
PYC=$(ls ~/.hermes/plugins/catfish-xcatfish-user/__pycache__/plugin.*.pyc 2>/dev/null | head -1)
if [[ -n "$PYC" ]]; then
    ls -la "$PYC"
    stat -f "  mtime=%Sm" "$PYC" 2>/dev/null
else
    echo "  ❌ 没生成 pyc — 说明 hermes 没 import plugin.py"
fi

echo ""
echo "════════════════════════════════════════════"
echo "→ 手机 WeChat 发 /批准 本次会话 · 期望执行 · 不再弹审批"
echo "════════════════════════════════════════════"
