#!/usr/bin/env bash
# BL-LOCAL-GATEWAY-RESTART (7/19): 本地 gateway restart · 用 llm-gateway/venv/bin/python.
#
# 用法:
#   bash ~/person_task/catfish/central/llm-gateway/restart-local-gateway.sh
#
# 做:
#   1. verify · gateway venv 有 catfish_gateway + orjson + errors.py 中文 fix
#   2. kill 8999 残留
#   3. nohup 后台起 · log 落 ~/catfish-gateway-YYYYMMDD.log
#   4. verify · 8999 pid + log 前 30 行

set -uo pipefail

GW_PY=~/person_task/catfish/central/llm-gateway/venv/bin/python
GW_DIR=~/person_task/catfish/central/llm-gateway
LOG=~/catfish-gateway-$(date +%Y%m%d).log

echo "════════════════════════════════════════════"
echo " 本地 gateway restart · 7/19"
echo "════════════════════════════════════════════"

# 1. verify · gateway venv
echo ""
echo "→ [1/4] verify gateway venv..."
if ! $GW_PY -c "import catfish_gateway, orjson; print('  module OK · orjson', orjson.__version__)"; then
    echo "❌ gateway venv 挂 · catfish_gateway 或 orjson 没装"
    exit 1
fi

CN_COUNT=$(grep -c "上游 LLM 连续重试仍挂" $GW_DIR/src/catfish_gateway/errors.py 2>/dev/null || echo 0)
echo "  errors.py 中文匹配 (retries): $CN_COUNT (期望 1)"
if [[ "$CN_COUNT" == "0" ]]; then
    echo "  ⚠ 中文匹配没进 · restart 后也是英文报错 · 但 gateway 通"
fi

# 2. kill 残留
echo ""
echo "→ [2/4] kill 8999 残留..."
KILLED=0
for pid in $(lsof -ti:8999 -sTCP:LISTEN 2>/dev/null); do
    echo "  杀 pid=$pid"
    kill -9 $pid && KILLED=$((KILLED+1))
done
echo "  killed=$KILLED"
sleep 2

# 3. 起
echo ""
echo "→ [3/4] 起新 gateway..."
cd $GW_DIR
nohup $GW_PY -m catfish_gateway.app > "$LOG" 2>&1 &
disown
echo "  spawn 完 · 等 5 秒 · log=$LOG"
sleep 5

# 4. verify
echo ""
echo "→ [4/4] verify..."
NEW_PID=$(lsof -ti:8999 -sTCP:LISTEN 2>/dev/null)
if [[ -n "$NEW_PID" ]]; then
    echo ""
    echo "════════════════════════════════════════════"
    echo "✅ gateway 起来了 · pid=$NEW_PID"
    echo "════════════════════════════════════════════"
    ps -p $NEW_PID -o pid,etime,command
    echo ""
    echo "→ log 头 30 行:"
    head -30 "$LOG"
    echo ""
    echo "→ 手机 WeChat 发 hi · 测:"
    echo "  A. 中文回复 → 全通 ✅"
    echo "  B. \"上游 LLM 连续重试仍挂...\" → gateway 通 · 上游挂 · 查 .env keys"
    echo "  C. 还英文 → errors.py 没生效 · 贴 log"
else
    echo ""
    echo "════════════════════════════════════════════"
    echo "❌ gateway 没起 · 看 log:"
    echo "════════════════════════════════════════════"
    tail -60 "$LOG"
fi
