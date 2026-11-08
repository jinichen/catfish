#!/usr/bin/env bash
# setup.sh 的 .env 生成回归测试 —— 重跑装机不能换密钥。
#
# 跑法:  bash test_setup_env.sh
#
# # 这组测试守的是什么 (8/4)
#
# CATFISH_SECRET_KEY 是供应商 API key 的加密主密钥。setup.sh 里保护它的逻辑
# ("有值就不动") 写在 `cp .env.example .env` **之后**, 而 .env.example 里这一行
# 是空的 —— 于是每次重跑装机必然判空、生成新钥匙, 那句"已有值·保持不变"
# 永远走不到。
#
# 这个错的形状跟 PG_PASSWORD 那次一模一样 (setup.sh:124 注释里记着), 修的时候
# 给 PG_PASSWORD 和 JWT_SIGNING_KEY 接上了捞取, 8/1 加第三个 key 时漏了。
# 所以这里三个 key 一起测 —— 下次再加第四个, 至少能照着抄。
#
# 用 REGEN_ENV_ONLY=1: setup.sh 在 .env 生成完就退出, 不碰 docker。

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PASS=0; FAIL=0
ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# docker-compose.yml 也要拷 —— 9/12 (020f172) 把镜像 tag 的唯一来源改成
# compose 之后, setup.sh 起手就要读它, 读不到直接 exit 1。
#
# ⚠ 这个测试从那天起就一直是红的, 到 9/22 才被发现 —— 因为**没有任何地方
#   在跑它**。一个写得挺好、记着真实 bug 的测试, 躺在仓库里死了十天。
#   已经挂进 CI (repo-checks job), 别再让它变成这样。
#
# tools/ 里是 certgen.py, REGEN_ENV_ONLY 路径用不到, 但拷上省得以后又漏。
cp setup.sh .env.example docker-compose.yml "$WORK/"
cp -R tools "$WORK/" 2>/dev/null || true

run() { (cd "$WORK" && SERVER_IP=10.0.0.1 REGEN_ENV_ONLY=1 bash setup.sh >/dev/null 2>&1); }
run_upgrade() { (cd "$WORK" && SERVER_IP=10.20.30.40 ENABLE_HTTPS=1 GATEWAY_WORKERS=1 UPGRADE=1 REGEN_ENV_ONLY=1 bash setup.sh >/dev/null 2>&1); }
val() { grep -E "^$1=" "$WORK/.env" | head -1 | cut -d= -f2-; }

echo "── 第一次装机 ──"
run || { echo "  ✗ setup.sh 首次就失败了"; exit 1; }
K1=$(val CATFISH_SECRET_KEY); P1=$(val PG_PASSWORD); J1=$(val JWT_SIGNING_KEY)
[ -n "$K1" ] && ok "CATFISH_SECRET_KEY 已生成" || bad "CATFISH_SECRET_KEY 是空的"
[ -n "$P1" ] && ok "PG_PASSWORD 已生成"        || bad "PG_PASSWORD 是空的"
[ -n "$J1" ] && ok "JWT_SIGNING_KEY 已生成"    || bad "JWT_SIGNING_KEY 是空的"
grep -q '<server-ip>' "$WORK/.env" && bad "占位符没被替换 (sed 没生效?)" \
                                   || ok "<server-ip> 已全部替换"
[ "$(val CATFISH_OIDC_ISSUER)" = "https://10.0.0.1" ] \
  && ok "新装 OIDC issuer 按 IP 生成" || bad "新装 OIDC issuer 错误"
[ "$(val CATFISH_IDENTITY_ISSUER)" = "https://10.0.0.1" ] \
  && ok "新装 Identity issuer 按 IP 生成" || bad "新装 Identity issuer 错误"
[ "$(val CATFISH_IDENTITY_CORS_ORIGINS)" = "https://10.0.0.1" ] \
  && ok "新装 CORS origin 按 Web URL 生成" || bad "新装 CORS origin 错误"
[ "$(val CATFISH_ENABLE_HTTPS)" = "1" ] \
  && ok "新装默认 HTTPS" || bad "新装 HTTPS 默认值错误"

echo "── 第二次装机 (同一目录重跑) ──"
run || { echo "  ✗ setup.sh 重跑失败"; exit 1; }
K2=$(val CATFISH_SECRET_KEY); P2=$(val PG_PASSWORD); J2=$(val JWT_SIGNING_KEY)
[ "$K2" = "$K1" ] && ok "CATFISH_SECRET_KEY 保持不变" \
  || bad "CATFISH_SECRET_KEY 被换了! 库里已存的 API key 全部解不开 ($K1 → $K2)"
[ "$P2" = "$P1" ] && ok "PG_PASSWORD 保持不变" || bad "PG_PASSWORD 被换了 ($P1 → $P2)"
[ "$J2" = "$J1" ] && ok "JWT_SIGNING_KEY 保持不变" || bad "JWT_SIGNING_KEY 被换了"

