#!/usr/bin/env bash
# 命令行跑完整 OIDC 登录链路
#
# 用途:
#   装机现场没有浏览器 / 要在 CI 里做冒烟测试时, 用 curl 走完
#   authorize → code → token → userinfo → 拿 access_token 调 gateway.
#
# 用法:
#   bash verify-login.sh                                  # 从 .env 读 issuer
#   BASE=https://192.168.31.199 bash verify-login.sh       # 显式指定
#   EMAIL=admin@catfish.com PASSWORD=catfish_2026 bash verify-login.sh
#
# ── 这个脚本验得到什么 / 验不到什么 ──────────────────────────────
#
# 验得到:
#   - 登录页能不能出来 (nginx → identity 的 /authorize 反代)
#   - 账号密码对不对 (常见原因: 重装时 users.yaml 已存在会被跳过,
#     密码回显只在生成分支里, IT 拿不到密码, 报 "email 或密码错误")
#   - 授权码能不能换出 token, id_token 的 iss 跟 discovery 是否一致
#   - access_token 能不能过 gateway 的验签 (JWKS 链路)
#
# 验不到:
#   - 浏览器 crypto.subtle 是否可用. 前端 oidc-client 算 PKCE challenge 要用它,
#     而它只在安全上下文 (https / localhost) 提供 —— 这是走 HTTPS 的**唯一**理由.
#     curl 不跑 JS, 所以这条只能靠浏览器实测. 好在 https origin = 安全上下文
#     是规范保证, 风险低.
#   - 前端 bundle 本身的行为 (路由、渲染、拿到 token 之后干了什么)
#
# ⚠ 注意: 服务端目前**不校验 PKCE** (identity-server 里没有 code_challenge /
#   code_verifier 的实现), 且 redirect_uri 没有白名单 (routes.py:23 已标 Phase 2).
#   所以本脚本不发 code_challenge 也能换出 token —— 这是符合当前实现的,
#   不是脚本偷懒.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 参数 ────────────────────────────────────────────────
BASE="${BASE:-}"
if [ -z "$BASE" ]; then
    if [ ! -f .env ]; then
        echo "❌ 没有 .env 且未指定 BASE"
        echo "   用法: BASE=https://192.168.31.199 bash verify-login.sh"
        exit 1
    fi
    BASE=$(grep -E "^CATFISH_IDENTITY_ISSUER=" .env | head -1 | cut -d= -f2- || true)
    if [ -z "$BASE" ]; then
        echo "❌ .env 里没有 CATFISH_IDENTITY_ISSUER"
        exit 1
    fi
fi
BASE="${BASE%/}"

EMAIL="${EMAIL:-admin@catfish.com}"
PASSWORD="${PASSWORD:-catfish_2026}"
CLIENT_ID="${CLIENT_ID:-catfish-web}"
REDIRECT_URI="${REDIRECT_URI:-$BASE/auth/callback}"

# 自签证书要 -k. 这里只验"链路通不通", 证书信任是员工浏览器侧的事.
CURL=(curl -sS --connect-timeout 5 --max-time 20)
case "$BASE" in
    https://*) CURL+=(-k) ;;
esac

# gateway 直连地址 (走 8999, 不经 nginx) —— 用来验 JWKS 验签链路.
GW_HOST=$(echo "$BASE" | sed -E 's#^https?://##; s#[:/].*$##')
GATEWAY="${GATEWAY:-http://$GW_HOST:8999}"

echo "═══════════════════════════════════════════════"
echo "  OIDC 登录链路验证 (命令行)"
echo "═══════════════════════════════════════════════"
echo "  issuer   : $BASE"
echo "  账号     : $EMAIL"
echo "  client_id: $CLIENT_ID"
echo "  gateway  : $GATEWAY"
echo ""

fail() { echo "❌ $1"; exit 1; }

# ── 1. discovery ────────────────────────────────────────
echo -n "1/6 discovery ............ "
DISC=$("${CURL[@]}" "$BASE/.well-known/openid-configuration") \
    || fail "拉不到 discovery · 检查 web 容器 443 反代"
