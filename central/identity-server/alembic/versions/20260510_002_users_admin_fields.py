"""users 表加 admin 管理字段 (BL-ARCH1 P1 完整用户管理, 5/10)

新加字段:
  locked              BOOLEAN  锁账号 (登录拒)
  locked_at           TIMESTAMPTZ  锁定时间
  locked_by           TEXT  谁锁的 (admin email)
  deleted_at          TIMESTAMPTZ  软删时间 (NULL = 未删)
  created_by          TEXT  谁创建的 (admin email, 系统初始化为 'system')
  last_login_at       TIMESTAMPTZ  最后登录时间
  password_changed_at TIMESTAMPTZ  密码变更时间 (强制定期改密用)
  must_change_password BOOLEAN 强制下次登录改密

也支持 sysadmin 角色 — 在 tier 字段已有 ('sysadmin' / 'admin' / 'employee'), 不加新字段.

索引:
  idx_users_deleted_at      非空快查 (admin 看活跃 / 已删)
  idx_users_department      按部门列
  idx_users_tier            按 tier 列 (sysadmin / admin)

Revision ID: 20260510_002
Revises: 20260502_001
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260510_002"
down_revision: Union[str, None] = "20260502_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_by TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by TEXT NOT NULL DEFAULT 'system'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE")

    op.execute("CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users(deleted_at) WHERE deleted_at IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_department ON users(department)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_tier ON users(tier)")

    # ── users_audit: admin 操作审计 (谁创建 / 改 / 锁 / 删 谁) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS users_audit (
            id          BIGSERIAL PRIMARY KEY,
            ts_ms       BIGINT NOT NULL,
            action      TEXT NOT NULL,
            target_email TEXT NOT NULL,
            by_email    TEXT NOT NULL,
            meta        JSONB
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_audit_ts ON users_audit(ts_ms DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_audit_target ON users_audit(target_email)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_audit_by ON users_audit(by_email)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_audit_by")
    op.execute("DROP INDEX IF EXISTS idx_users_audit_target")
    op.execute("DROP INDEX IF EXISTS idx_users_audit_ts")
    op.execute("DROP TABLE IF EXISTS users_audit")

    op.execute("DROP INDEX IF EXISTS idx_users_tier")
    op.execute("DROP INDEX IF EXISTS idx_users_department")
    op.execute("DROP INDEX IF EXISTS idx_users_deleted_at")

    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS must_change_password")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_changed_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_login_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS created_by")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS locked_by")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS locked_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS locked")