echo "── UPGRADE=1 (现场参数完整保留) ──"
# 模拟早期交付包: 现场 .env 没有这些有效字段, 升级必须自动补齐而不是回退 compose 默认值.
sed -i.bak \
  -e '/^CATFISH_OIDC_ISSUER=/d' \
  -e '/^CATFISH_IDENTITY_ISSUER=/d' \
  -e '/^CATFISH_IDENTITY_CORS_ORIGINS=/d' \
  -e '/^CATFISH_ENABLE_HTTPS=/d' \
  -e '/^CATFISH_HTTPS_PORT=/d' \
  -e '/^GATEWAY_WORKERS=/d' \
  "$WORK/.env"
sed -i.bak 's|^CATFISH_IDENTITY_URL=.*|CATFISH_IDENTITY_URL=http://10.20.30.40:8998|' "$WORK/.env"
sed -i.bak 's|^DASHSCOPE_API_KEY=.*|DASHSCOPE_API_KEY=keep-me|' "$WORK/.env"
run_upgrade || { echo "  ✗ UPGRADE=1 失败"; exit 1; }
[ "$(val CATFISH_IDENTITY_URL)" = "http://10.20.30.40:8998" ] \
  && ok "CATFISH_IDENTITY_URL 保持不变" \
  || bad "CATFISH_IDENTITY_URL 被覆盖"
[ "$(val DASHSCOPE_API_KEY)" = "keep-me" ] \
  && ok "现场 API 参数保持不变" \
  || bad "现场 API 参数被覆盖"
[ "$(val CATFISH_OIDC_ISSUER)" = "https://10.20.30.40" ] \
  && ok "升级 OIDC issuer 按新 IP/HTTPS 更新" || bad "升级 OIDC issuer 未更新"
[ "$(val CATFISH_IDENTITY_ISSUER)" = "https://10.20.30.40" ] \
  && ok "升级 Identity issuer 按新 IP/HTTPS 更新" || bad "升级 Identity issuer 未更新"
[ "$(val CATFISH_IDENTITY_CORS_ORIGINS)" = "https://10.20.30.40" ] \
  && ok "升级 CORS origin 按新 Web URL 更新" || bad "升级 CORS origin 未更新"
[ "$(val CATFISH_ENABLE_HTTPS)" = "1" ] \
  && ok "升级 HTTPS 模式更新" || bad "升级 HTTPS 模式未更新"
[ "$(val GATEWAY_WORKERS)" = "1" ] \
  && ok "升级 worker 数更新" || bad "升级 worker 数未更新"

echo "── .env 删了但 .env.bak.* 还在 (装机目录被清过) ──"
rm -f "$WORK/.env"
run || { echo "  ✗ setup.sh 在只剩备份时失败"; exit 1; }
[ "$(val CATFISH_SECRET_KEY)" = "$K1" ] && ok "从备份里捞回了 CATFISH_SECRET_KEY" \
  || bad "没从备份捞回 CATFISH_SECRET_KEY"
[ "$(val PG_PASSWORD)" = "$P1" ] && ok "从备份里捞回了 PG_PASSWORD" \
  || bad "没从备份捞回 PG_PASSWORD"

echo
echo "通过 $PASS · 失败 $FAIL"
[ "$FAIL" -eq 0 ]

# ─────────────────────────────────────────────────────────────────────
# 9/22: HTTP 模式。
#
# 加这一段是因为 9/22 重构时把 HTTP 模式的 issuer 和 CORS origin 写成了
# 同一个地址 (都是 http://IP), 而正确的是:
#
#     issuer = http://IP:8998   (identity 自己的端口)
#     cors   = http://IP:5173   (web 的端口)
#
# HTTPS 模式下两者本来就相同 (443 由 web 容器统一扛), 所以那条路完全看不
# 出问题 —— 而当时这个文件里的 20 条断言**全是 HTTPS**。回归一路绿灯,
# 到现场跑 HTTP 才会发现登录挂了, 且错误在浏览器里, 不在我们日志里。
#
# 教训不是"多写几条测试", 是: 一个有分支的行为, 测试只覆盖了其中一支时,
# 绿灯的含义比看上去小得多。
# ─────────────────────────────────────────────────────────────────────
echo "── HTTP 模式 (issuer 和 CORS 不是同一个地址) ──"
WORK2=$(mktemp -d)
trap 'rm -rf "$WORK" "$WORK2"' EXIT
cp setup.sh .env.example docker-compose.yml "$WORK2/"
cp -R tools "$WORK2/" 2>/dev/null || true
(cd "$WORK2" && SERVER_IP=10.1.2.3 ENABLE_HTTPS=0 REGEN_ENV_ONLY=1 bash setup.sh >/dev/null 2>&1) \
    || { echo "  ✗ HTTP 模式跑失败"; exit 1; }
