"""P3.5.60 (6/22 鸿波 catch "继续完成") — gateway_audit (ts_ms, department) 索引.

/admin/perf 加 by_department PERCENTILE_CONT 聚合 → query_perf_summary_global
带 dept filter 路径 + GROUP BY department. 已有 idx_audit_ts_user/model/status,
缺 dept 这一刀, 30 天窗口 + by_department 全表扫太慢.

CONCURRENTLY 不加 — alembic 一笔事务里跑, CONCURRENTLY 要求独立事务. 现量 (
单租户 demo + 早期 OSS 用户) 几十万行可接受短锁. 上规模再切 manual maint window.

Revision ID: 20260622_006
Revises: 20260607_005
Create Date: 2026-06-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260622_006"
down_revision: Union[str, None] = "20260607_005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # (ts_ms, department) 复合索引 — 给 P3.5.60 /admin/perf by_department 加速.
    # PERCENTILE_CONT GROUP BY department WHERE ts_ms >= cutoff 是热路径.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_ts_dept "
        "ON gateway_audit(ts_ms, department)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_ts_dept")
