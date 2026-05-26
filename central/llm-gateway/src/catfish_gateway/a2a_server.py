"""STUB — DEPRECATED 5/26 (A2A federation 整套砍).

# 5/26 鸿波拍板砍

A2A federation (Plan D, 5/12 BL-FED2 ship) 整套砍. 理由:
1. 1695+ LOC, 0 真客户用 — 单机 mock 状态, 没真跨 mac 验过
2. a2a_jwt 私钥在中央代码读 (违反 SaaS-safe 边界)
3. hermes 0.14 release notes 0 命中 a2a/federation/inter-agent — 未来也不会被
   hermes 替代; 真要 federation 等真客户问再从 git history 拉回
4. 占 ALLOWLIST 5 条 (a2a_audit/journal_hook/allow/jwt/self_register), 占
   maintenance 时间不值

# 砍范围 (同批)

- gateway 7 个 a2a_*.py 全改 stub
- app.py 3 处 caller 删 (self_register / build_a2a_router / a2a_internal_ask)
- tool-bridge: catfish_a2a_ask + catfish_list_a2a_help dispatch + schema +
  expert_consult.py + expertise.py extract/consult 部分
- identity-server registry.py /registry/by-expertise endpoint
- employee_journal.py 同砍 (唯一 caller 是 a2a_journal_hook)
- ALLOWLIST 5 条全清

# 关联

- docs/HERMES-014-AUDIT.md (5/26 audit verdict: hermes 0.14 没原生 federation)
- docs/BACKLOG-2026-05-26.md (鸿波"还有哪些任务"全景)
- git history: 真要恢复 git log --diff-filter=A --follow central/llm-gateway/src/catfish_gateway/a2a_server.py
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "a2a_server 5/26 砍 — A2A federation 整套停 (0 真客户 + 1695 LOC + 私钥违规). "
    "真要恢复从 git history 拉."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26 A2A 砍] {_DEPRECATED_NOTICE} (attr: {name})")
