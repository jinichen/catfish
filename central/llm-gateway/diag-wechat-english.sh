#!/usr/bin/env bash
# BL-DIAG-WECHAT-ENGLISH (7/19): 5 步 · 定位 WeChat 英文串到底谁返 · 军规不瞎猜.
#
# 用法: bash ~/person_task/catfish/central/llm-gateway/diag-wechat-english.sh

set -uo pipefail

TODAY=$(date +%Y%m%d)
GW_LOG=~/catfish-gateway-$TODAY.log

echo "════════════════════════════════════════════"
echo " WeChat 英文串 · 5 步定位 · 7/19"
echo "════════════════════════════════════════════"

# ─── Step 1: pid ─────────────
echo ""
echo "→ [1/5] gateway 8999 + hermes 8642 pid"
lsof -i:8999,8642 -sTCP:LISTEN 2>/dev/null | grep -v COMMAND || echo "  (两个都空)"

# ─── Step 2: roles.yaml chat_default ─────────────
echo ""
echo "→ [2/5] roles.yaml chat_default (是否已切 deepseek)"
grep "^  chat_default:" ~/person_task/catfish/central/llm-gateway/config/roles.yaml | head -1

# ─── Step 3: hermes/config.yaml api_key 解析 ─────────────
echo ""
echo "→ [3/5] hermes/config.yaml api_key exp / aud / sub"
grep "^  api_key:" ~/.hermes/config.yaml | awk '{print $2}' > /tmp/hermes-api-key.txt
python3 <<'PYEOF'
import base64, json, time
with open('/tmp/hermes-api-key.txt') as f:
    tok_full = f.read().strip()
if not tok_full or '.' not in tok_full:
    print('  ❌ api_key 空 或 非 JWT')
else:
    tok = tok_full.split('.')[1]
    tok += '=' * (4 - len(tok) % 4)
    try:
        p = json.loads(base64.urlsafe_b64decode(tok))
        now = int(time.time())
        exp = p.get('exp', 0)
        remain = (exp - now) // 60
        status = "✓ 有效" if remain > 0 else "❌ 过期"
        print(f'  {status} · sub={p.get("sub")} · aud={p.get("aud")} · exp={exp} · now={now} · 剩={remain}min')
    except Exception as e:
        print(f'  ❌ 解析挂: {e}')
PYEOF
rm -f /tmp/hermes-api-key.txt

# ─── Step 4: gateway log 尾 30 行 ─────────────
echo ""
echo "→ [4/5] gateway log 尾 30 行"
if [[ -f "$GW_LOG" ]]; then
    tail -30 "$GW_LOG"
else
    echo "  ❌ log 不存在: $GW_LOG"
fi

# ─── Step 5: 直 curl gateway ─────────────
echo ""
echo "→ [5/5] 拿 fresh JWT · 直 curl gateway chat/completions"

FRESH_JWT=$(curl -sS -X POST http://127.0.0.1:8998/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=hermes-cli" \
    --data-urlencode "client_secret=hermes-dev-secret-2026-please-change" \
    --data-urlencode "scope=chat.completions" \
    2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))" 2>/dev/null)

if [[ -z "$FRESH_JWT" ]]; then
    echo "  ❌ 拿 JWT 挂 · identity 8998 挂?"
    curl -sS http://127.0.0.1:8998/healthz 2>&1 | head -3
else
    echo "  ✓ JWT 拿到 · 长度=${#FRESH_JWT}"
    echo ""
    echo "  用它 curl gateway (catfish-auto model)..."
    curl -sS -X POST http://127.0.0.1:8999/v1/chat/completions \
        -H "Authorization: Bearer $FRESH_JWT" \
        -H "Content-Type: application/json" \
        -d '{"model":"catfish-auto","messages":[{"role":"user","content":"hi"}],"stream":false}' \
        2>&1 | python3 -m json.tool 2>&1 | head -60
fi

echo ""
echo "════════════════════════════════════════════"
echo "判定 (贴以上给我 · 我判 A/B/C/D):"
echo "  A. 返 {\"choices\": ...} → gateway+deepseek 通 · hermes 8642 那边挂"
echo "  B. 返 {\"detail\": {\"friendly\": \"中文...\"}} → gateway 通 · hermes 取错字段"
echo "  C. 返 401 / Unauthorized → JWT/audience 挂"
echo "  D. 返 {\"detail\": {\"message\": \"Provider authentication failed\"}} → 上游 deepseek key 挂"
echo "════════════════════════════════════════════"