v2() { grep -E "^$1=" "$WORK2/.env" | head -1 | cut -d= -f2-; }

[ "$(v2 CATFISH_OIDC_ISSUER)" = "http://10.1.2.3:8998" ] \
    && ok "HTTP · OIDC issuer 指向 identity 的 8998" \
    || bad "HTTP · OIDC issuer = $(v2 CATFISH_OIDC_ISSUER) (应为 http://10.1.2.3:8998)"
[ "$(v2 CATFISH_IDENTITY_ISSUER)" = "http://10.1.2.3:8998" ] \
    && ok "HTTP · Identity issuer 指向 8998" \
    || bad "HTTP · Identity issuer = $(v2 CATFISH_IDENTITY_ISSUER)"
[ "$(v2 CATFISH_IDENTITY_CORS_ORIGINS)" = "http://10.1.2.3:5173" ] \
    && ok "HTTP · CORS origin 指向 web 的 5173" \
    || bad "HTTP · CORS origin = $(v2 CATFISH_IDENTITY_CORS_ORIGINS) (应为 http://10.1.2.3:5173)"
[ "$(v2 CATFISH_OIDC_ISSUER)" != "$(v2 CATFISH_IDENTITY_CORS_ORIGINS)" ] \
    && ok "HTTP · issuer 和 CORS origin 确实不同 (正是 9/22 写错的那处)" \
    || bad "HTTP · issuer 和 CORS origin 相同了 —— 回到 9/22 那个 bug"
[ "$(v2 CATFISH_ENABLE_HTTPS)" = "0" ] && ok "HTTP · 模式字段写对" || bad "HTTP · 模式字段错"

# ─────────────────────────────────────────────────────────────────────
# compose 项目名 (9/23 现场: 包目录改名 → compose 当成新项目 → 5 个空卷)
#
# 项目名必须进 .env 且跟目录名脱钩; 升级时必须接上正在跑的那套。
# 这几条直接调 envgen.py (setup.sh 那层只是把容器标签读出来传进去)。
# ─────────────────────────────────────────────────────────────────────
echo "── compose 项目名 ──"
[ "$(val COMPOSE_PROJECT_NAME)" = "catfish" ] \
    && ok "新装 · COMPOSE_PROJECT_NAME=catfish (不是目录名 $(basename "$WORK"))" \
    || bad "新装 · COMPOSE_PROJECT_NAME = '$(val COMPOSE_PROJECT_NAME)' (应为 catfish)"

WORK3=$(mktemp -d)
trap 'rm -rf "$WORK" "$WORK2" "$WORK3"' EXIT
cp .env.example docker-compose.yml "$WORK3/"; cp -R tools "$WORK3/"
# 老包装的现场: .env 里没有 COMPOSE_PROJECT_NAME, 但容器在跑, 标签说项目叫 (老目录名) oldpkg-0715
cp "$WORK3/.env.example" "$WORK3/.env"
sed -i.bak -e 's/^PG_PASSWORD=.*/PG_PASSWORD=oldpw/' -e 's/^JWT_SIGNING_KEY=.*/JWT_SIGNING_KEY=oldjwt/' \
    -e 's/^CATFISH_SECRET_KEY=.*/CATFISH_SECRET_KEY=oldsec/' -e '/^COMPOSE_PROJECT_NAME=/d' "$WORK3/.env"
python3 "$WORK3/tools/envgen.py" --dir "$WORK3" --server-ip 10.0.0.9 --https 1 --upgrade 1 \
    --project-name oldpkg-0715 >/dev/null 2>&1 \
    || bad "升级 · envgen 在老包 + 现有容器的情况下失败了"
v3() { grep -E "^$1=" "$WORK3/.env" | head -1 | cut -d= -f2-; }
[ "$(v3 COMPOSE_PROJECT_NAME)" = "oldpkg-0715" ] \
    && ok "升级 · .env 没项目名时沿用正在跑的容器的 (接上老的卷)" \
    || bad "升级 · 项目名 = '$(v3 COMPOSE_PROJECT_NAME)' (应沿用 oldpkg-0715, 否则起在空库上)"
# 再跑一次, 这回容器标签和 .env 一致 → 照常
python3 "$WORK3/tools/envgen.py" --dir "$WORK3" --server-ip 10.0.0.9 --https 1 --upgrade 1 \
    --project-name oldpkg-0715 >/dev/null 2>&1 \
    && ok "升级 · 项目名一致时照常" || bad "升级 · 项目名一致却失败"
# .env 说 A, 正在跑的容器说 B → 必须停下, 不能猜
if python3 "$WORK3/tools/envgen.py" --dir "$WORK3" --server-ip 10.0.0.9 --https 1 --upgrade 1 \
    --project-name some-other-project >/dev/null 2>&1; then
    bad "升级 · .env 和容器的项目名不一致却放行了 —— 会起在一套空卷上"
