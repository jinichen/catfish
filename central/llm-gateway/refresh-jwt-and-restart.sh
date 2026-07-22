#!/usr/bin/env bash
# BL-JWT-REFRESH-HOME (7/19 Task #15 一部分): 家里 · JWT 过期 + chat_default 内网不通 · 一次修.
#
# 做:
#   1. 若 identity 8998 挂 · 提示手动起
#   2. 用 hermes-cli client_credentials 拿 30 天 service JWT (aud=catfish-gateway)
#   3. sed 塞 hermes/config.yaml api_key
#   4. sed roles.yaml chat_default → catfish-public-deepseek-flash (家里能通)
#   5. gateway restart (nohup + disown)
#   6. hermes gateway restart (让 config.yaml 生效)
#
# 后续 (Task #15 长期): Companion Rust 端 · 定时用 refresh_token · 或 · 面板改 IP 时 · 自动 refresh JWT + 写 hermes/config.yaml.

set -uo pipefail

GW_PY=~/person_task/catfish/central/llm-gateway/venv/bin/python
GW_DIR=~/person_task/catfish/central/llm-gateway
IDENTITY_URL=http://127.0.0.1:8998
HERMES_CFG=~/.hermes/config.yaml
ROLES_YAML=$GW_DIR/config/roles.yaml
LOG=~/catfish-gateway-$(date +%Y%m%d).log

echo "════════════════════════════════════════════"
echo " Refresh JWT + roles.yaml + restart · 7/19"
echo "════════════════════════════════════════════"

# ─── [1/6] verify identity 8998 起 ─────────────
echo ""
echo "→ [1/6] verify identity 8998..."
if ! curl -sS -f -o /dev/null $IDENTITY_URL/healthz; then
    echo "❌ identity 8998 挂 · 先起 identity · 再跑这脚本"
    exit 1
fi
echo "  ✓ identity 8998 通"

# ─── [2/6] 用 client_credentials 拿 30 天 service JWT ─────────────
echo ""
echo "→ [2/6] 拿 30 天 service JWT (hermes-cli client_credentials)..."
JWT_RESP=$(curl -sS -X POST $IDENTITY_URL/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=hermes-cli" \
    --data-urlencode "client_secret=hermes-dev-secret-2026-please-change" \
    --data-urlencode "scope=chat.completions audit.write tools.invoke skills.run")

NEW_JWT=$(echo "$JWT_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('access_token', ''))" 2>/dev/null)

if [[ -z "$NEW_JWT" ]]; then
    echo "❌ 拿 JWT 挂 · resp:"
    echo "$JWT_RESP"
    exit 1
fi

# decode 看 exp
$GW_PY -c "
import base64, json, time
tok = '$NEW_JWT'.split('.')[1]
tok += '=' * (4 - len(tok) % 4)
p = json.loads(base64.urlsafe_b64decode(tok))
print(f'  ✓ JWT 拿到 · sub={p.get(\"sub\")} · aud={p.get(\"aud\")} · exp={p.get(\"exp\")} · TTL={(p.get(\"exp\", 0) - int(time.time())) // 86400} 天')
"

# ─── [3/6] sed 塞 hermes/config.yaml api_key ─────────────
echo ""
echo "→ [3/6] 塞新 JWT 到 $HERMES_CFG..."
cp "$HERMES_CFG" "$HERMES_CFG.bak-$(date +%s)"
# api_key 那行替换 (2-space indent)
$GW_PY -c "
import re
p = '$HERMES_CFG'
with open(p) as f: content = f.read()
new = re.sub(r'^(  api_key:\s*).*\$', r'\1$NEW_JWT', content, count=1, flags=re.M)
with open(p, 'w') as f: f.write(new)
print('  ✓ api_key 替换完')
"

# ─── [4/6] sed roles.yaml chat_default → deepseek ─────────────
echo ""
echo "→ [4/6] roles.yaml chat_default → catfish-public-deepseek-flash..."
CUR=$(grep "^  chat_default:" $ROLES_YAML | head -1 | awk '{print $2}')
echo "  当前: $CUR"
if [[ "$CUR" != "catfish-public-deepseek-flash" ]]; then
    cp "$ROLES_YAML" "$ROLES_YAML.bak-$(date +%s)"
    sed -i '' 's|^  chat_default: .*|  chat_default: catfish-public-deepseek-flash|' $ROLES_YAML
    echo "  ✓ 已切"
else
    echo "  ✓ 已是 deepseek · 跳过"
fi

# ─── [5/6] gateway restart ─────────────
echo ""
echo "→ [5/6] gateway restart..."
for pid in $(lsof -ti:8999 -sTCP:LISTEN 2>/dev/null); do
    echo "  杀老 pid=$pid"
    kill -9 $pid
done
sleep 2

cd $GW_DIR
nohup $GW_PY -m catfish_gateway.app > "$LOG" 2>&1 &
disown
sleep 5

NEW_GW_PID=$(lsof -ti:8999 -sTCP:LISTEN 2>/dev/null)
if [[ -n "$NEW_GW_PID" ]]; then
    echo "  ✓ gateway 起 · pid=$NEW_GW_PID"
else
    echo "  ❌ gateway 没起 · log:"
    tail -30 "$LOG"
    exit 1
fi

# ─── [6/6] hermes restart ─────────────
echo ""
echo "→ [6/6] hermes 8642 restart..."
export PATH=~/.hermes/hermes-agent/venv/bin:$PATH
hermes gateway restart 2>&1 | tail -5

sleep 3
NEW_HERMES_PID=$(lsof -ti:8642 -sTCP:LISTEN 2>/dev/null)
if [[ -n "$NEW_HERMES_PID" ]]; then
    echo "  ✓ hermes 起 · pid=$NEW_HERMES_PID"
else
    echo "  ⚠ hermes 8642 空 · 手工 restart: hermes gateway start"
fi

# ─── 结束 · 手机 WeChat 测 ─────────────
echo ""
echo "════════════════════════════════════════════"
echo "✅ 全修完 · 手机 WeChat 发 hi 测"
echo "════════════════════════════════════════════"
echo "期望: DeepSeek 中文回复 · 家里网通"
echo ""
echo "若仍挂 · 3 步 debug:"
echo "  a. tail -30 $LOG"
echo "  b. curl -sS -H \"Authorization: Bearer <新JWT>\" http://127.0.0.1:8999/v1/catalog"
echo "  c. hermes gateway status"
