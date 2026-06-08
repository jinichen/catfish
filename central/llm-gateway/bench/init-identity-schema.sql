-- BL-F10 bench: 模拟 prod 部署 (identity-server + gateway 共享同一 PG).
--
-- # 干啥
--
-- bench 只跑 gateway 自己的 alembic, 不跑 identity-server. gateway 的
-- `fetch_user_metadata()` (src/catfish_gateway/db.py:141) 会 SELECT 一个
-- 由 identity-server 管的 `users` 表, prod 共享 PG 下查得到, bench 下没建
-- 就撞 `asyncpg.UndefinedTableError: relation "users" does not exist`. 每
-- 请求一次 ERROR + 6 行 stack trace → 1000 user 5min 跑出 ~50000 行垃圾.
--
-- 不引 identity-server 整套 (太重), 改用 postgres 官方 docker entrypoint
-- 机制 — 容器 first start 自动跑 `/docker-entrypoint-initdb.d/*.sql`. 把
-- identity-server alembic 20260502_001 + 20260510_002 的 users / users_audit /
-- registry_agents 直接 inline 进来 + seed 5 个 bench 员工.
--
-- # 跟 identity-server 真 schema 对齐
--
-- 源:
--   central/identity-server/alembic/versions/20260502_001_initial_users_registry.py
--   central/identity-server/alembic/versions/20260510_002_users_admin_fields.py
--
-- 如果以后 identity-server 加字段, 这个文件要同步 (bench/README.md 已记).

-- ── users (员工身份) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    email                TEXT PRIMARY KEY,
    password_hash        TEXT NOT NULL,
    name                 TEXT NOT NULL DEFAULT '',
    department           TEXT NOT NULL DEFAULT '',
    tier                 TEXT NOT NULL DEFAULT 'employee',
    role                 TEXT NOT NULL DEFAULT '',
    managed_departments  JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 20260510_002 admin 字段
    locked               BOOLEAN NOT NULL DEFAULT FALSE,
    locked_at            TIMESTAMPTZ,
    locked_by            TEXT,
    deleted_at           TIMESTAMPTZ,  -- NULL = 未删, fetch_user_metadata 过滤
    created_by           TEXT NOT NULL DEFAULT 'system',
    last_login_at        TIMESTAMPTZ,
    password_changed_at  TIMESTAMPTZ,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_department ON users(department);
CREATE INDEX IF NOT EXISTS idx_users_tier ON users(tier);

-- ── users_audit (admin 操作审计) ──────────────────────────
CREATE TABLE IF NOT EXISTS users_audit (
    id           BIGSERIAL PRIMARY KEY,
    ts_ms        BIGINT NOT NULL,
    action       TEXT NOT NULL,
    target_email TEXT NOT NULL,
    by_email     TEXT NOT NULL,
    meta         JSONB
);
CREATE INDEX IF NOT EXISTS idx_users_audit_ts ON users_audit(ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_users_audit_target ON users_audit(target_email);
CREATE INDEX IF NOT EXISTS idx_users_audit_by ON users_audit(by_email);

-- ── registry_agents (Plan D Federation 注册表 — gateway 不查, 但建着) ──
CREATE TABLE IF NOT EXISTS registry_agents (
    sub               TEXT PRIMARY KEY,
    catfish_endpoint  TEXT NOT NULL,
    jwks_uri          TEXT NOT NULL,
    public_pem        TEXT NOT NULL DEFAULT '',
    department        TEXT NOT NULL DEFAULT '',
    capabilities      JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_registry_last_seen ON registry_agents(last_seen);

-- ── seed: 5 个 bench 员工 (跟 dev_users.bench.yaml 一致) ──
-- password_hash 用 bcrypt 占位 ('bench-not-used' 的 bcrypt, dev_token 路径
-- 不验密码, 但字段 NOT NULL).
INSERT INTO users (email, password_hash, name, department, tier, role, managed_departments) VALUES
    ('bench-admin@catfish.bench', '$2b$12$bench.placeholder.not.used.in.bench', 'Bench Admin', 'bench-admin', 'admin',    'admin',    '[]'::jsonb),
    ('bench-alice@catfish.bench', '$2b$12$bench.placeholder.not.used.in.bench', 'Alice (eng)', 'bench-eng',   'employee', 'employee', '[]'::jsonb),
    ('bench-bob@catfish.bench',   '$2b$12$bench.placeholder.not.used.in.bench', 'Bob (eng)',   'bench-eng',   'employee', 'employee', '[]'::jsonb),
    ('bench-carol@catfish.bench', '$2b$12$bench.placeholder.not.used.in.bench', 'Carol (sales)', 'bench-sales', 'employee', 'employee', '[]'::jsonb),
    ('bench-dave@catfish.bench',  '$2b$12$bench.placeholder.not.used.in.bench', 'Dave (sales)',  'bench-sales', 'employee', 'employee', '[]'::jsonb)
ON CONFLICT (email) DO NOTHING;
