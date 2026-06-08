#!/usr/bin/env bash
# BL-F10 一键跑性能 benchmark.
#
# 用法:
#   bash bench/run.sh                # 默认 100 用户 / 5min
#   bash bench/run.sh 50              # 50 用户 / 5min
#   bash bench/run.sh 500 10m         # 500 用户 / 10min
#   UVICORN_WORKERS=4 bash bench/run.sh 500   # gateway 多 worker 重跑
#
# 输出:
#   bench/report-<users>-<timestamp>.html  — locust HTML 报告 (P50/95/99 / RPS / err)
#   bench/csv-<users>-<timestamp>_*.csv    — 详细数据 (能用 pandas 二次分析)
#   bench/docker-stats-<users>-<timestamp>.json — gateway 容器内存/CPU

set -euo pipefail

USERS="${1:-100}"
RUN_TIME="${2:-5m}"
SPAWN_RATE="${SPAWN_RATE:-10}"
TS="$(date +%Y%m%d-%H%M%S)"
GATEWAY_HOST="${GATEWAY_HOST:-http://localhost:8999}"

cd "$(dirname "$0")/.."  # cd 到 central/llm-gateway/

echo "==> BL-F10 bench: $USERS users, $RUN_TIME, spawn $SPAWN_RATE/s"
echo "    UVICORN_WORKERS=${UVICORN_WORKERS:-1}, MOCK_FAIL_RATE=${MOCK_FAIL_RATE:-0.0}"

# 0. host venv 装 locust (没装就 pip install). 跑 host 不进 docker — daocloud
#    国内镜像不代理 locustio/* namespace 撞 403.
echo ""
echo "==> 检 locust 装没..."
if ! command -v locust >/dev/null 2>&1; then
    echo "    locust 没装, pip install (走清华 pypi 加速)..."
    pip install --quiet -i https://pypi.tuna.tsinghua.edu.cn/simple 'locust>=2.31,<3.0'
fi
LOCUST_VERSION="$(locust --version 2>&1 | head -1)"
echo "    locust: $LOCUST_VERSION"

# 1. 启 stack (不含 locust)
echo ""
echo "==> docker compose up..."
docker compose -f bench/docker-compose-bench.yml up -d pg mock_upstream gateway

# 2. 等 gateway healthcheck (host 上 curl, docker ports 暴露 :8999)
echo ""
echo "==> 等 gateway 健康 ($GATEWAY_HOST/healthz)..."
for i in $(seq 1 60); do
    if curl -fs "$GATEWAY_HOST/healthz" >/dev/null 2>&1; then
        echo "    gateway up after ${i}s"
        break
    fi
    sleep 1
done

# 3. docker stats 后台采 (memory / CPU)
# 6/8 鸿波修: 之前用 `{{.ts}}` 不存在的 template 字段 →
# `can't evaluate field ts in type *container.statsContext`. docker stats 真字段:
# .Name / .CPUPerc / .MemUsage / .NetIO / .BlockIO. 改用合法 format.
echo ""
echo "==> 起 docker stats 后台采..."
STATS_FILE="bench/docker-stats-${USERS}-${TS}.txt"
(
    docker stats catfish-bench-gateway --no-trunc --format \
        'name={{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} netio={{.NetIO}}' \
        > "$STATS_FILE" &
    echo $! > bench/.stats.pid
) || true

# 4. locust headless 跑 (host 上, 通过 ports 暴露的 localhost:8999 打 gateway)
echo ""
echo "==> locust host 跑 $USERS 用户 → $GATEWAY_HOST..."
HTML="bench/report-${USERS}-${TS}.html"
CSV_PREFIX="bench/csv-${USERS}-${TS}"

locust \
    -f bench/locustfile.py \
    --host "$GATEWAY_HOST" \
    --headless \
    --users "$USERS" \
    --spawn-rate "$SPAWN_RATE" \
    --run-time "$RUN_TIME" \
    --html "$HTML" \
    --csv "$CSV_PREFIX" \
    || echo "    (locust exit non-0, 继续 collect 数据)"

# 5. 停 stats 采样
if [ -f bench/.stats.pid ]; then
    kill "$(cat bench/.stats.pid)" 2>/dev/null || true
    rm bench/.stats.pid
fi

# 6. 验 quota 一致性 (找 race condition)
# quota_events 真 schema (alembic/versions/20260502_001_initial_quota_audit.py):
#   ts_ms, user_email, department, model, tokens_in, tokens_out
echo ""
echo "==> 验 quota 是否 race (按 user / dept 聚 quota_events 看 token 总和)..."
docker compose -f bench/docker-compose-bench.yml exec -T pg \
    psql -U catfish -d catfish_bench -c "
        SELECT user_email,
               department,
               COUNT(*) AS events,
               SUM(tokens_in) AS tok_in,
               SUM(tokens_out) AS tok_out
        FROM quota_events
        GROUP BY user_email, department
        ORDER BY events DESC;
    " > "bench/pg-quota-${USERS}-${TS}.txt" 2>&1 || echo "    (quota_events 表不存在或 PG 没起, skip)"

# 7. 拉 gateway log (debug 401 / 鉴权问题用 — 6/8 鸿波加)
# locust 看到 100% 401 时, BL-DEBUG-401 logger.warning 会输 token preview +
# dev_token provider 加载状态. 这里 dump 后再关 stack, 没法 exec 上去看.
echo ""
echo "==> 抓 gateway log (debug 401 / 鉴权问题用)..."
GATEWAY_LOG="bench/gateway-log-${USERS}-${TS}.txt"
docker compose -f bench/docker-compose-bench.yml logs gateway --no-color \
    > "$GATEWAY_LOG" 2>&1 || echo "    (拉 log 失败, skip)"
echo "    log: $GATEWAY_LOG"

# 8. 关 stack
echo ""
echo "==> 关 stack..."
docker compose -f bench/docker-compose-bench.yml down -v

# 9. 输出汇总
echo ""
echo "==> 完成. 报告:"
echo "    $HTML"
echo "    ${CSV_PREFIX}_*.csv"
echo "    $STATS_FILE"
echo "    bench/pg-quota-${USERS}-${TS}.txt"
echo "    $GATEWAY_LOG  (debug 401 看这)"
echo ""
echo "    打开 HTML 看 P50/P95/P99 + RPS + error rate"
echo "    pg-quota 看 user 数 / token 总和, 比对 locust 期望"
echo ""
echo "    遇 100% 401: grep 'BL-DEBUG-401\\|auth:' $GATEWAY_LOG | head -20"
