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

echo ""
echo "通过 $PASS · 失败 $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
