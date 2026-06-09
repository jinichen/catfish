#!/usr/bin/env bash
# BL-MCP-BENCH 一键跑 mcp-registry 性能 smoke (6/9 鸿波 B 方案, 30 min 投入).
#
# 用法:
#   bash bench/run.sh                          # 默认 50 user / 1min / 2 worker
#   bash bench/run.sh 200 2m                   # 加压
#   MCP_WORKERS=4 bash bench/run.sh 200 2m     # 4 worker scaling 对比
#
# 真验证目标:
#   - P99 < 100ms — manifests join subscriptions 没 N+1
#   - 0 fail — header 信任链工作 (X-Catfish-User-Sub)
#
# 输出:
#   bench/report-mcp-<users>-<ts>.html      — locust HTML
#   bench/csv-mcp-<users>-<ts>_*.csv         — 详细 CSV
#   bench/docker-stats-mcp-<users>-<ts>.txt  — CPU/mem 时序

set -euo pipefail

USERS="${1:-50}"
RUN_TIME="${2:-1m}"
SPAWN_RATE="${SPAWN_RATE:-10}"
TS="$(date +%Y%m%d-%H%M%S)"
MCP_HOST="${MCP_HOST:-http://localhost:8996}"

cd "$(dirname "$0")/.."  # cd 到 central/mcp-registry/

echo "==> BL-MCP-BENCH: $USERS users, $RUN_TIME, spawn $SPAWN_RATE/s"

# 0. 体检 locust
echo ""
echo "==> 检 locust 装没..."
if ! command -v locust >/dev/null 2>&1; then
    pip install --quiet -i https://pypi.tuna.tsinghua.edu.cn/simple 'locust>=2.31,<3.0'
fi
echo "    locust: $(locust --version 2>&1 | head -1)"

# 1. 起 stack
echo ""
echo "==> docker compose up (含 --build)..."
BUILD_FLAG="--build"
[ "${BENCH_NO_BUILD:-0}" = "1" ] && BUILD_FLAG=""
docker compose -f bench/docker-compose-mcp-bench.yml up -d $BUILD_FLAG

# 2. 等 mcp-registry 健康
echo ""
echo "==> 等 mcp-registry 健康 ($MCP_HOST/health)..."
for i in $(seq 1 60); do
    if curl -fs "$MCP_HOST/health" >/dev/null 2>&1; then
        echo "    mcp-registry up after ${i}s"
        break
    fi
    sleep 1
done

# 3. docker stats 后台采
STATS_FILE="bench/docker-stats-mcp-${USERS}-${TS}.txt"
(docker stats catfish-bench-mcp-registry --no-stream --format \
    "{{.Container}} CPU={{.CPUPerc}} MEM={{.MemUsage}}" \
    >> "$STATS_FILE" 2>&1; sleep 5) &
STATS_PID=$!

# 4. locust 跑
echo ""
echo "==> locust 跑 $USERS 用户 → $MCP_HOST..."
HTML="bench/report-mcp-${USERS}-${TS}.html"
CSV_PREFIX="bench/csv-mcp-${USERS}-${TS}"
set +e
locust -f bench/mcp-locustfile.py \
    --host "$MCP_HOST" \
    --headless \
    --users "$USERS" \
    --spawn-rate "$SPAWN_RATE" \
    --run-time "$RUN_TIME" \
    --html "$HTML" \
    --csv "$CSV_PREFIX"
LOCUST_EXIT=$?
set -e
[ $LOCUST_EXIT -ne 0 ] && echo "    (locust exit non-0, 继续 collect)"

kill $STATS_PID 2>/dev/null || true

# 5. 抓 log
LOG_FILE="bench/mcp-log-${USERS}-${TS}.txt"
docker compose -f bench/docker-compose-mcp-bench.yml logs mcp-registry --no-color \
    > "$LOG_FILE" 2>&1 || true

# 6. 关 stack
echo ""
echo "==> 关 stack..."
docker compose -f bench/docker-compose-mcp-bench.yml down -v

# 7. 汇报
echo ""
echo "==> 完成. 报告:"
echo "    $HTML"
echo "    ${CSV_PREFIX}_*.csv"
echo "    $STATS_FILE"
echo "    $LOG_FILE"
echo ""
echo "    P99 < 100ms = baseline 健康. 真 prod 5000 sub join 后会涨, 但不会跳秒级"
