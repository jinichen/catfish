#!/usr/bin/env bash
# verify-auth-decouple.sh — 验证 A1 + A2 + A4 治本方案完整 work.
#
# BL-AUTH-DECOUPLE-A4 (5/19) 落地后, hermes 跟 gateway 之间从
# "hermes 用 user JWT" 切到 "hermes 用 service token + X-Catfish-User header".
# 这个脚本一目了然现在到底走哪条路.
#
# 用法:
#   scripts/verify-auth-decouple.sh
#
# 退出码:
#   0  全绿
#   1  有红 (.env 没 service token / config.yaml 不是模板 / sub 看着像 user JWT)

set -u

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
ENV_FILE="$HERMES_HOME/.env"
CFG_FILE="$HERMES_HOME/config.yaml"

# ANSI
G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'

fail=0

echo "═══ A4 验证: hermes 用 service token 调 gateway ═══"
echo

# ── 1. .env 有 HERMES_SERVICE_TOKEN ──────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
    echo "${R}✗${N} .env 不存在: $ENV_FILE"
    fail=1
elif grep -q '^[[:space:]]*HERMES_SERVICE_TOKEN=' "$ENV_FILE"; then
    echo "${G}✓${N} .env 有 HERMES_SERVICE_TOKEN"
    SVC_TOKEN="$(grep -m1 '^[[:space:]]*HERMES_SERVICE_TOKEN=' "$ENV_FILE" | sed 's/^[[:space:]]*HERMES_SERVICE_TOKEN=//')"
    # decode JWT payload
    PAYLOAD="$(echo "$SVC_TOKEN" | cut -d. -f2)"
    while [[ $((${#PAYLOAD} % 4)) -ne 0 ]]; do PAYLOAD="${PAYLOAD}="; done
    DECODED="$(echo "$PAYLOAD" | tr '_-' '/+' | base64 -d 2>/dev/null || true)"
    if [[ -z "$DECODED" ]]; then
        echo "  ${R}✗${N} JWT payload 解不开 (token 损坏?)"
        fail=1
    else
        SUB="$(echo "$DECODED" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("sub",""))' 2>/dev/null)"
        IAT="$(echo "$DECODED" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("iat",0))' 2>/dev/null)"
        EXP="$(echo "$DECODED" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("exp",0))' 2>/dev/null)"
        TTL=$((EXP - IAT))
        NOW=$(date +%s)
        LEFT=$((EXP - NOW))
        echo "     sub=$SUB"
        echo "     ttl=${TTL}s (≈$((TTL / 86400)) 天)  剩余=${LEFT}s (≈$((LEFT / 86400)) 天)"
        # service token 的 sub 在 catfish-identity 里是 "client:<client_id>" 或 "<client_id>"
        if [[ "$SUB" == "client:hermes-cli" || "$SUB" == "hermes-cli" ]]; then
            echo "  ${G}✓${N} service token (sub=$SUB) — A4 真切上了"
        elif [[ "$SUB" == *"@"* ]]; then
            echo "  ${R}✗${N} 这看起来是 user JWT (sub=$SUB), A4 没切上"
            fail=1
        else
            echo "  ${Y}?${N} sub=$SUB — 不像 user 也不像标准 service, 自己看看"
        fi
        if [[ "$LEFT" -lt 0 ]]; then
            echo "  ${R}✗${N} token 已经过期! 重跑 mint-hermes-service-token.sh"
            fail=1
        elif [[ "$LEFT" -lt 86400 ]]; then
            echo "  ${Y}⚠${N} token 24h 内到期, 该续了"
        fi
    fi
else
    echo "${R}✗${N} .env 没 HERMES_SERVICE_TOKEN"
    echo "    跑: scripts/mint-hermes-service-token.sh"
    fail=1
fi

echo

# ── 2. config.yaml.model.api_key + custom_providers[*].api_key 是模板 ────
if [[ ! -f "$CFG_FILE" ]]; then
    echo "${R}✗${N} config.yaml 不存在: $CFG_FILE"
    fail=1
else
    python3 - "$CFG_FILE" <<'PY' || fail=1
import sys, pathlib, yaml
TEMPLATE = "${HERMES_SERVICE_TOKEN}"
G="\033[32m"; R="\033[31m"; Y="\033[33m"; N="\033[0m"

cfg = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")) or {}

def is_catfish(name, url):
    name = (name or "").lower(); url = (url or "").lower()
    return ("catfish" in name or "catfish" in url
            or "10.10.40.50" in url or "localhost:8999" in url
            or "127.0.0.1:8999" in url)

slots = []
m = cfg.get("model")
if isinstance(m, dict) and "api_key" in m and is_catfish("", m.get("base_url","")):
    slots.append(("model.api_key", m.get("api_key","")))
prov = cfg.get("providers")
if isinstance(prov, dict):
    for n, p in prov.items():
        if isinstance(p, dict) and "api_key" in p:
            url = p.get("base_url") or p.get("url") or ""
            if is_catfish(n, url):
                slots.append((f"providers.{n}.api_key", p.get("api_key","")))
cust = cfg.get("custom_providers")
if isinstance(cust, list):
    for i, p in enumerate(cust):
        if isinstance(p, dict) and "api_key" in p:
            if is_catfish(p.get("name",""), p.get("base_url","")):
                slots.append((f"custom_providers[{i}] ({p.get('name','?')}).api_key", p.get("api_key","")))

if not slots:
    print(f"  {Y}?{N} 没在 config.yaml 找到 catfish-gateway api_key 槽")
    sys.exit(1)

bad = 0
for label, v in slots:
    if v == TEMPLATE:
        print(f"  {G}✓{N} {label} = {TEMPLATE}")
    else:
        short = (v[:20] + "...") if v else "(empty)"
        print(f"  {R}✗{N} {label} = {short} (不是模板)")
        bad += 1
sys.exit(1 if bad else 0)
PY
fi

echo

# ── 3. hermes daemon 在跑吗 ──────────────────────────────────────────────
if pgrep -fl "hermes serve" >/dev/null 2>&1; then
    echo "${G}✓${N} hermes daemon 在跑"
    echo "     $(pgrep -fl 'hermes serve' | head -1)"
else
    echo "${Y}?${N} hermes daemon 没在跑"
    echo "     重启:  pkill -f 'hermes serve' 2>/dev/null; nohup hermes serve > ~/.hermes/serve.log 2>&1 &"
fi

echo
echo "─── 下一步实测 ──────────────────────────────────────────────────────"
echo
echo "  1. 重启 hermes (让它重新 load config.yaml + .env):"
echo "       pkill -f 'hermes serve' 2>/dev/null"
echo "       sleep 1"
echo "       nohup hermes serve > ~/.hermes/serve.log 2>&1 &"
echo
echo "  2. 真发一条 chat (任何客户端: CLI / Companion / Feishu / Weixin)"
echo
echo "  3. 看 catfish-gateway log 应该出现 A1 marker:"
echo "       BL-AUTH-DECOUPLE-A1 effective_user_email=<your-email> (sub=client:hermes-cli)"
echo
echo "     如果出现 → A1 + A2 + A4 路径全部跑通了."
echo "     如果还是 sub=<email> (user JWT) → A4 没真切上, 看上面哪步红了."
echo

exit $fail
