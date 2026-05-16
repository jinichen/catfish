"""BL-RBAC-DAY3B (5/17) — User.can_access 真 RBAC 实施测试.

Phase 1 时 can_access 永远返 True. 现在按 effective_allowed_models 真过滤:
  - 空 list → 全允许 (开放默认 / 无 dept 配置)
  - [m1, m2] → 只允许这俩
  - sysadmin 永远绕过 RBAC

跟 OIDC claims 联动: OIDCProvider 验 JWT 后塞 effective_allowed_models
(identity to_oidc_claims_async 已合并 user + dept).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from catfish_gateway.auth.base import User


@dataclass
class _FakeModel:
    """模拟 ModelConfig (catfish_gateway.config) 只保留 can_access 关心的字段."""

    name: str
    mode: str = "chat"


# ── effective_allowed_models 空 = 全允许 ──────────────────────────


def test_empty_allowed_models_means_all_allowed():
    """空 list → 全允许 (开放默认)."""
    u = User(sub="alice@x", effective_allowed_models=[])
    assert u.can_access(_FakeModel("catfish-private-main")) is True
    assert u.can_access(_FakeModel("catfish-public-deepseek-flash")) is True
    assert u.can_access(_FakeModel("any-model")) is True


def test_none_allowed_models_means_all_allowed():
    """None (没传) → __post_init__ 转空 list → 全允许."""
    u = User(sub="alice@x")  # 没传 effective_allowed_models
    assert u.effective_allowed_models == []
    assert u.can_access(_FakeModel("catfish-private-main")) is True


# ── 部门收紧 ───────────────────────────────────────────────────


def test_sales_only_deepseek_flash():
    """销售部门 dept.allowed_models=['catfish-public-deepseek-flash'] 场景."""
    u = User(
        sub="sales@x",
        department="sales",
        effective_allowed_models=["catfish-public-deepseek-flash"],
    )
    assert u.can_access(_FakeModel("catfish-public-deepseek-flash")) is True
    assert u.can_access(_FakeModel("catfish-private-main")) is False
    assert u.can_access(_FakeModel("catfish-public-gemini-pro")) is False


def test_legal_only_private():
    """法务部门 dept.allowed_models=['catfish-private-main', 'catfish-private-vision'] 场景."""
    u = User(
        sub="legal@x",
        department="legal",
        effective_allowed_models=[
            "catfish-private-main",
            "catfish-private-vision",
        ],
    )
    assert u.can_access(_FakeModel("catfish-private-main")) is True
    assert u.can_access(_FakeModel("catfish-private-vision")) is True
    assert u.can_access(_FakeModel("catfish-public-deepseek-flash")) is False


# ── sysadmin 绕过 RBAC ──────────────────────────────────────────


def test_sysadmin_bypasses_rbac():
    """sysadmin 即使 effective_allowed_models 收紧也全允许 (运维 / 排错)."""
    u = User(
        sub="admin@x",
        role="sysadmin",
        effective_allowed_models=["catfish-public-deepseek-flash"],  # 表面只允许这个
    )
    # 但 sysadmin 任何 model 都能用
    assert u.can_access(_FakeModel("catfish-private-main")) is True
    assert u.can_access(_FakeModel("any-secret-model")) is True


def test_admin_does_not_bypass_rbac():
    """admin (非 sysadmin) 仍受 RBAC 限制. 区别 sysadmin 给最高权限运维 vs admin 业务管理."""
    u = User(
        sub="admin@x",
        role="admin",
        effective_allowed_models=["catfish-public-deepseek-flash"],
    )
    assert u.can_access(_FakeModel("catfish-public-deepseek-flash")) is True
    # admin 不绕过, 受 effective_allowed_models 限制
    assert u.can_access(_FakeModel("catfish-private-main")) is False


# ── model 输入兼容 (ModelConfig 或 str) ─────────────────────────


def test_can_access_accepts_str_model_name():
    """can_access(str) 兼容 — model 可能是 ModelConfig 或纯 name str."""
    u = User(
        sub="x@y",
        effective_allowed_models=["catfish-public-deepseek-flash"],
    )
    # str 直接传
    assert u.can_access("catfish-public-deepseek-flash") is True
    assert u.can_access("catfish-private-main") is False
