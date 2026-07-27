#!/usr/bin/env bash
# BL-HERMES-ENV-JWT-REFRESH (7/19): 军规漏!
# refresh-jwt-and-restart.sh 只改了 ~/.hermes/config.yaml · 没改 ~/.hermes/.env OPENAI_API_KEY.
# hermes credential_pool 从 env:OPENAI_API_KEY 拿 · config.yaml 只喂 model 段 · 分裂路径.
# 这次一次修完 · 且 reset auth.json exhausted 状态.
#
# BL-PLUGIN-AUTH-FIX (7/27 鸿波): scope 加 background.tasks.
#   hermes 进程内 plugin (catfish-memory distill/summarize · catfish-xcatfish-user
#   memory_enforce) 复用本脚本写的 OPENAI_API_KEY 调 gateway. gateway 侧
#   app.py is_internal_call 校验这个 scope 才允许免 quota (堵 header 滥用洞).
#   没这 scope → plugin 调用照常扣员工 quota (功能不断 · 只是记账变了).
#   ⚠ 前置: identity-server clients.yaml 的 hermes-cli 必须先有 background.tasks
#      在 allowed_scopes 里 (不然 /token 返 invalid_scope), 且 identity 要重启.

set -uo pipefail

echo "════════════════════════════════════════════"
echo " Refresh hermes/.env OPENAI_API_KEY · 7/19"
echo "════════════════════════════════════════════"

# ─── [1/4] 拿 30 天 service JWT ─────────────
echo ""
echo "→ [1/4] 拿 30 天 service JWT (hermes-cli client_credentials)..."
NEW_JWT=$(curl -sS -X POST http://127.0.0.1:8998/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=hermes-cli" \
    --data-urlencode "client_secret=hermes-dev-secret-2026-please-change" \
    --data-urlencode "scope=chat.completions background.tasks" \
    2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [[ -z "$NEW_JWT" ]]; then
    echo "❌ identity 8998 挂 · 拿不到 JWT"
    exit 1
fi
echo "  ✓ JWT 拿到 · 长度=${#NEW_JWT}"

# ─── [2/4] sed 塞 ~/.hermes/.env OPENAI_API_KEY ─────────────
echo ""
echo "→ [2/4] sed ~/.hermes/.env OPENAI_API_KEY..."
cp ~/.hermes/.env ~/.hermes/.env.bak-$(date +%s)
python3 <<PYEOF
import re
p = '/Users/chenhongbo/.hermes/.env'
with open(p) as f: content = f.read()
if 'OPENAI_API_KEY=' in content:
    new = re.sub(r'^OPENAI_API_KEY=.*\$', 'OPENAI_API_KEY=$NEW_JWT', content, count=1, flags=re.M)
else:
    new = content.rstrip() + '\nOPENAI_API_KEY=$NEW_JWT\n'
with open(p, 'w') as f: f.write(new)
print('  ✓ OPENAI_API_KEY 替换/追加完')
PYEOF

# 同步 · config.yaml api_key (保险 · 双写)
echo ""
echo "  → 同步 · config.yaml model.api_key..."
python3 <<PYEOF
import re
p = '/Users/chenhongbo/.hermes/config.yaml'
with open(p) as f: content = f.read()
new = re.sub(r'^(  api_key:\s*).*\$', r'\1$NEW_JWT', content, count=1, flags=re.M)
with open(p, 'w') as f: f.write(new)
print('  ✓ config.yaml api_key 也同步')
PYEOF

# ─── [3/4] reset auth.json exhausted 状态 ─────────────
echo ""
echo "→ [3/4] reset ~/.hermes/auth.json openai-api 池 (last_status=exhausted 会拒调)..."
python3 <<PYEOF
import json
p = '/Users/chenhongbo/.hermes/auth.json'
try:
    with open(p) as f: d = json.load(f)
    reset_count = 0
    for cred in d.get('credential_pool', {}).get('openai-api', []):
        cred['last_status'] = 'healthy'
        cred['last_error_code'] = None
        cred['last_error_message'] = None
        cred['last_error_reset_at'] = None
        reset_count += 1
    with open(p, 'w') as f: json.dump(d, f, indent=2)
    print(f'  ✓ reset {reset_count} 个 openai-api cred 到 healthy')
except FileNotFoundError:
    print('  ⚠ auth.json 不存在 · 跳过 (hermes 首启会重建)')
except Exception as e:
    print(f'  ⚠ reset 挂 (不阻塞): {e}')
PYEOF

# ─── [4/4] hermes 冷启 (kill -9 + bootout + install --force + start) ─────────────
echo ""
echo "→ [4/4] hermes 8642 冷启..."
export PATH=~/.hermes/hermes-agent/venv/bin:$PATH

hermes gateway stop 2>&1 | tail -3
sleep 3
pkill -9 -f "hermes.*gateway" 2>/dev/null || true
sleep 2
launchctl bootout gui/$(id -u)/ai.hermes.gateway 2>/dev/null || true
hermes gateway install --force 2>&1 | tail -3
hermes gateway start 2>&1 | tail -3

# BL-REFRESH-JWT-WAIT-FIX (7/27 鸿波实测): 老代码 `sleep 5` 就查端口 → 误报
# "❌ hermes 8642 没起". 真实冷启耗时 (7/27 11:14 日志):
#   11:14:57.706  API server listening on 8642
#   11:15:20.463  Press Ctrl+C to stop  ← 真就绪, 距 start 约 23s
# 5s 时连 listening 都可能没到. 改**轮询到 45s**, 每 2s 查一次, 起来就早退.
echo "  → 等 hermes 8642 起 (冷启约 20-30s, 最多等 45s)..."
NEW_PID=""
for i in $(seq 1 23); do
    NEW_PID=$(lsof -ti:8642 -sTCP:LISTEN 2>/dev/null | head -1)
    [[ -n "$NEW_PID" ]] && break
    sleep 2
done

if [[ -n "$NEW_PID" ]]; then
    echo ""
    echo "════════════════════════════════════════════"
    echo "✅ 全修完 · hermes 8642 pid=$NEW_PID (等了 $((i * 2))s)"
    echo "════════════════════════════════════════════"
    ps -p $NEW_PID -o pid,etime,command | head -2
    echo ""
    echo "→ 手机 WeChat 发 hi · 期望: DeepSeek 中文回复"
    echo ""
    echo "→ 若还英文 · tail 抓 log:"
    echo "  tail -20 ~/catfish-gateway-\$(date +%Y%m%d).log | grep -E 'chat|401|200|exp|sub'"
else
    echo "❌ hermes 8642 等 45s 还没起 · 手工排查:"
    echo "  hermes gateway status"
    echo "  tail -40 ~/.hermes/logs/gateway.log"
    echo "  tail -20 ~/.hermes/logs/gateway.error.log"
    echo ""
    echo "  (注 · 先看 gateway.log 有没有 'API server listening on 8642' —"
    echo "   有就是还在起, 再等等; 没有才是真挂)"
fi