else
    ok "升级 · .env 和容器的项目名不一致 → 拒绝"
    [ "$(v3 COMPOSE_PROJECT_NAME)" = "oldpkg-0715" ] \
        && ok "升级 · 拒绝时没有动 .env" || bad "升级 · 拒绝了却改了 .env"
fi

# ── setup.sh 开着 set -e + pipefail: 取值用的 $(grep …) 必须兜底 (10/21) ──
# grep 没匹配返 1 → 整个脚本在那一行静默退出, 不报错。10/14 加的 CATFISH_HTTP_PORT 那行就是这样:
# 新装 .env 里没这个字段, 脚本停在接入码之前, 退出码 1。上面几项都用 REGEN_ENV_ONLY=1, 跑不到那儿,
# 所以这里静态查: 每个 $(grep …) 要么带 || true / || echo, 要么在 if / && 判断里。
echo ""
echo "── setup.sh 的 \$(grep …) 都有兜底 ──"
UNGUARDED=$(grep -n '\$(grep' setup.sh | grep -v -E '\|\| *(true|echo)|^[0-9]+: *(if|elif|while) |&&' || true)
if [ -n "$UNGUARDED" ]; then
    bad "这些 \$(grep …) 没匹配时会让 setup.sh 静默退出 (加 || true):"
    echo "$UNGUARDED" | sed 's/^/        setup.sh:/'
else
    ok "没有会让 set -e 静默退出的 \$(grep …)"
fi

# 11/08 鸿波「每次升级都会因为 images 下面还有老的 gz 残留出错」: 原来按文件名挑第一个 = 最旧的包。
echo ""
echo "── 没传 IMAGE_TAR: images/ 里有旧包残留也挑对 ──"
eval "$(sed -n '/^# >>> pick_image_tar/,/^# <<< pick_image_tar/p' setup.sh)"
PK="$WORK/pick"; mkdir -p "$PK/images"
for d in 20261106 20261107 20261108; do touch "$PK/images/catfish-poc-central-amd64-$d.tar.gz" "$PK/images/catfish-poc-central-arm64-$d.tar.gz"; done
printf 'arch=amd64\ndate=20261107\nimage_tar=catfish-poc-central-amd64-20261107.tar.gz\n' > "$PK/BUILD-INFO.txt"
[ "$(basename "$(pick_image_tar "$PK")")" = "catfish-poc-central-amd64-20261107.tar.gz" ] \
    && ok "BUILD-INFO.txt 写了 image_tar: 就用它" || bad "没按 BUILD-INFO.txt 挑: $(pick_image_tar "$PK")"
printf 'arch=amd64\ndate=20261020\n' > "$PK/BUILD-INFO.txt"   # 老包: 没有 image_tar 这一行
case "$(uname -m)" in x86_64|amd64) want=amd64 ;; aarch64|arm64) want=arm64 ;; *) want="" ;; esac
got="$(basename "$(pick_image_tar "$PK")")"
if [ -n "$want" ]; then
    [ "$got" = "catfish-poc-central-$want-20261108.tar.gz" ] && ok "老包: 本机架构里日期最新的 ($got)" || bad "老包挑错了: $got"
fi
rm -f "$PK/BUILD-INFO.txt" "$PK/images/"*
[ -z "$(pick_image_tar "$PK")" ] && ok "一个包都没有: 返回空, 不让 set -e 把脚本带走" || bad "空目录没返回空"
(set -euo pipefail; x=$(pick_image_tar "$PK"); echo "still-alive$x") | grep -q still-alive \
    && ok "set -euo pipefail 下也不退出" || bad "set -e 下没包时脚本被带走"

# 11/08 80 → https 跳转: 单独的叠加文件、只在 HTTPS 模式下叠, 80 被别人占着不拦装机
echo ""
echo "── 80 → https 跳转 ──"
grep -qE '"\$\{CATFISH_HTTP_REDIRECT_PORT:-80\}:8080"' docker-compose.http-redirect.yml \
    && ok "叠加文件把宿主机 80 映射到容器的跳转口 8080" || bad "docker-compose.http-redirect.yml 映射不对"
grep -q 'CATFISH_HTTPS_PORT: "${CATFISH_HTTPS_PORT:-443}"' docker-compose.https.yml \
    && ok "web 容器拿得到 HTTPS 端口 (改过端口的跳转要带上)" || bad "https.yml 没把 CATFISH_HTTPS_PORT 传给 web"
! grep -q '8080' docker-compose.yml && ok "主 compose 不映射 80 (HTTP 模式 / 80 被占时不受影响)" || bad "主 compose 里出现了 8080"
SECTION=$(awk '/^COMPOSE_FILES=\(-f docker-compose.yml\)/{on=1} on{print} /docker compose "\$\{COMPOSE_FILES\[@\]\}" up/{exit}' setup.sh)
echo "$SECTION" | grep -q 'docker-compose.http-redirect.yml' && ok "只在 HTTPS 那段里叠跳转文件" || bad "跳转文件不在 HTTPS 那段里"
echo "$SECTION" | grep -A3 '被别的程序占着 · 不做' | grep -q 'exit 1' && bad "80 被占时把装机拦下了" || ok "80 被占: 只说一句, 不拦装机"

