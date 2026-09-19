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
# 6/9 鸿波修: 加 --build — 前次撞 pyproject.toml 改了 (python-multipart 漏依赖)
# 但 docker image cache 还是旧的, 重跑不生效. --build 强制按当前 Dockerfile +
# pyproject 重 build, 改源/依赖立刻可见. 已 build cache 命中还是快, 没成本.
# Set BENCH_NO_BUILD=1 跳过 build (调试 docker compose 自身用).
echo ""
echo "==> docker compose up..."
BUILD_FLAG="--build"
if [ "${BENCH_NO_BUILD:-0}" = "1" ]; then
    BUILD_FLAG=""
    echo "    BENCH_NO_BUILD=1, 跳过 build (用现 cached image)"
fi
docker compose -f bench/docker-compose-bench.yml up -d $BUILD_FLAG pg mock_upstream gateway

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

# 9/20: 加 --exit-code-on-error 0, 跟 bench-nightly.yml 保持**同一个行为**。
#
# 原来这里是 `locust ... || echo "(locust exit non-0, 继续 collect 数据)"`:
# 本机容忍, CI 不容忍。locust 默认"有任何一个请求失败就 exit 1", 而 300 用户
# 压 3 分钟出现千分之几失败是常态, 于是同一套东西在 Mac 上"正常"、在 runner 上
# 全红 —— 查了两轮环境差异, 差异其实在这两行脚本里。
#
# 一个 `|| echo` 把退出码整片吞掉, 代价是**真崩了也看不出来**。改成: 失败当量
# 交给 locust 的 flag 处理 (请求级失败不算), 剩下的非零退出如实记下并在末尾
# 复述一遍, 但不中断 —— 后面几步还要收 gateway 日志和 quota, 崩了才最需要它们。
LOCUST_EXIT=0
locust \
    -f bench/locustfile.py \
    --host "$GATEWAY_HOST" \
    --headless \
    --exit-code-on-error 0 \
    --users "$USERS" \
    --spawn-rate "$SPAWN_RATE" \
    --run-time "$RUN_TIME" \
    --html "$HTML" \
    --csv "$CSV_PREFIX" \
    || LOCUST_EXIT=$?
if [ "$LOCUST_EXIT" -ne 0 ]; then
    echo "    ⚠ locust 自身非零退出 (exit=$LOCUST_EXIT) —— 这不是「有请求失败」,"
    echo "      请求级失败已经由 --exit-code-on-error 0 排除了。继续收数据,"
    echo "      末尾会再提醒一次, gateway 日志在第 7 步。"
fi

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

if [ "$LOCUST_EXIT" -ne 0 ]; then
    echo ""
    echo "⚠⚠ 这次 locust 自身非零退出 (exit=$LOCUST_EXIT), 上面的数据**可能不完整**。"
    echo "    请求级失败不会走到这里 (--exit-code-on-error 0 已排除), 所以这是"
    echo "    locust 进程本身出了问题: 被 OOM kill (137)、脚本异常、端口连不上之类。"
    echo "    别拿这次的 csv 采基线 —— parse_results.py --emit-baseline 也会拦。"
fi
