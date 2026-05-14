#!/usr/bin/env bash
# BL-HERMES013-INTEGRATE 端到端 OAuth 测脚本 (5/14 evening 真接).
#
# 用法:   bash ~/person_task/catfish/scripts/test_oauth_e2e.sh
# 不要用: zsh 跑 (zsh 的 = 展开会吃 env 变量, 用 bash 安全)
#
# 这个脚本:
#   1. 干净杀光所有 catfish_gateway 进程
#   2. 检验 .env 已有 OIDC 配置
#   3. 重启 gateway (用 .env 里的 env, 不用 inline)
#   4. 等 gateway 真起来 + 验 OIDCProvider 已初始化
#   5. 用 client_credentials 拿 service token
#   6. 真打 /v1/models + /v1/chat/completions
#   7. 每步失败立即停, 报清楚原因
#
# 每步 prefix 用清楚标记:  [STEP N]  / [OK]  / [FAIL]  / [INFO]

set -u  # 不允许未定义变量
# 不用 set -e — 我们要自己控制错误处理给清楚 message

# 颜色 (失败明显, 成功淡)
RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RST=$'\033[0m'

ok()    { echo "${GREEN}[OK]${RST} $*"; }
info()  { echo "${CYAN}[INFO]${RST} $*"; }
fail()  { echo "${RED}[FAIL]${RST} $*" >&2; }
step()  { echo ""; echo "${YELLOW}━━━ [STEP $1] $2 ━━━${RST}"; }

CATFISH_HOME="$HOME/person_task/catfish"
GATEWAY_DIR="$CATFISH_HOME/central/llm-gateway"
GATEWAY_LOG="/tmp/catfish-gateway.log"
IDENTITY_URL="http://localhost:8998"
GATEWAY_URL="http://localhost:8999"

# ─────────────────────────────────────────────────────────────
step 1 "杀光所有 gateway 进程"
# ─────────────────────────────────────────────────────────────
pkill -f catfish_gateway 2>/dev/null
sleep 2
remaining=$(ps aux | grep -E "catfish_gateway" | grep -v grep | wc -l | tr -d ' ')
if [ "$remaining" -gt 0 ]; then
    fail "$remaining 个 gateway 进程没死, 可能有 launchctl 守护"
    ps aux | grep catfish_gateway | grep -v grep
    exit 1
fi
ok "所有 gateway 进程已杀"

# ─────────────────────────────────────────────────────────────
step 2 "检验 .env 已有 OIDC 配置"
# ─────────────────────────────────────────────────────────────
ENV_FILE="$GATEWAY_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    fail ".env 不存在: $ENV_FILE"
    exit 1