DISC_ISS=$(echo "$DISC" | grep -oE '"issuer"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | cut -d'"' -f4)
[ -n "$DISC_ISS" ] || fail "discovery 里没有 issuer 字段: $DISC"
if [ "$DISC_ISS" != "$BASE" ]; then
    fail "issuer 不一致 · discovery 返 '$DISC_ISS' 但访问的是 '$BASE'
     两者不同会让 JWT 的 iss claim 验不过. 改 .env 的 CATFISH_IDENTITY_ISSUER 后
     docker compose up -d --force-recreate identity"
fi
echo "✓ issuer=$DISC_ISS"

# ── 2. jwks ─────────────────────────────────────────────
#
# 多采样几次: 单次请求只会落到**一个** worker, 拿到的是那个 worker 自己的
# signer. 两个 worker 各持一把不同的钥匙时, 单次请求照样只看到 1 个 kid ——
# 只请求一次的话这个检查抓不到竞态, 等于假绿灯.
#
# 采样 N 次取并集: 多 worker 场景下内核会把连接分给不同 worker, 采够次数
# 大概率覆盖到所有 worker. 注意这是**概率性**的, 不是确定性证明 ——
# 要确定性结论看密钥文件 (setup.sh 的"identity 签名密钥"那一项):
#   docker compose exec identity ls -1 /home/catfish/.catfish/identity-server/keys/
# 只有一个 private.pem 才是竞态修复的硬证据.
echo -n "2/6 jwks ................. "
JWKS_SAMPLES="${JWKS_SAMPLES:-12}"
ALL_KIDS=""
for _ in $(seq 1 "$JWKS_SAMPLES"); do
    J=$("${CURL[@]}" "$BASE/.well-known/jwks.json") || fail "拉不到 jwks"
    ALL_KIDS="$ALL_KIDS
$(echo "$J" | grep -oE '"kid"[[:space:]]*:[[:space:]]*"[^"]+"' | cut -d'"' -f4 || true)"
done
UNIQ=$(echo "$ALL_KIDS" | grep -v '^$' | sort -u)
NKEYS=$(echo "$UNIQ" | grep -cv '^$' || true)
[ "$NKEYS" != "0" ] || fail "jwks 里没有 key"
if [ "$NKEYS" != "1" ]; then
    fail "采样 $JWKS_SAMPLES 次看到 $NKEYS 把不同的 kid · 多 worker 各生成了自己的 RSA:
$(echo "$UNIQ" | sed 's/^/       /')
     JWT 验签会随机失败 (命中哪个 worker 签的就哪个通). 查密钥目录:
       docker compose exec identity ls -la /home/catfish/.catfish/identity-server/keys/"
fi
echo "✓ 采样 ${JWKS_SAMPLES} 次均为同一 kid"

# ── 3. 登录页 ───────────────────────────────────────────
echo -n "3/6 登录页 ............... "
AUTH_URL="$BASE/authorize?client_id=$CLIENT_ID&redirect_uri=$REDIRECT_URI&response_type=code&scope=openid&state=cliverify"
PAGE=$("${CURL[@]}" "$AUTH_URL") || fail "拿不到登录页"
echo "$PAGE" | grep -q 'name="password"' \
    || fail "返回的不是登录页 (没有 password 字段):
$(echo "$PAGE" | head -c 300)"
echo "✓ 表单正常"

# ── 4. 提交登录拿 code ──────────────────────────────────
echo -n "4/6 登录 ................. "
LOC=$("${CURL[@]}" -o /dev/null -w '%{redirect_url}' \
    -X POST "$BASE/authorize" \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "redirect_uri=$REDIRECT_URI" \
    --data-urlencode "scope=openid" \
    --data-urlencode "state=cliverify" \
    --data-urlencode "email=$EMAIL" \
    --data-urlencode "password=$PASSWORD") || fail "POST /authorize 失败"

if [ -z "$LOC" ]; then
    fail "登录没有返回跳转 —— 密码不对 (identity 返 401 + 'email 或密码错误').
     常见原因: 重装时 users.yaml 已存在会被跳过, 而密码只在
     生成分支里, IT 拿不到密码.
     重置办法: 删 identity-server/config/users.yaml 和数据库里的用户后重装,
     或让已有 admin 走 /me/password 改密."
fi
CODE=$(echo "$LOC" | sed -nE 's/.*[?&]code=([^&]+).*/\1/p')
[ -n "$CODE" ] || fail "跳转里没有 code: $LOC"
echo "✓ 拿到授权码 ${CODE:0:8}..."

# ── 5. 换 token ─────────────────────────────────────────
echo -n "5/6 换 token ............. "
TOK=$("${CURL[@]}" -X POST "$BASE/token" \
    --data-urlencode "grant_type=authorization_code" \
    --data-urlencode "code=$CODE" \
    --data-urlencode "redirect_uri=$REDIRECT_URI" \
    --data-urlencode "client_id=$CLIENT_ID") || fail "POST /token 失败"
ACCESS=$(echo "$TOK" | grep -oE '"access_token"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | cut -d'"' -f4)
[ -n "$ACCESS" ] || fail "没换出 access_token: $TOK"

# id_token 的 iss 必须跟 discovery 一致 (gateway 就是拿这个比对的)
IDT=$(echo "$TOK" | grep -oE '"id_token"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | cut -d'"' -f4)
if [ -n "$IDT" ]; then
    PAYLOAD=$(echo "$IDT" | cut -d. -f2 | tr '_-' '/+')
    case $(( ${#PAYLOAD} % 4 )) in 2) PAYLOAD="$PAYLOAD==" ;; 3) PAYLOAD="$PAYLOAD=" ;; esac
    TOK_ISS=$(echo "$PAYLOAD" | base64 -d 2>/dev/null | grep -oE '"iss"[[:space:]]*:[[:space:]]*"[^"]+"' | cut -d'"' -f4 || true)
    if [ -n "$TOK_ISS" ] && [ "$TOK_ISS" != "$DISC_ISS" ]; then
        fail "id_token 的 iss='$TOK_ISS' 跟 discovery 的 '$DISC_ISS' 不一致 · gateway 会拒"
    fi
fi
echo "✓ access_token + id_token"

# ── 6. 拿 token 调 userinfo + gateway ───────────────────
echo -n "6/6 token 可用 ........... "
UI=$("${CURL[@]}" "$BASE/userinfo" -H "Authorization: Bearer $ACCESS") \
    || fail "userinfo 调不通"
echo "$UI" | grep -q '"sub"' || fail "userinfo 返回异常: $UI"

# gateway 拉 JWKS 验签 —— 这条独立于上面, 走的是 CATFISH_OIDC_JWKS_URI.
# HTTPS 模式下它必须走容器内网 http://identity:8998, 否则会撞自签证书验证失败.
GW=$(curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w '%{http_code}' \
    "$GATEWAY/v1/models" -H "Authorization: Bearer $ACCESS" || echo "000")
case "$GW" in
    200) echo "✓ userinfo + gateway 验签都通过" ;;
    401|403) fail "userinfo 通了但 gateway 返 $GW · gateway 验签失败.
     多半是 CATFISH_OIDC_JWKS_URI 配错. HTTPS 模式下它必须是
     http://identity:8998/.well-known/jwks.json (容器内网直连),
     指到 https://<ip>/... 会撞自签证书验证失败." ;;
    000) fail "连不上 gateway ($GATEWAY) · 检查 docker compose ps" ;;
    *)   fail "gateway 返 $GW · 看 docker logs catfish-gateway" ;;
esac

echo ""
echo "═══════════════════════════════════════════════"
echo "  ✓ 登录链路全通"
echo "═══════════════════════════════════════════════"
echo "  未覆盖: 浏览器 crypto.subtle (前端 PKCE 要用, 只有 https/localhost 才有)."
echo "         有浏览器时打开 $BASE 实测一次即可闭环."
