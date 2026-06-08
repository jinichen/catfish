# BL-F11 identity-server 性能基准 (跟 BL-F10 gateway 同模板)

> 验 identity-server 在 1000 并发下 POST /token client_credentials grant 撑不撑得住.

## 6/9 BL-F11.P2 ship: identity 真支持 multi-worker

老版 uvicorn factory=True + workers>1 在 macOS docker 跑 multiprocess 撞 port bind
竞态, hard-code workers=1, P99 = 189s @ 1000 user.

下午 ship: `_CodeStore` 从 in-memory 移到 sqlite (跨 worker 共享 authorization_code
state) + `create_app` 拆 dependency 注入 + module-level lazy `app` 单例, uvicorn
走 import string `catfish_identity.app:app` 不走 factory, multi-worker 真稳.

默认 2 worker (跟 prod 默认对齐). `IDENTITY_WORKERS=4 bash bench/run.sh` 看 4
worker scaling.

## 快速跑

```bash
cd central/identity-server

# 默认 1000 user / 5min / 2 worker (跟 prod 默认对齐)
bash bench/run.sh

# 4 worker 对比 (重负载机房)
IDENTITY_WORKERS=4 bash bench/run.sh 1000 5m

# 轻负载: 100 user 5min
bash bench/run.sh 100 5m
```

## 输出

| 文件 | 内容 |
|---|---|
| `report-identity-<N>-<ts>.html` | locust HTML — P50/P95/P99 / RPS / error rate |
| `csv-identity-<N>-<ts>_*.csv` | 详细 CSV — 二次分析 |
| `docker-stats-identity-<N>-<ts>.txt` | identity 容器 CPU / mem 时序 |
| `pg-state-identity-<N>-<ts>.txt` | PG 表 row count (users_audit 增多少 = 多少次 token 发放) |
| `identity-log-<N>-<ts>.txt` | identity 全 log dump |

## 任务权重

- **70% POST /token (client_credentials)** — 真热路径, bcrypt verify + JWT RSA sign
- **25% GET /.well-known/jwks.json** — gateway 拉 JWKS
- **5% GET /healthz** — sanity

## 真瓶颈预测

**bcrypt 12 rounds ≈ 250-400ms / verify** (Python `bcrypt` C ext, 释放 GIL).

| 配置 | 极限 (理论) |
|---|---|
| 1 worker, 1 vCPU | 1000 / 300 ≈ 3-4 verify/s |
| 2 worker, 4 vCPU | 8-12 verify/s |
| 4 worker, 4 vCPU | 12-16 verify/s |
| 8 worker, 8 vCPU | 24-32 verify/s |

**如果 P99 > 30s 且 fail rate 高, 不是 identity bug, 是 bcrypt 12 round 物理上限**.

## 工件

| 文件 | 用途 |
|---|---|
| `Dockerfile.identity` | identity 镜像 (清华 mirror) |
| `docker-compose-identity-bench.yml` | pg + identity 两服务 |
| `clients.bench.yaml` | 5 个 bench service client (共享 bcrypt-12 hash) |
| `users.bench.yaml` | 占位 (client_credentials 不需要 user) |
| `identity-locustfile.py` | 3 task locust |
| `run.sh` | 一键 |

## bcrypt hash 怎么来

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'bench-secret-2026', bcrypt.gensalt(rounds=12)).decode())"
```

5 个 bench client 共享同一 hash, 因为 bcrypt salt 已在 hash 内, server 5 次 verify 仍跑 5 次完整 bcrypt (不是 cache 命中).

## 跟 BL-F10 gateway bench 对照

| 维度 | gateway BL-F10 | identity BL-F11 |
|---|---|---|
| 真热路径 | 流式 chat 转发 | bcrypt verify + JWT sign |
| 单 worker 瓶颈 | asyncio loop CPU | bcrypt CPU (释放 GIL) |
| 多 worker scaling | 2.2× linear (实测) | 期望接近线性 (bcrypt 释放 GIL) |
| race 验证 | quota_events 0 race ✅ | users_audit 累计 = success token 数 |
| prod 默认 worker | 4 | 2 (load 比 gateway 轻) |

## 跟 prod 部署关系

bench 跑出来的 worker 数建议 (gateway 4 / identity 2) 已写进:
- `central/.env.production.example`: `GATEWAY_WORKERS=4` / `IDENTITY_WORKERS=2`
- `central/docker-compose.yml`: gateway / identity service 读这 env

bench 重测后改建议 → 改 `.env.production.example` 默认值 → 客户 IT 自动跟.

## 跟 manifesto 对齐

- bench 全本机, 不出公司
- 跑完 down -v 清 tmpfs PG, 不留数据
- identity 走真 OIDC client_credentials grant (跟 prod hermes-cli 路径一致), 不走 dev_token bypass
