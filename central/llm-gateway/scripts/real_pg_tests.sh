#!/usr/bin/env bash
# 在一个临时 PG 上跑「真库」测试 (8/1).
#
# ## 为什么需要这个
#
# tests/ 里绝大多数碰库的用例把 model_store / provider_store mock 成内存字典
# —— CATFISH_DB_URL 是空的。那样能验业务判断, 但有一整类 bug 它**结构上就
# 看不见**: 迁移 SQL 到底把什么写进了 JSONB、pydantic 校验 + model_dump +
# JSONB round-trip 之后哪些字段还在。
#
# 8/1 漏掉过两个, 都是零报错的:
#   · gemini-flash 的 timeout 被拆分从 60 静默抬到 180
#   · 界面存一次模型就丢掉 upstream.provider, 而 api_key_env 落回
#     INTERNAL_LLM_KEY —— 内网集群的凭据会被发到公网端点
#
# CI 里这件事由 .github/workflows/ci.yml 的 gateway-real-pg 做 (postgres
# 服务容器)。这个脚本是给本机用的。
#
# ## 用法
#
#   # 已经有库
#   CATFISH_TEST_PG_URL=postgresql://... scripts/real_pg_tests.sh
#
#   # 没有库: 用 docker 起一个一次性的
#   scripts/real_pg_tests.sh --docker
#
# ⚠ 会清空目标库的 gateway_models / gateway_providers。别指着生产库跑。

set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER=catfish-test-pg
CLEANUP=""

if [ "${1:-}" = "--docker" ]; then
  command -v docker >/dev/null || { echo "没装 docker。要么装, 要么自己给 CATFISH_TEST_PG_URL"; exit 1; }
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" \
    -e POSTGRES_USER=catfish -e POSTGRES_PASSWORD=catfish -e POSTGRES_DB=catfish \
    -p 55432:5432 postgres:16-alpine >/dev/null
  CLEANUP="docker rm -f $CONTAINER"
  # shellcheck disable=SC2064
  trap "$CLEANUP >/dev/null 2>&1 || true" EXIT
  export CATFISH_TEST_PG_URL="postgresql://catfish:catfish@localhost:55432/catfish"

  echo "等 PG 起来…"
  for _ in $(seq 1 30); do
    docker exec "$CONTAINER" pg_isready -U catfish >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$CONTAINER" pg_isready -U catfish >/dev/null 2>&1 \
    || { echo "PG 30 秒还没起来"; exit 1; }
fi

: "${CATFISH_TEST_PG_URL:?没设 CATFISH_TEST_PG_URL。要么给一个库, 要么加 --docker}"

echo "== alembic upgrade head =="
CATFISH_DB_URL="$CATFISH_TEST_PG_URL" alembic upgrade head

echo
echo "== 真库测试 =="
# ⚠ 不设 CATFISH_TEST_PG_URL 时这个文件会整体 skip, 而 skip 在 pytest 里是绿的。
# 所以下面额外确认它**真的跑了** —— 否则连不上库时这个脚本会安静地成功。
out=$(PYTHONPATH=src python -m pytest tests/test_provider_split_real_pg.py tests/test_room_link_mailbox_real_pg.py -v -p no:cacheprovider 2>&1) || {
  echo "$out"; exit 1;
}
echo "$out"
grep -qE "[0-9]+ passed" <<<"$out" || {
  echo
  echo "❌ 一条都没跑起来 (全 skip?) —— 检查 CATFISH_TEST_PG_URL 能不能连"
  exit 1
}