# ─────────────────────────────────────────────────────────────────────
# 11/08 IP + 域名 / 公网映射同时用 (PUBLIC_HOST / TLS_CERT / TLS_KEY)
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "── PUBLIC_HOST: IP + 域名 ──"
W4=$(mktemp -d)
trap 'rm -rf "$WORK" "$WORK2" "$WORK3" "$W4"' EXIT
cp setup.sh .env.example docker-compose.yml "$W4/"; cp -R tools "$W4/"
e4() { grep -E "^$1=" "$W4/.env" | head -1 | cut -d= -f2-; }
run4() { (cd "$W4" && REGEN_ENV_ONLY=1 SERVER_IP=192.168.31.199 "$@" bash setup.sh >"$W4/out" 2>&1); }

run4 || bad "IP-only 新装失败"
for k in CATFISH_PUBLIC_HOSTS CATFISH_IDENTITY_ISSUERS CATFISH_OIDC_ISSUERS; do
    grep -q "^$k=" "$W4/.env" && bad "IP-only 也写了 $k (应跟以前一模一样)" || ok "IP-only: 不写 $k"
done
[ "$(e4 CATFISH_IDENTITY_CORS_ORIGINS)" = "https://192.168.31.199" ] && ok "IP-only: CORS 还是只有 IP" || bad "IP-only CORS = $(e4 CATFISH_IDENTITY_CORS_ORIGINS)"

run4 env PUBLIC_HOST=Catfish.Example.com || bad "PUBLIC_HOST 装机失败: $(cat "$W4/out")"
[ "$(e4 CATFISH_OIDC_ISSUER)" = "https://192.168.31.199" ] && [ "$(e4 CATFISH_IDENTITY_ISSUER)" = "https://192.168.31.199" ] \
    && ok "主 issuer 还是内网 IP (renew-cert / 老镜像只认它)" || bad "主 issuer 被改了: $(e4 CATFISH_OIDC_ISSUER)"
[ "$(e4 CATFISH_IDENTITY_ISSUERS)" = "https://192.168.31.199,https://catfish.example.com" ] \
    && [ "$(e4 CATFISH_OIDC_ISSUERS)" = "$(e4 CATFISH_IDENTITY_ISSUERS)" ] \
    && ok "issuer 列表 = IP + 域名 (identity / 网关同一串)" || bad "issuer 列表 = $(e4 CATFISH_IDENTITY_ISSUERS) / $(e4 CATFISH_OIDC_ISSUERS)"
[ "$(e4 CATFISH_IDENTITY_CORS_ORIGINS)" = "https://192.168.31.199,https://catfish.example.com" ] \
    && ok "CORS = IP + 域名" || bad "CORS = $(e4 CATFISH_IDENTITY_CORS_ORIGINS)"
[ "$(e4 CATFISH_PUBLIC_HOSTS)" = "catfish.example.com" ] && ok "CATFISH_PUBLIC_HOSTS 存进 .env (小写)" || bad "CATFISH_PUBLIC_HOSTS = $(e4 CATFISH_PUBLIC_HOSTS)"

run4 || bad "重跑失败"
[ "$(e4 CATFISH_PUBLIC_HOSTS)" = "catfish.example.com" ] && [ "$(e4 CATFISH_OIDC_ISSUERS)" = "https://192.168.31.199,https://catfish.example.com" ] \
    && ok "重跑不写 PUBLIC_HOST: 沿用 .env 里的" || bad "重跑把域名丢了"
run4 env UPGRADE=1 || bad "UPGRADE=1 失败"
[ "$(e4 CATFISH_PUBLIC_HOSTS)" = "catfish.example.com" ] && ok "UPGRADE=1: 沿用 PUBLIC_HOST" || bad "UPGRADE=1 把域名丢了"

echo "── PUBLIC_HOST: 公网映射 (NAT) + 混合 ──"
run4 env PUBLIC_HOST=203.0.113.7:8443 || bad "NAT 装机失败: $(cat "$W4/out")"
[ "$(e4 CATFISH_OIDC_ISSUERS)" = "https://192.168.31.199,https://203.0.113.7:8443" ] \
    && ok "203.0.113.7:8443 → issuer https://203.0.113.7:8443 (对外端口)" || bad "NAT issuer = $(e4 CATFISH_OIDC_ISSUERS)"
run4 env PUBLIC_HOST='203.0.113.7:8443, catfish.example.com,https://m.example.com/' || bad "混合列表失败: $(cat "$W4/out")"
[ "$(e4 CATFISH_PUBLIC_HOSTS)" = "203.0.113.7:8443,catfish.example.com,m.example.com" ] \
    && ok "混合列表规范化 (去空格 / 去 https:// 和尾斜杠)" || bad "规范化 = $(e4 CATFISH_PUBLIC_HOSTS)"
