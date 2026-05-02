"""users.py 单测."""
from __future__ import annotations

from pathlib import Path

import bcrypt
import pytest

from catfish_identity.users import UserRegistry, hash_password


def _write_yaml(tmp_path: Path, content: str) -> Path:
    fp = tmp_path / "users.yaml"
    fp.write_text(content, encoding="utf-8")
    return fp


def test_load_normal(tmp_path) -> None:
    pwd_hash = hash_password("secret123")
    _write_yaml(tmp_path, f"""
users:
  - email: alice@x.com
    password_hash: {pwd_hash}
    name: Alice
    department: sales
    tier: employee
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    assert len(reg) == 1
    user = reg.find("alice@x.com")
    assert user is not None
    assert user.name == "Alice"
    assert user.department == "sales"


def test_email_case_insensitive(tmp_path) -> None:
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: alice@x.com
    password_hash: {pwd_hash}
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    assert reg.find("ALICE@X.com") is not None
    assert reg.find("Alice@X.COM") is not None
    assert reg.find("bob@x.com") is None


def test_verify_password_ok(tmp_path) -> None:
    pwd_hash = hash_password("secret123")
    _write_yaml(tmp_path, f"""
users:
  - email: alice@x.com
    password_hash: {pwd_hash}
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    assert reg.verify_password("alice@x.com", "secret123") is not None
    assert reg.verify_password("alice@x.com", "wrong_password") is None


def test_verify_unknown_user_returns_none(tmp_path) -> None:
    """未知 user 也走 bcrypt 不暴露 timing"""
    _write_yaml(tmp_path, """users: []""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    assert reg.verify_password("nobody@x.com", "anything") is None


def test_missing_yaml_empty_registry(tmp_path) -> None:
    """文件不存在 → 空注册表, 不抛"""
    reg = UserRegistry(users_path=tmp_path / "absent.yaml")
    assert len(reg) == 0
    assert reg.find("anyone@x.com") is None
    assert reg.verify_password("x", "y") is None


def test_skips_invalid_entries(tmp_path) -> None:
    """缺 email/password_hash 的条目 skip 掉, 不抛"""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: ""
    password_hash: {pwd_hash}
  - email: alice@x.com
    # 缺 password_hash
  - "not a dict"
  - email: bob@x.com
    password_hash: {pwd_hash}
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    assert len(reg) == 1
    assert reg.find("bob@x.com") is not None


def test_to_oidc_claims(tmp_path) -> None:
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: alice@x.com
    password_hash: {pwd_hash}
    name: Alice
    department: sales
    tier: admin
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    user = reg.find("alice@x.com")
    claims = user.to_oidc_claims()
    assert claims["email"] == "alice@x.com"
    assert claims["email_verified"] is True
    assert claims["name"] == "Alice"
    assert claims["department"] == "sales"
    assert claims["tier"] == "admin"
    # password_hash 不能进 claims
    assert "password_hash" not in claims


# ── RBAC (五一 sprint 5/2 加, BL-D8) ────────────────────────


def test_role_field_loaded(tmp_path) -> None:
    """yaml 里 role 字段透传."""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: alice@x.com
    password_hash: {pwd_hash}
    department: 研发部
    role: manager
    managed_departments:
      - 研发部
      - 测试部
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    user = reg.find("alice@x.com")
    assert user.role == "manager"
    assert user.managed_departments == ["研发部", "测试部"]
    assert user.effective_role() == "manager"


def test_role_default_employee(tmp_path) -> None:
    """没填 role → effective_role = employee (老 yaml 兼容)."""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: bob@x.com
    password_hash: {pwd_hash}
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    user = reg.find("bob@x.com")
    assert user.role == ""
    assert user.effective_role() == "employee"


def test_legacy_tier_admin_maps_to_role_admin(tmp_path) -> None:
    """老 yaml 没 role 但 tier=admin → effective_role = admin."""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: ceo@x.com
    password_hash: {pwd_hash}
    tier: admin
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    user = reg.find("ceo@x.com")
    assert user.effective_role() == "admin"


def test_role_whitelist_rejects_invalid(tmp_path) -> None:
    """role 白名单只接受 admin/manager/employee, 其他 fallback 到默认."""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: hack@x.com
    password_hash: {pwd_hash}
    role: superhacker
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    user = reg.find("hack@x.com")
    assert user.role == ""  # superhacker 被拒, 清空
    assert user.effective_role() == "employee"


def test_oidc_claims_include_rbac(tmp_path) -> None:
    """to_oidc_claims 把 role / managed_departments 一起带出."""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: m@x.com
    password_hash: {pwd_hash}
    department: 研发部
    role: manager
    managed_departments:
      - 研发部
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    claims = reg.find("m@x.com").to_oidc_claims()
    assert claims["role"] == "manager"
    assert claims["managed_departments"] == ["研发部"]


def test_oidc_claims_admin_no_managed_departments(tmp_path) -> None:
    """admin 的 managed_departments 在 claims 里返 [] (隐式全权)."""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: a@x.com
    password_hash: {pwd_hash}
    role: admin
    managed_departments:
      - 不应该出现
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    claims = reg.find("a@x.com").to_oidc_claims()
    assert claims["role"] == "admin"
    # admin 的 managed_departments 在 claims 里清空 (admin 隐式全权, 不需要列)
    assert claims["managed_departments"] == []


def test_to_oidc_claims_default_name_from_email(tmp_path) -> None:
    """没 name 时 fallback 到 email 前缀"""
    pwd_hash = hash_password("x")
    _write_yaml(tmp_path, f"""
users:
  - email: alice@x.com
    password_hash: {pwd_hash}
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    claims = reg.find("alice@x.com").to_oidc_claims()
    assert claims["name"] == "alice"


def test_corrupted_hash_returns_none(tmp_path) -> None:
    """password_hash 格式坏了 → 验证失败但不抛"""
    _write_yaml(tmp_path, """
users:
  - email: alice@x.com
    password_hash: not-a-valid-bcrypt-hash
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    # find 能找到 (条目格式上有效)
    assert reg.find("alice@x.com") is not None
    # 但 verify 必失败
    assert reg.verify_password("alice@x.com", "anything") is None


def test_hash_password_helper() -> None:
    """hash_password 工具函数生成的 hash 能被 bcrypt verify"""
    h = hash_password("test_secret")
    assert h.startswith("$2b$")
    assert bcrypt.checkpw(b"test_secret", h.encode())
