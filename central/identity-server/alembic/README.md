# Identity-Server 数据库 Migration (Alembic)

> 五一 sprint 5/2 收尾加. 替代 db.py 老 idempotent CREATE TABLE.
> 跟 gateway alembic 一致 (各服务独立 alembic 历史, 共享 PG database).

## 用法

```bash
cd central/identity-server

# 跑所有未应用 migration (生产部署 / 升级时调一次)
alembic upgrade head

# 看当前 schema 版本
alembic current

# 看所有 migration 历史
alembic history

# 回滚一个版本 (慎用 — users 数据会丢)
alembic downgrade -1

# 生成新 migration (改 schema 时)
alembic revision -m "add column X"
```

## URL 来源

`alembic/env.py` 复用 `catfish_identity.db.db_url()`:

1. `env CATFISH_DB_URL`
2. `config/database.yaml` 的 `url` 字段
3. 都没 → `alembic upgrade` 报错退出

## 何时不需要 alembic

`CATFISH_DB_URL` 不配, 单机 / 单测 / dev 走 yaml fallback (config/users.yaml).
db.py 老 SCHEMA_SQL 仍保留作 dev 启动时自动创建表的兜底, 不需要先跑 alembic.

## 表归属

| 表 | 服务 | 谁管 schema |
|---|---|---|
| `users` | identity-server | **本 alembic** ★ |
| `registry_agents` | identity-server | **本 alembic** ★ |
| `quota_events` | gateway | gateway alembic |
| `gateway_audit` | gateway | gateway alembic |

两服务共享 PG database, 各自 alembic 互不干扰. alembic_version 表也是各服务一份
(Postgres `public` schema 下 alembic_version 会冲突 — Phase 2 改成 alembic 用
`version_table_schema=catfish_identity` / `version_table_schema=catfish_gateway` 隔离).

## 多 service alembic_version 共存 (后续)

现在两个 service 各跑 alembic upgrade head, 最后 alembic_version 行被覆盖, 不同步.

修法 (Phase 2):
```python
# env.py 里
context.configure(
    connection=connection,
    version_table="alembic_version_identity",  # 各服务独立表名
)
```

或更激进 — 用 PG schema 隔离:
```python
context.configure(
    connection=connection,
    version_table="alembic_version",
    version_table_schema="catfish_identity",  # CREATE SCHEMA catfish_identity 后用
)
```

现在 demo 阶段两个 service 各自跑就行, 第二个跑会覆盖第一个的 alembic_version 行
(因为我们都用 revision_id `20260502_001`, 巧合下不冲突 — 实际生产要分开).