[ "$(e4 CATFISH_IDENTITY_CORS_ORIGINS)" = "https://192.168.31.199,https://203.0.113.7:8443,https://catfish.example.com,https://m.example.com" ] \
    && ok "混合列表: CORS 全部来源" || bad "混合 CORS = $(e4 CATFISH_IDENTITY_CORS_ORIGINS)"
run4 env PUBLIC_HOST=catfish.example.com CATFISH_HTTPS_PORT=8443 || bad "8443 失败"
[ "$(e4 CATFISH_OIDC_ISSUERS)" = "https://192.168.31.199:8443,https://catfish.example.com:8443" ] \
    && ok "内部端口 8443、域名没写端口: 跟着 8443" || bad "8443 issuers = $(e4 CATFISH_OIDC_ISSUERS)"
sed -i.bak 's/^CATFISH_HTTPS_PORT=.*/CATFISH_HTTPS_PORT=443/' "$W4/.env"

cp "$W4/.env" "$W4/env.before"
for badhost in 'catfish.example.com/m' '*.example.com' 'x.com:99999' '127.0.0.1' 'bad_name.com' '[::1]:443'; do
    if run4 env PUBLIC_HOST="$badhost"; then bad "非法 PUBLIC_HOST '$badhost' 被放行了"
    else ok "非法 PUBLIC_HOST '$badhost' 拒绝: $(grep '❌' "$W4/out" | head -1 | cut -c1-60)…"; fi
done
cmp -s "$W4/.env" "$W4/env.before" && ok "拒绝时 .env 一个字没改" || bad "拒绝了却改了 .env"
if run4 env PUBLIC_HOST=catfish.example.com ENABLE_HTTPS=0; then bad "HTTP + 域名被放行了"
else grep -q '必须走 HTTPS' "$W4/out" && ok "域名必须 HTTPS: HTTP 模式拒绝并说清楚" || bad "HTTP + 域名的提示不对"; fi

run4 env PUBLIC_HOST=none || bad "PUBLIC_HOST=none 失败"
! grep -qE '^CATFISH_(PUBLIC_HOSTS|IDENTITY_ISSUERS|OIDC_ISSUERS)=' "$W4/.env" \
    && [ "$(e4 CATFISH_IDENTITY_CORS_ORIGINS)" = "https://192.168.31.199" ] \
    && ok "PUBLIC_HOST=none: 去掉对外地址, 回到只有 IP" || bad "PUBLIC_HOST=none 没清干净"

# 跳转目标: 用 .env 里的值跑一遍 web 容器的 41-catfish-ssl.sh (仓库里才有 central/web, 交付包里没有就跳过)
WEBDIR="../../central/web"
if [ -f "$WEBDIR/docker-entrypoint-catfish-ssl.sh" ]; then
    run4 env PUBLIC_HOST=203.0.113.7:8443,catfish.example.com
    N=$(mktemp -d); mkdir -p "$N/conf.d" "$N/certs"; echo c > "$N/certs/cert.pem"; echo k > "$N/certs/key.pem"
    for f in catfish-ssl.conf catfish-ssl-official.conf catfish-http-redirect.conf; do cp "$WEBDIR/$f" "$N/$f.disabled"; done
    CATFISH_NGINX_DIR="$N" CATFISH_ENABLE_HTTPS=1 CATFISH_HTTPS_PORT=443 CATFISH_PUBLIC_HOSTS="$(e4 CATFISH_PUBLIC_HOSTS)" \
        sh "$WEBDIR/docker-entrypoint-catfish-ssl.sh" >/dev/null 2>&1
    grep -A3 'server_name 203.0.113.7;' "$N/conf.d/catfish-http-redirect.conf" | grep -q 'return 301 https://203.0.113.7:8443$request_uri;' \
        && ok "跳转: http://203.0.113.7 → https://203.0.113.7:8443" || bad "NAT 跳转目标不对"
    grep -A3 'server_name catfish.example.com;' "$N/conf.d/catfish-http-redirect.conf" | grep -q 'return 301 https://catfish.example.com$request_uri;' \
        && ok "跳转: http://域名 → https://域名" || bad "域名跳转目标不对"
    rm -rf "$N"
fi

# ── 证书: certgen (SAN) / certcheck (正式证书) —— 要 cryptography ──
# 装机时它们跑在 identity 镜像里; 这里找一个有 cryptography 的 python: CERT_PY > python3 > 主仓 identity venv
CERT_PY="${CERT_PY:-}"
if [ -z "$CERT_PY" ]; then
    for c in python3 ../../central/identity-server/venv/bin/python ../../central/identity-server/.venv/bin/python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import cryptography' 2>/dev/null; then CERT_PY="$c"; break; fi
    done
