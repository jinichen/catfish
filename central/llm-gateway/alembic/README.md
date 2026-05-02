# Gateway 数据库 Migration (Alembic)

> 五一 sprint 5/2 收尾加. 替代 db.py 老 idempotent CREATE TABLE.

## 用法

```bash
cd central/llm-gateway

# 跑所有未应用 migration (生产部署 / 升级时调一次)
alembic upgrade head

# 看当前 schema 版本
alembic current

# 看所有 migration 历史
alembic history

# 回滚一个版本 (慎用 — gateway_audit 数据会丢)
alembic downgrade -1

# 生成新 migration (改 schema 时)
alembic revision -m "add column X"
# 然后编辑 alembic/versions/ 里新生成的 .py 文件填 upgrade/downgrade
```

## URL 来源

`alembic/env.py` 复用 `catfish_gateway.db.db_url()`:

1. `env CATFISH_DB_URL` (跨进程统一最优先)
2. `config/database.yaml` 的 `url` 字段
3. 都没 → `alembic upgrade` 报错退出 (alembic 必须 PG, sqlite/jsonl fallback 不走 alembic)

## 何时不需要 alembic

`CATFISH_DB_URL` 不配, 单机 / 单测 / dev mode 走 sqlite (quota_events) + jsonl (gateway_audit) —
表自动创建, 不需要 alembic.

## 表归属

| 表 | 服务 | 内容 |
|---|---|---|
| `quota_events` | gateway | 滑动窗口 token 用量 |
| `gateway_audit` | gateway | 中央 metadata audit (无对话内容) |
| `users` | identity-server | 员工身份 |
| `registry_agents` | identity-server | Plan D federation registry |

两服务共享同一 PG database, 但各自 alembic 管自己的表 (微服务标准模式).
表名各自 prefix 防冲突.

## CI 集成 (后续)

```yaml
# .github/workflows/test-gateway.yml
- name: Run alembic migrations on test DB
  run: |
    cd central/llm-gateway
    CATFISH_DB_URL=postgresql://test:test@localhost/catfish_test \
      alembic upgrade head
```
