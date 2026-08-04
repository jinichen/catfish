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
cp setup.sh .env.example "$WORK/"

run() { (cd "$WORK" && SERVER_IP=10.0.0.1 REGEN_ENV_ONLY=1 bash setup.sh >/dev/null 2>&1); }
val() { grep -E "^$1=" "$WORK/.env" | head -1 | cut -d= -f2-; }

echo "── 第一次装机 ──"
run || { echo "  ✗ setup.sh 首次就失败了"; exit 1; }
K1=$(val CATFISH_SECRET_KEY); P1=$(val PG_PASSWORD); J1=$(val JWT_SIGNING_KEY)
[ -n "$K1" ] && ok "CATFISH_SECRET_KEY 已生成" || bad "CATFISH_SECRET_KEY 是空的"
[ -n "$P1" ] && ok "PG_PASSWORD 已生成"        || bad "PG_PASSWORD 是空的"
[ -n "$J1" ] && ok "JWT_SIGNING_KEY 已生成"    || bad "JWT_SIGNING_KEY 是空的"
grep -q '<server-ip>' "$WORK/.env" && bad "占位符没被替换 (sed 没生效?)" \
                                   || ok "<server-ip> 已全部替换"

echo "── 第二次装机 (同一目录重跑) ──"
run || { echo "  ✗ setup.sh 重跑失败"; exit 1; }
K2=$(val CATFISH_SECRET_KEY); P2=$(val PG_PASSWORD); J2=$(val JWT_SIGNING_KEY)
[ "$K2" = "$K1" ] && ok "CATFISH_SECRET_KEY 保持不变" \
  || bad "CATFISH_SECRET_KEY 被换了! 库里已存的 API key 全部解不开 ($K1 → $K2)"
[ "$P2" = "$P1" ] && ok "PG_PASSWORD 保持不变" || bad "PG_PASSWORD 被换了 ($P1 → $P2)"
[ "$J2" = "$J1" ] && ok "JWT_SIGNING_KEY 保持不变" || bad "JWT_SIGNING_KEY 被换了"

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
