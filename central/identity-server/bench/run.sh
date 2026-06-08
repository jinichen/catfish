#!/usr/bin/env bash
# BL-F11 一键跑 identity-server 性能 benchmark.
#
# 用法:
#   bash bench/run.sh                          # 默认 1000 user / 5min / 2 worker
#   bash bench/run.sh 500 5m                    # 500 user / 5min
#   IDENTITY_WORKERS=4 bash bench/run.sh 1000 5m   # 4 worker 重跑对比
#
# 输出:
#   bench/report-identity-<users>-<ts>.html      — locust HTML 报告
#   bench/csv-identity-<users>-<ts>_*.csv         — 详细 CSV
#   bench/docker-stats-identity-<users>-<ts>.txt  — identity 容器 CPU/mem 时序
#   bench/identity-log-<users>-<ts>.txt           — identity log dump (debug 用)

set -euo pipefail

USERS="${1:-1000}"
RUN_TIME="${2:-5m}"
SPAWN_RATE="${SPAWN_RATE:-10}"
TS="$(date +%Y%m%d-%H%M%S)"
IDENTITY_HOST="${IDENTITY_HOST:-http://localhost:8998}"

cd "$(dirname "$0")/.."  # cd 到 central/identity-server/

echo "==> BL-F11 identity bench: $USERS users, $RUN_TIME, spawn $SPAWN_RATE/s"
echo "    IDENTITY_WORKERS=${IDENTITY_WORKERS:-2}"

# 0. 体检 locust (跟 BL-F10 gateway 一样, 跑 host 不进 docker)
echo ""
echo "==> 检 locust 装没..."
if ! command -v locust >/dev/null 2>&1; then
    echo "    locust 没装, pip install (清华 pypi)..."
    pip install --quiet -i https://pypi.tuna.tsinghua.edu.cn/simple 'locust>=2.31,<3.0'
fi
echo "    locust: $(locust --version 2>&1 | head -1)"

# 1. 起 stack — --build 防 pyproject/代码改了 image cache 不更新 (BL-F10 撞过)
echo ""
echo "==> docker compose up (含 --build, 防 image stale)..."
BUILD_FLAG="--build"
if [ "${BENCH_NO_BUILD:-0}" = "1" ]; then
    BUILD_FLAG=""
    echo "    BENCH_NO_BUILD=1, 跳过 build"
fi
docker compose -f bench/docker-compose-identity-bench.yml up -d $BUILD_FLAG pg identity

# 2. 等 identity healthcheck
echo ""
echo "==> 等 identity 健康 ($IDENTITY_HOST/healthz)..."
for i in $(seq 1 60); do
    if curl -fs "$IDENTITY_HOST/healthz" >/dev/null 2>&1; then
        echo "    identity up after ${i}s"
        break
    fi
    sleep 1
done

# 3. docker stats 后台采 CPU / mem
echo ""
echo "==> 起 docker stats 后台采..."
STATS_FILE="bench/docker-stats-identity-${USERS}-${TS}.txt"
(
    docker stats catfish-bench-identity --no-trunc --format \
        'name={{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} netio={{.NetIO}}' \
        > "$STATS_FILE" &
    echo $! > bench/.stats.pid
) || true

# 4. locust headless 跑
echo ""
echo "==> locust host 跑 $USERS 用户 → $IDENTITY_HOST..."
HTML="bench/report-identity-${USERS}-${TS}.html"
CSV_PREFIX="bench/csv-identity-${USERS}-${TS}"

locust \
    -f bench/identity-locustfile.py \
    --host "$IDENTITY_HOST" \
    --headless \
    --users "$USERS" \
    --spawn-rate "$SPAWN_RATE" \
    --run-time "$RUN_TIME" \
    --html "$HTML" \
    --csv "$CSV_PREFIX" \
    || echo "    (locust exit non-0, 继续 collect)"

# 5. 停 stats
if [ -f bench/.stats.pid ]; then
    kill "$(cat bench/.stats.pid)" 2>/dev/null || true
    rm bench/.stats.pid
fi

# 6. 验 PG 状态 (identity 用 PG 存 audit / refresh tokens, 验是不是 race)
echo ""
echo "==> 验 identity PG state (users_audit / refresh_tokens 表大小)..."
docker compose -f bench/docker-compose-identity-bench.yml exec -T pg \
    psql -U catfish -d catfish_bench -c "
        SELECT 'users_audit' AS tbl, COUNT(*) AS rows FROM users_audit
        UNION ALL
        SELECT 'users', COUNT(*) FROM users
        UNION ALL
        SELECT 'registry_agents', COUNT(*) FROM registry_agents;
    " > "bench/pg-state-identity-${USERS}-${TS}.txt" 2>&1 || echo "    (查询失败, skip)"

# 7. 拉 identity log (debug 用)
echo ""
echo "==> 抓 identity log..."
IDENTITY_LOG="bench/identity-log-${USERS}-${TS}.txt"
docker compose -f bench/docker-compose-identity-bench.yml logs identity --no-color \
    > "$IDENTITY_LOG" 2>&1 || echo "    (拉 log 失败, skip)"
echo "    log: $IDENTITY_LOG"

# 8. 关 stack
echo ""
echo "==> 关 stack..."
docker compose -f bench/docker-compose-identity-bench.yml down -v

# 9. 汇报
echo ""
echo "==> 完成. 报告:"
echo "    $HTML"
echo "    ${CSV_PREFIX}_*.csv"
echo "    $STATS_FILE"
echo "    bench/pg-state-identity-${USERS}-${TS}.txt"
echo "    $IDENTITY_LOG"
echo ""
echo "    打开 HTML 看 P50/P95/P99 + RPS + error rate"
echo "    重点: POST /token 路径 (bcrypt + JWT sign) 是不是真瓶颈"
echo ""
echo "    多 worker 对比: IDENTITY_WORKERS=4 bash bench/run.sh $USERS $RUN_TIME"
