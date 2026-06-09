#!/usr/bin/env bash
# BL-BROKER-BENCH 一键跑 secret-broker 性能 smoke (6/9 鸿波 B 方案, 30 min 投入).
#
# 用法:
#   bash bench/run.sh                          # 默认 50 user / 1min
#   bash bench/run.sh 200 2m                   # 加压
#
# 真验证目标:
#   - GET P99 < 50ms (PG SELECT + AES-GCM 解密)
#   - 0 fail (X-Catfish-User-Sub 信任链 + master_key 加密 roundtrip 工作)
#   - threading.Lock 多 worker 不卡

set -euo pipefail

USERS="${1:-50}"
RUN_TIME="${2:-1m}"
SPAWN_RATE="${SPAWN_RATE:-10}"
TS="$(date +%Y%m%d-%H%M%S)"
BROKER_HOST="${BROKER_HOST:-http://localhost:8995}"

cd "$(dirname "$0")/.."

echo "==> BL-BROKER-BENCH: $USERS users, $RUN_TIME, spawn $SPAWN_RATE/s"

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
docker compose -f bench/docker-compose-broker-bench.yml up -d $BUILD_FLAG

# 2. 等 broker 健康
echo ""
echo "==> 等 secret-broker 健康 ($BROKER_HOST/health)..."
for i in $(seq 1 60); do
    if curl -fs "$BROKER_HOST/health" >/dev/null 2>&1; then
        echo "    secret-broker up after ${i}s"
        break
    fi
    sleep 1
done

# 3. docker stats 后台采
STATS_FILE="bench/docker-stats-broker-${USERS}-${TS}.txt"
(docker stats catfish-bench-secret-broker --no-stream --format \
    "{{.Container}} CPU={{.CPUPerc}} MEM={{.MemUsage}}" \
    >> "$STATS_FILE" 2>&1; sleep 5) &
STATS_PID=$!

# 4. locust 跑
echo ""
echo "==> locust 跑 $USERS 用户 → $BROKER_HOST..."
HTML="bench/report-broker-${USERS}-${TS}.html"
CSV_PREFIX="bench/csv-broker-${USERS}-${TS}"
set +e
locust -f bench/broker-locustfile.py \
    --host "$BROKER_HOST" \
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

# 5. 拉 log
LOG_FILE="bench/broker-log-${USERS}-${TS}.txt"
docker compose -f bench/docker-compose-broker-bench.yml logs secret-broker --no-color \
    > "$LOG_FILE" 2>&1 || true

# 6. 关 stack
echo ""
echo "==> 关 stack..."
docker compose -f bench/docker-compose-broker-bench.yml down -v

# 7. 汇报
echo ""
echo "==> 完成. 报告:"
echo "    $HTML"
echo "    ${CSV_PREFIX}_*.csv"
echo "    $STATS_FILE"
echo "    $LOG_FILE"
echo ""
echo "    重点: GET /v1/secret/{ref} P99 < 50ms = PgEncryptedStorage 健康"
echo "          fail rate 0% = master_key + AES-GCM roundtrip 工作"
