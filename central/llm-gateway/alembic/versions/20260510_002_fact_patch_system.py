"""BL-Q3-FACT P0 MVP — 事实补丁系统 4 张表 (5/10)

跟 jsonl 落地双写, PG 主存储 (复用 metrics.py / quota.py 同款 _use_pg() 模式).
失败兜底 jsonl, 保持本地可调试 + 离线可工作.

设计文档: docs/CATFISH-FACT-PATCH-DESIGN.md 第五章.

Revision ID: 20260510_002
Revises: 20260502_001
Create Date: 2026-05-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260510_002"
down_revision: Union[str, None] = "20260502_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── fact_changes: 政策变更主记录 ───────────────────────────
    # 一行 = 一次合规员上传的变更文件 + LLM 提取出的事实点 (JSONB)
    op.execute("""
        CREATE TABLE IF NOT EXISTS fact_changes (
            id                  TEXT PRIMARY KEY,
            title               TEXT NOT NULL,
            original_filename   TEXT NOT NULL,
            raw_path            TEXT NOT NULL,
            ext                 TEXT NOT NULL,
            size_bytes          BIGINT NOT NULL,
            effective_date      DATE,
            uploaded_by         TEXT NOT NULL,
            uploaded_at_ms      BIGINT NOT NULL,
            extracted_at_ms     BIGINT,
            analyzed_at_ms      BIGINT,
            approved_at_ms      BIGINT,
            dismissed_at_ms     BIGINT,
            dismissed_by        TEXT,
            status              TEXT NOT NULL DEFAULT 'uploaded',
            -- uploaded → extracted → analyzed → patches_ready → approved / dismissed
            facts_json          JSONB,
            -- LLM 提取的事实点完整结构 (跟 fact.json 同 schema)
            llm_summary         TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_fact_uploaded_at ON fact_changes(uploaded_at_ms DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fact_status     ON fact_changes(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fact_uploaded_by ON fact_changes(uploaded_by)")

    # ── fact_skill_impacts: 受影响 skill 关联 ──────────────────
    # 一行 = 一个 fact_change × 一个 skill 的"可能影响"判定 (LLM 给的 confidence + reason)
    op.execute("""
        CREATE TABLE IF NOT EXISTS fact_skill_impacts (
            id                  BIGSERIAL PRIMARY KEY,
            fact_change_id      TEXT NOT NULL REFERENCES fact_changes(id) ON DELETE CASCADE,
            fact_point_id       TEXT,
            -- 对应到 fact_changes.facts_json[].id, 串明白是哪个事实点导致这个 impact
            fact_summary        TEXT,
            skill_namespace     TEXT NOT NULL,
            skill_name          TEXT NOT NULL,
            confidence          REAL NOT NULL,
            reason              TEXT,
            detection_method    TEXT,
            -- 'llm_semantic' (P0) / 'grep' (P1) / 'bm25' (P1)
            created_at_ms       BIGINT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_impact_fact     ON fact_skill_impacts(fact_change_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_impact_skill    ON fact_skill_impacts(skill_namespace, skill_name)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_impact_conf     ON fact_skill_impacts(confidence DESC)")

    # ── fact_skill_patches: LLM 生成的改进 patch ───────────────
    # 一行 = 一个 patch (跟一个 impact 1:1), 待审批
    op.execute("""
        CREATE TABLE IF NOT EXISTS fact_skill_patches (
            id                  BIGSERIAL PRIMARY KEY,
            fact_change_id      TEXT NOT NULL REFERENCES fact_changes(id) ON DELETE CASCADE,
            fact_point_id       TEXT,
            skill_namespace     TEXT NOT NULL,
            skill_name          TEXT NOT NULL,
            skill_version_base  TEXT NOT NULL,
            -- patch 基于哪个版本改的
            confidence          REAL,
            rationale           TEXT,
            -- 改的理由 (LLM 引用 raw_quote 写的, 给审批员看)
            changes_json        JSONB NOT NULL,
            -- 数组: [{description, old_snippet, new_snippet}, ...]
            full_new_content    TEXT NOT NULL,
            -- 完整改后的 SKILL.md, 落盘到 SkillsHub 用
            status              TEXT NOT NULL DEFAULT 'pending',
            -- pending → approved / rejected
            generated_at_ms     BIGINT NOT NULL,
            approved_at_ms      BIGINT,
            approved_by         TEXT,
            rejected_at_ms      BIGINT,
            rejected_by         TEXT,
            published_version   TEXT
            -- 采纳后 SkillsHub 真发布的版本号 (如 1.0.fact-a1b2c3d4)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_patch_fact      ON fact_skill_patches(fact_change_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_patch_skill     ON fact_skill_patches(skill_namespace, skill_name)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_patch_status    ON fact_skill_patches(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_patch_generated ON fact_skill_patches(generated_at_ms DESC)")

    # ── fact_audit: 操作审计链 ────────────────────────────────
    # 一行 = 一次操作 (upload / extract / analyze / approve_patch / reject_patch / dismiss)
    # 央企合规边界 — auditable continual learning 的核心.
    op.execute("""
        CREATE TABLE IF NOT EXISTS fact_audit (
            id              BIGSERIAL PRIMARY KEY,
            ts_ms           BIGINT NOT NULL,
            action          TEXT NOT NULL,
            fact_change_id  TEXT REFERENCES fact_changes(id) ON DELETE SET NULL,
            by_user         TEXT NOT NULL,
            meta_json       JSONB
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_fact_audit_ts   ON fact_audit(ts_ms DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fact_audit_fact ON fact_audit(fact_change_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fact_audit_user ON fact_audit(by_user)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fact_audit_action ON fact_audit(action)")


def downgrade() -> None:
    # 顺序: 反向 (有外键的先删)
    op.execute("DROP TABLE IF EXISTS fact_audit")
    op.execute("DROP TABLE IF EXISTS fact_skill_patches")
    op.execute("DROP TABLE IF EXISTS fact_skill_impacts")
    op.execute("DROP TABLE IF EXISTS fact_changes")