fi
missing=()
grep -q "^CATFISH_ENV=prod" "$ENV_FILE" || missing+=("CATFISH_ENV=prod")
grep -q "^CATFISH_OIDC_ISSUER=" "$ENV_FILE" || missing+=("CATFISH_OIDC_ISSUER")
grep -q "^CATFISH_OIDC_AUDIENCE=" "$ENV_FILE" || missing+=("CATFISH_OIDC_AUDIENCE")
if [ ${#missing[@]} -gt 0 ]; then
    fail ".env 缺这些行: ${missing[*]}"
    info "现 .env 末尾:"
    tail -10 "$ENV_FILE"
    exit 1
fi
ok ".env 含 3 个 OIDC 配置项"

# ─────────────────────────────────────────────────────────────
step 3 "启动 gateway (从 .env 读 env)"
# ─────────────────────────────────────────────────────────────
cd "$GATEWAY_DIR" || { fail "进 $GATEWAY_DIR 失败"; exit 1; }
PYBIN="./venv/bin/python"
if [ ! -x "$PYBIN" ]; then
    fail "$PYBIN 不存在或不可执行 — venv 坏了"
    ls -la venv/bin/python* 2>&1 | head -3
    exit 1
fi
nohup "$PYBIN" -m catfish_gateway.app > "$GATEWAY_LOG" 2>&1 &
GW_PID=$!
info "gateway 启动中, PID=$GW_PID, 等 5 秒..."
sleep 5

# ─────────────────────────────────────────────────────────────
step 4 "验 gateway 起来了 + OIDCProvider 已初始化"
# ─────────────────────────────────────────────────────────────
if ! kill -0 "$GW_PID" 2>/dev/null; then
    fail "gateway PID $GW_PID 已死. 看 log:"
    tail -40 "$GATEWAY_LOG"
    exit 1
fi
listening=$(lsof -i :8999 2>/dev/null | grep LISTEN | wc -l | tr -d ' ')
if [ "$listening" -eq 0 ]; then
    fail "8999 没人听. 看 log:"
    tail -40 "$GATEWAY_LOG"
    exit 1
fi
ok "gateway 在 8999 听着"

# 注: AuthProvider 是 lazy singleton — 第一次 auth 请求才初始化.
# 所以启动 log 里**没**有 OIDCProvider 行是正常的, step 6 调 /v1/models 时才触发.
# 我们这里只快速验 mode=prod 在 log 里 (确认 .env 真被读了).
prod_mode=$(grep -E "mode=prod" "$GATEWAY_LOG" | head -1)
if [ -z "$prod_mode" ]; then
    fail "log 里 mode 不是 prod (.env 没生效). log 头 30 行:"
    head -30 "$GATEWAY_LOG"
    exit 1
fi
ok "gateway mode=prod (auth provider 会在 step 6 第一次 request 时 lazy 初始化)"

# ─────────────────────────────────────────────────────────────
step 5 "用 client_credentials 拿 service token"
# ─────────────────────────────────────────────────────────────
TOKEN_RESP=$(curl -s -X POST "$IDENTITY_URL/token" \
    -d "grant_type=client_credentials" \
    -d "client_id=hermes-cli" \
    -d "client_secret=hermes-dev-secret-2026-please-change" \
    -d "scope=chat.completions")
TOKEN=$(echo "$TOKEN_RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('access_token', ''))
except Exception as e:
    print('', file=sys.stderr)
    print('JSON parse 失败:', e, file=sys.stderr)
")
if [ -z "$TOKEN" ]; then
    fail "拿 token 失败. catfish-identity 响应:"
    echo "$TOKEN_RESP" | python3 -m json.tool 2>&1 | head -10
    exit 1
fi
ok "拿到 token (前 60): ${TOKEN:0:60}..."

# 解 token claims
echo ""
info "token claims:"
echo "$TOKEN" | python3 -c "
import sys, json, base64
parts = sys.stdin.read().strip().split('.')
payload = parts[1] + '=' * (-len(parts[1]) % 4)
print(json.dumps(json.loads(base64.urlsafe_b64decode(payload)), indent=2, ensure_ascii=False))
"

# ─────────────────────────────────────────────────────────────
step 6 "真打 /v1/models (核心 OIDC 验签测试)"
# ─────────────────────────────────────────────────────────────
MODELS_RESP=$(curl -s "$GATEWAY_URL/v1/models" -H "Authorization: Bearer $TOKEN")
echo "$MODELS_RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'detail' in d:
        print('REJECT:', d['detail'])
        sys.exit(2)
    if 'data' in d:
        print('OK: 返', len(d['data']), '个模型:')
        for m in d['data'][:10]:
            print('  -', m.get('id'))
        sys.exit(0)
    print('UNKNOWN response:', json.dumps(d, ensure_ascii=False)[:200])
    sys.exit(3)
except json.JSONDecodeError:
    print('NOT JSON. raw response:')
    print(sys.stdin.read()[:300] if hasattr(sys.stdin, 'read') else 'empty')
    sys.exit(4)
"
RC=$?
if [ "$RC" -ne 0 ]; then
    fail "/v1/models 拒绝. 看 gateway log 找 PyJWT 真原因:"
    tail -30 "$GATEWAY_LOG" | grep -E "OIDC|jwt|token|invalid|reject|verify" -i || tail -10 "$GATEWAY_LOG"
    exit 1
fi
ok "/v1/models 通过! gateway 接 catfish-identity 签的 token 了"

# ─────────────────────────────────────────────────────────────
step 7 "真 chat completion (端到端 LLM 调用)"
# ─────────────────────────────────────────────────────────────
CHAT_RESP=$(curl -s -X POST "$GATEWAY_URL/v1/chat/completions" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"model":"catfish-public-deepseek-flash","messages":[{"role":"user","content":"用一句话回答: 1+1 等于几"}]}')
echo ""
info "chat 响应:"
echo "$CHAT_RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'detail' in d:
        print('REJECT:', d['detail'])
        sys.exit(2)
    if 'choices' in d:
        print('OK! LLM 答:', d['choices'][0]['message']['content'])
        if 'usage' in d:
            u = d['usage']
            print('   tokens:', u.get('total_tokens'), '(in:', u.get('prompt_tokens'), 'out:', u.get('completion_tokens'),')')
        sys.exit(0)
    print('UNKNOWN:', json.dumps(d, ensure_ascii=False)[:300])
    sys.exit(3)
except json.JSONDecodeError:
    print('NOT JSON:', sys.stdin.read()[:300])
    sys.exit(4)
"
RC=$?

echo ""
echo "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
if [ "$RC" -eq 0 ]; then
    echo "${GREEN}🎉 全链路通! BL-HERMES013-INTEGRATE 真集成 ship.${RST}"
    echo ""
    echo "下一步 — 配 hermes:"
    echo "  hermes model --portal-url $IDENTITY_URL --inference-url $GATEWAY_URL --client-id hermes-cli"
    echo ""
    echo "(浏览器登录 chenhongbo@ffcs.cn / catfish123)"
else
    fail "chat 完成失败. 看 gateway log 末 30 行排查:"
    tail -30 "$GATEWAY_LOG"
fi