fi
echo ""
if [ -z "$CERT_PY" ]; then
    echo "── 证书 (跳过: 没有带 cryptography 的 python; CERT_PY=<python> bash test_setup_env.sh) ──"
else
    echo "── 内部证书 SAN: IP + 域名 + 公网 IP ──"
    C=$(mktemp -d)
    "$CERT_PY" tools/certgen.py --ip 192.168.31.199 --out "$C/certs" --public-hosts '203.0.113.7:8443,catfish.example.com' >/dev/null 2>&1 \
        || bad "certgen 带 --public-hosts 失败"
    SAN=$("$CERT_PY" -c 'import sys; from cryptography import x509
c = x509.load_pem_x509_certificate(open(sys.argv[1], "rb").read())
s = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
print(" ".join(sorted("IP:" + str(v) for v in s.get_values_for_type(x509.IPAddress)) + sorted("DNS:" + v for v in s.get_values_for_type(x509.DNSName))))' "$C/certs/server.pem")
    [ "$SAN" = "IP:127.0.0.1 IP:192.168.31.199 IP:203.0.113.7 DNS:catfish.example.com DNS:localhost" ] \
        && ok "SAN: 公网 IP 进 IPAddress, 域名进 DNSName" || bad "SAN = $SAN"
    CA1=$(cat "$C/certs/pairing-code.txt")
    H1=$(cat "$C/certs/server.pem" | shasum | cut -c1-40)
    "$CERT_PY" tools/certgen.py --ip 192.168.31.199 --out "$C/certs" --public-hosts 'catfish.example.com' >/dev/null 2>&1
    [ "$(shasum < "$C/certs/server.pem" | cut -c1-40)" = "$H1" ] && ok "去掉一个地址: 不重签" || bad "去掉地址也重签了"
    "$CERT_PY" tools/certgen.py --ip 192.168.31.199 --out "$C/certs" --public-hosts 'catfish.example.com,new.example.com' > "$C/out" 2>&1
    grep -q 'SAN 里没有 new.example.com' "$C/out" && ok "新加域名 → 重签 (说清楚原因)" || bad "新域名没触发重签: $(cat "$C/out")"
    [ "$(cat "$C/certs/pairing-code.txt")" = "$CA1" ] && ok "重签不动 CA (接入码不变)" || bad "CA 变了!"
    grep -q '"internal"' "$C/certs/cert-info.json" && ok "cert-info.json 写了 (门户「服务状态」)" || bad "没写 cert-info.json"

    echo "── 正式证书 (certcheck) ──"
    # 造一套「公网 CA → 中间 CA → 服务器证书」, 外加各种坏的
    "$CERT_PY" - "$C" <<'PYGEN'
import datetime as dt, sys
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
out = Path(sys.argv[1]); now = dt.datetime.now(dt.timezone.utc)
def key(): return rsa.generate_private_key(public_exponent=65537, key_size=2048)
def name(cn): return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
def cert(subj, k, issuer, ik, *, ca=False, sans=(), start=-1, days=90):
    b = (x509.CertificateBuilder().subject_name(name(subj)).issuer_name(name(issuer)).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now + dt.timedelta(days=start))
         .not_valid_after(now + dt.timedelta(days=start + days))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if sans: b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
    return b.sign(ik, hashes.SHA256())
pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
kpem = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
rk, ik, lk = key(), key(), key()
root = cert("Test Root CA", rk, "Test Root CA", rk, ca=True, days=3650)
inter = cert("Test Intermediate", ik, "Test Root CA", rk, ca=True, days=3650)
good = cert("catfish.example.com", lk, "Test Intermediate", ik, sans=["catfish.example.com", "*.example.com"])
(out / "good.pem").write_bytes(pem(good) + pem(inter)); (out / "good.key").write_bytes(kpem(lk))
(out / "leaf-only.pem").write_bytes(pem(good))
(out / "other.key").write_bytes(kpem(key()))
(out / "expired.pem").write_bytes(pem(cert("catfish.example.com", lk, "Test Intermediate", ik, sans=["catfish.example.com"], start=-100, days=90)) + pem(inter))
(out / "soon.pem").write_bytes(pem(cert("catfish.example.com", lk, "Test Intermediate", ik, sans=["catfish.example.com"], start=-80, days=90)) + pem(inter))
(out / "other-domain.pem").write_bytes(pem(cert("x.other.com", lk, "Test Intermediate", ik, sans=["x.other.com"])) + pem(inter))
PYGEN
    cc() { "$CERT_PY" tools/certcheck.py "$@" > "$C/out" 2>&1; }
    O="$C/inst"; mkdir -p "$O"
    cc --cert "$C/good.pem" --key "$C/good.key" --public-hosts 'catfish.example.com,m.example.com,203.0.113.7:8443' --out "$O" \
        && cmp -s "$C/good.pem" "$O/official/fullchain.pem" && cmp -s "$C/good.key" "$O/official/privkey.pem" \
        && ok "合格的正式证书: 拷到 certs/official/ (通配 *.example.com 覆盖 m.example.com)" || bad "合格证书没装上: $(cat "$C/out")"
    [ "$(stat -f %Lp "$O/official/privkey.pem" 2>/dev/null || stat -c %a "$O/official/privkey.pem")" = 600 ] && ok "私钥 600" || bad "私钥权限不对"
    grep -q '203.0.113.7 是 IP' "$C/out" && ok "公网 IP 项: 说明它仍走内部证书" || bad "没说明 IP 走内部证书"
    rm -rf "$O"; mkdir -p "$O"
    rejected() {   # $1 = 说明, $2 = 期望出现的字, 其余 = certcheck 参数
        local what="$1" want="$2"; shift 2
        if cc "$@" --out "$O"; then bad "$what: 被放行了"
        elif [ -e "$O/official" ]; then bad "$what: 拒绝了却拷了文件"
        elif grep -q "$want" "$C/out"; then ok "$what → 拒绝, 什么都没拷"
        else bad "$what: 提示不对: $(cat "$C/out")"; fi
    }
    rejected "私钥不配对" "不是一对" --cert "$C/good.pem" --key "$C/other.key" --public-hosts catfish.example.com
    rejected "证书不含域名" "不包含域名 catfish.example.com" --cert "$C/other-domain.pem" --key "$C/good.key" --public-hosts catfish.example.com
    rejected "证书已过期" "过期" --cert "$C/expired.pem" --key "$C/good.key" --public-hosts catfish.example.com
    rejected "PUBLIC_HOST 只有 IP" "没有域名" --cert "$C/good.pem" --key "$C/good.key" --public-hosts 203.0.113.7:8443
    rejected "通配只管一层" "不包含域名 a.b.example.com" --cert "$C/good.pem" --key "$C/good.key" --public-hosts a.b.example.com
    rejected "证书当私钥传" "读不开" --cert "$C/good.pem" --key "$C/good.pem" --public-hosts catfish.example.com
    cc --cert "$C/leaf-only.pem" --key "$C/good.key" --public-hosts catfish.example.com && grep -q '没有中间证书' "$C/out" \
        && ok "只有服务器证书: 装, 但提醒要 fullchain" || bad "leaf-only: $(cat "$C/out")"
    cc --cert "$C/soon.pem" --key "$C/good.key" --public-hosts catfish.example.com && grep -q '不会自动续' "$C/out" \
        && ok "30 天内到期: 装, 但提醒不会自动续" || bad "快到期: $(cat "$C/out")"
    cc --cert "$C/expired.pem" --key "$C/good.key" --public-hosts catfish.example.com --existing && grep -q '过期' "$C/out" \
        && ok "升级核对已在用的: 过期只提醒, 不拦升级" || bad "--existing 过期: $(cat "$C/out")"
    if cc --cert "$C/good.pem" --key "$C/good.key" --public-hosts catfish.example.com,new.other.com --existing; then
        bad "升级换了证书没覆盖的域名却放行了"
    else ok "升级: 新域名不在已有正式证书里 → 停下说清楚"; fi

    echo "── 自动续期不碰正式证书 ──"
    mkdir -p "$C/certs/official"; cp "$C/good.pem" "$C/certs/official/fullchain.pem"; cp "$C/good.key" "$C/certs/official/privkey.pem"
    "$CERT_PY" tools/certgen.py --ip 192.168.31.199 --out "$C/certs" --public-hosts 'catfish.example.com,renew.example.com' >/dev/null 2>&1
    cmp -s "$C/good.pem" "$C/certs/official/fullchain.pem" && cmp -s "$C/good.key" "$C/certs/official/privkey.pem" \
        && ok "内部证书重签了, certs/official/ 原样" || bad "certgen 动了正式证书"
    grep -q '"official": {' "$C/certs/cert-info.json" && ok "cert-info.json 带上正式证书的到期日" || bad "cert-info 没有正式证书"
    grep -q 'certs/official' renew-cert.sh && ! grep -E 'official' renew-cert.sh | grep -vE '^\s*#' | grep -q . \
        && ok "renew-cert.sh 里除了注释不提 certs/official" || bad "renew-cert.sh 碰了 official"
    rm -rf "$C"
fi

echo "── setup.sh: 升级沿用正式证书 (静态) ──"
grep -q -- '--existing' setup.sh && grep -q 'certs/official/fullchain.pem' setup.sh \
    && ok "没给 TLS_CERT 时核对 certs/official/ 里已有的 (不重新拷、不删)" || bad "setup.sh 没处理已有正式证书"
! grep -nE 'rm .*certs/official' setup.sh && ok "setup.sh 不删 certs/official" || bad "setup.sh 会删正式证书"

echo ""
echo "通过 $PASS · 失败 $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
