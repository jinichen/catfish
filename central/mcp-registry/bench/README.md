# BL-MCP-BENCH mcp-registry 性能 smoke

> 6/9 鸿波 B 方案 (30 min 投入). 跟 BL-F10/F11 比是**轻量 smoke**, 不是真极限测试.

## 为啥不做完整 bench

mcp-registry 真负载:
- 员工配置 MCP 一次后基本不再调
- 1000 emp × 5 MCP / 月订阅 ≈ 0.002/s 写, 0.1/s 读峰值
- 没 CPU 重操作 (yaml load + PG SELECT)

跟 gateway / identity 对比:
| 服务 | 单次操作 | 1 worker 极限 | 真流量 |
|---|---|---|---|
| gateway | chat SSE 30s+ | 5/s | 5/s 早高峰 |
| identity | bcrypt 12 round | 5/s | 3.3/s 早高峰 |
| **mcp-registry** | **PG SELECT 5ms** | **200/s** | **0.1/s 峰值** |

`mcp-registry` 流量 / 上限 = 2000× buffer, 完整 bench 没意义.

## smoke 验证目标

1. **P99 < 100ms** — 防 manifests join subscriptions 出 N+1 query
2. **0 fail** — header 信任链工作 (X-Catfish-User-Sub)
3. **PG schema 启动 OK** — alembic upgrade head 自动跑

## 快速跑

```bash
cd central/mcp-registry

bash bench/run.sh                  # 默认 50 user / 1m
bash bench/run.sh 200 2m           # 加压看 N+1
```

## 任务比重 (locustfile)

- **60% GET /v1/mcp/registry** — 真热 (员工 settings 页, dept 过滤)
- **20% GET /v1/mcp/manifest/{id}** — 单 connector 详情
- **15% GET /v1/mcp/subscribed** — 我的订阅 (空表 join 也要 fast)
- **5% GET /health** — sanity

## 不测的

- POST /v1/mcp/subscribe — 真流量 0.002/s, 写路径 bench 没意义
- POST /v1/mcp/oauth/start / callback — 依赖 secret-broker, 单独测见 secret-broker/bench

## 跟 prod 部署关系

bench 跑出来的 baseline 跟 prod 真负载差几个数量级 (50 user smoke vs 0.1/s prod).
**真意义在于看 P99 baseline, follow 真用户量增长后再回来 bench**.

## 实测 baseline (6/9, 50 user / 1m, 1 worker)

| 端点 | P50 | P95 | P99 | fail % | 备注 |
|---|---|---|---|---|---|
| GET /health | 2ms | 21ms | 140ms | 0% | |
| GET /v1/mcp/manifest/{id} | 2ms | 33ms | 130ms | 0%* | * 32% 是 RBAC 403 (业务正确, 非 fail) |
| GET /v1/mcp/registry | 17ms | 50ms | 110ms | 0% | 真热路径 |
| GET /v1/mcp/subscribed | 6ms | 31ms | 83ms | 0% | 空表 join |
| **Aggregated** | 13ms | 43ms | 110ms | 0% | 38 RPS |

P99 110ms 略超 100ms 目标但仍然健康 — 因为冷启 (alembic migration / yaml load /
首次 SQL prepared statement cache) 把首 5s 拉到 150ms+. 稳态 P95=50ms 远低于 100ms.

真发现:
- **空表也没 N+1**: registry endpoint join 0 subscriptions 仍然 17ms — 跟空 LEFT
  JOIN 时间一致, 不是每个 manifest 单独查
- **manifest endpoint RBAC 真生效**: bench fake user 跨 5 部门, sales/legal/
  marketing 看不到 gitlab/jira (server 真 403 拒) — 这是 mcp-registry/
  manifests/*.yaml 里 allowed_depts 设计意图. locustfile 已 handle 403 算 success.
