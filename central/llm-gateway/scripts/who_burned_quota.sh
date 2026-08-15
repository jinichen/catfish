#!/usr/bin/env bash
# 配额烧在谁身上 —— 按 来源/用户/模型 聚合 gateway_audit。
#
# 为什么要这个脚本: 计量早就不写 ~/.catfish/gateway_audit.jsonl 了
# (metrics.py _use_pg(): 配了 CATFISH_DB_URL 就走 PG, 那个文件停在 5/9),
# 所以"谁烧的配额"只能查库, 没有现成入口。
#
# 用法:  bash scripts/who_burned_quota.sh [起始时间]
#        bash scripts/who_burned_quota.sh "2026-08-15 07:54"   # 本周期重置时刻
set -euo pipefail
cd "$(dirname "$0")/.."
DB=$(grep -m1 '^CATFISH_DB_URL=' .env | cut -d= -f2-)
SINCE="${1:-$(date -v-24H '+%Y-%m-%d %H:%M' 2>/dev/null || date -d '24 hours ago' '+%Y-%m-%d %H:%M')}"
MS=$(python3 -c "import datetime,sys;print(int(datetime.datetime.strptime(sys.argv[1],'%Y-%m-%d %H:%M').timestamp()*1000))" "$SINCE")

echo "══ 统计区间: $SINCE 起"
psql "$DB" <<SQL
\pset border 2
SELECT
  COALESCE(extra->>'source','(无标记)')            AS 来源,
  model                                             AS 模型,
  count(*)                                          AS 次数,
  sum(tokens_in)                                    AS 输入token,
  sum(tokens_out)                                   AS 输出token,
  round(avg(tokens_in))                             AS 每次平均输入,
  sum(tokens_total)                                 AS 合计
FROM gateway_audit
WHERE ts_ms >= $MS
GROUP BY 1,2
ORDER BY 合计 DESC
LIMIT 25;

SELECT user_email AS 用户, count(*) AS 次数, sum(tokens_total) AS 合计token
FROM gateway_audit WHERE ts_ms >= $MS
GROUP BY 1 ORDER BY 3 DESC LIMIT 10;

SELECT to_char(to_timestamp(ts_ms/1000),'MM-DD HH24:MI') AS 分钟,
       count(*) AS 次数, sum(tokens_total) AS token
FROM gateway_audit WHERE ts_ms >= $MS AND model LIKE '%qwen%'
GROUP BY 1 ORDER BY 3 DESC LIMIT 15;
SQL
