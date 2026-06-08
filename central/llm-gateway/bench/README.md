# BL-F10 性能基准 (gateway 多 user 并发)

> 不烧 LLM token 测 catfish gateway 在 N 并发下的承载能力. mock upstream
> 替代真 Anthropic/Groq, 全网络栈跑通但 0 上游成本.

## 快速跑

```bash
cd central/llm-gateway

# 默认 100 用户 / 5min / uvicorn 1 worker
bash bench/run.sh

# 500 用户 / 10min / 4 worker (找 multi-worker scaling)
UVICORN_WORKERS=4 bash bench/run.sh 500 10m

# 50 用户 / 模拟 10% 上游 5xx (测 fallback 链在并发下)
MOCK_FAIL_RATE=0.1 bash bench/run.sh 50 5m
```

## 输出

每次跑生成 4 文件 (timestamped):

| 文件 | 内容 |
|---|---|
| `report-<N>-<ts>.html` | locust HTML — P50/P95/P99 / RPS / error rate 完整图 |
| `csv-<N>-<ts>_*.csv` | 详细 CSV — 二次 pandas 分析 |
| `docker-stats-<N>-<ts>.txt` | gateway 容器 CPU / mem 时序 |
| `pg-quota-<N>-<ts>.txt` | PG quota_events 聚合 — 检 race 用 |

## 验证 quota race (BL-F10 最重要检的真 bug)

`quota.py` grep `FOR UPDATE` 0 命中, 大概率有 race. bench 用 5 个固定虚拟员工
(bench-admin/alice/bob/carol/dave), 100 locust 用户 ramp = 每员工 ~20 并发 → 撕同
user 的 quota PG 写 race.

跑完看 `pg-quota-<N>-<ts>.txt`:

```
user_email                    department  events  tok_in  tok_out
bench-alice@catfish.bench    bench-eng      120   12000   24000
bench-bob@catfish.bench      bench-eng      118   11800   23600
bench-carol@catfish.bench    bench-sales    122   12200   24400
...
```

期望: locust 跑出的总 chat 数 ≈ events 总和. 如果**少了**, lost update / race
已撞 (gateway 收了 N 个 chat 但 PG 只记 M < N 次).

fix path: `quota.py` UPSERT 加 `INSERT ... ON CONFLICT DO UPDATE` 或 `SELECT ... FOR UPDATE`
row lock. 这是 BL-F10 暴露 + 修的真 bug.

## 工件

- `mock_upstream.py` — FastAPI OpenAI 兼容 streaming, 50 token × 30-80ms 平均
- `locustfile.py` — Locust task class, 模拟员工 (60% chat / 20% quota / 15% audit / 5% advisory)
- `models.bench.yaml` — 单 mock-bench model, 替 prod models.yaml
- `Dockerfile.mock` — mock upstream 镜像
- `docker-compose-bench.yml` — pg + mock_upstream + gateway + locust 一键起
- `run.sh` — 跑 + 收报告 + down

## 跟 manifesto 一致

mock 100% 本机, 不出公司. 跟 BL-F10 测的对象 (catfish gateway) 一样 — 内部网络栈, 不依赖外部 LLM 服务.

## 调延迟模拟其它 model

```bash
# 模拟 Anthropic sonnet (3-15s 总, P50 ≈ 5s)
MOCK_TOKENS_PER_RESPONSE=80 \
MOCK_LATENCY_PER_TOKEN_MS_MIN=40 \
MOCK_LATENCY_PER_TOKEN_MS_MAX=120 \
bash bench/run.sh 100

# 模拟 Groq (快, 总 0.5-2s)
MOCK_TOKENS_PER_RESPONSE=50 \
MOCK_LATENCY_PER_TOKEN_MS_MIN=10 \
MOCK_LATENCY_PER_TOKEN_MS_MAX=40 \
bash bench/run.sh 100
```

## 已知限制

- 不测 tool_call 流 (mock 不模拟 OpenAI tools)
- 不测 multimodal (mock 不模拟 vision)
- 不测真 LLM SLA (上游延迟可控 = 单变量, 想测真 LLM 跳变跑另一组真 model)
- 测的是单 gateway 实例 — 多实例共享 PG 测试是 **BL-D17**, 用另一个 docker-compose

## 跟 BL-D17 / BL-F4 关系

- F10 测**单实例上限** → 知道单 gateway 扛多少 → D17 才有依据 ("超过 X 用户要 N 实例")
- F4 跟 perf 无关 (员工换电脑迁移工具), 独立 ship 路径
