"""DevTokenProvider 多账号测试 (五一 sprint 5/2 RBAC).

覆盖:
- yaml 加载多账号
- token 匹配返对应 role / department / managed_departments
- env CATFISH_DEV_TOKEN back-compat (走 default)
- 没 yaml 时 env 仍能用 (老兼容)
- 无效 token 返 None
- list_dev_users 返脱敏列表
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catfish_gateway.auth.dev_token import DevTokenProvider, list_dev_users


@pytest.fixture
def yaml_path(tmp_path: Path, monkeypatch) -> Path:
    """isolated dev_users.yaml."""
    p = tmp_path / "dev_users.yaml"
    monkeypatch.setenv("CATFISH_DEV_USERS_PATH", str(p))
    return p


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ── 多账号加载 ──────────────────────────────────────────────


def test_no_yaml_env_token_works(yaml_path: Path, monkeypatch) -> None:
    """5/9 BL-security: yaml 必须有 default 段, env token 才能兜底.

    老兼容路径 (任意 token 都能解成 admin) 已废 (token 偷渡漏洞).
    现在: 显式写 dev_users.yaml.default 才走兜底.
    """
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "my-env-token")
    _write_yaml(yaml_path, """
default:
  email: dev-user@catfish.dev
  name: Dev User
  department: 默认部门
  role: admin
""")
    p = DevTokenProvider()
    user = p.verify_bearer("Bearer my-env-token")
    assert user is not None
    assert user.role == "admin"
    assert user.sub == "dev-user@catfish.dev"


def test_no_yaml_no_default_rejects(yaml_path: Path, monkeypatch) -> None:
    """没 yaml.default 段时 env token → return None (强制走 SSO).

    5/9 改的安全行为: 老 hardcoded 'dev-user' 兜底废, 防偷渡.
    """
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "my-env-token")
    # 不写 yaml — yaml_path 文件不存在 / cfg.default = None
    p = DevTokenProvider()
    user = p.verify_bearer("Bearer my-env-token")
    assert user is None  # 没 default → 拒


def test_yaml_admin_account(yaml_path: Path) -> None:
    _write_yaml(yaml_path, """
users:
  - email: admin@catfish.dev
    token: dev-admin
    name: Admin
    department: 总裁办
    role: admin
""")
    p = DevTokenProvider()
    user = p.verify_bearer("Bearer dev-admin")
    assert user is not None
    assert user.role == "admin"
    assert user.sub == "admin@catfish.dev"
    assert user.department == "总裁办"
    assert user.managed_departments == []


def test_yaml_manager_with_managed_departments(yaml_path: Path) -> None:
    _write_yaml(yaml_path, """
users:
  - email: zhang@catfish.dev
    token: dev-mgr
    department: 研发部
    role: manager
    managed_departments: ["研发部", "产品部"]
""")
    p = DevTokenProvider()
    user = p.verify_bearer("Bearer dev-mgr")
    assert user is not None
    assert user.role == "manager"
    assert user.department == "研发部"
    assert user.managed_departments == ["研发部", "产品部"]
    assert user.can_manage_department("研发部")
    assert user.can_manage_department("产品部")
    assert not user.can_manage_department("销售部")


def test_yaml_employee(yaml_path: Path) -> None:
    _write_yaml(yaml_path, """
users:
  - email: alice@catfish.dev
    token: dev-emp
    department: 研发部
    role: employee
""")
    p = DevTokenProvider()
    user = p.verify_bearer("Bearer dev-emp")
    assert user is not None
    assert user.role == "employee"
    assert user.managed_departments == []
    assert not user.can_manage_department("研发部")
    assert not user.is_admin()
    assert not user.is_manager()


def test_yaml_multiple_accounts_all_work(yaml_path: Path) -> None:
    """yaml 多账号, 各自 token 都能 verify."""
    _write_yaml(yaml_path, """
users:
  - email: admin@x.com
    token: tk-admin
    role: admin
  - email: mgr@x.com
    token: tk-mgr
    role: manager
    managed_departments: ["dept-a"]
  - email: emp@x.com
    token: tk-emp
    role: employee
""")
    p = DevTokenProvider()
    assert p.verify_bearer("Bearer tk-admin").role == "admin"
    assert p.verify_bearer("Bearer tk-mgr").role == "manager"
    assert p.verify_bearer("Bearer tk-emp").role == "employee"


def test_invalid_token_returns_none(yaml_path: Path) -> None:
    _write_yaml(yaml_path, """
users:
  - email: a@x.com
    token: tk-a
    role: admin
""")
    p = DevTokenProvider()
    assert p.verify_bearer("Bearer not-in-yaml") is None
    assert p.verify_bearer("Bearer ") is None
    assert p.verify_bearer(None) is None
    assert p.verify_bearer("Basic foo") is None  # 不是 Bearer


def test_default_section_back_compat(yaml_path: Path, monkeypatch) -> None:
    """yaml 有 default 段, env CATFISH_DEV_TOKEN 走 default."""
    _write_yaml(yaml_path, """
users:
  - email: someone@x.com
    token: tk-someone
    role: admin
default:
  email: dev-user@catfish.dev
  department: engineering
  role: admin
  managed_departments: ["engineering"]
""")
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "dev-token-local")
    p = DevTokenProvider()
    user = p.verify_bearer("Bearer dev-token-local")
    assert user is not None
    assert user.sub == "dev-user@catfish.dev"
    assert user.managed_departments == ["engineering"]


# ── list_dev_users (Companion 切换器用) ──────────────────────


def test_list_dev_users_returns_all(yaml_path: Path) -> None:
    _write_yaml(yaml_path, """
users:
  - email: a@x.com
    token: tk-a
    name: Admin
    department: 总裁办
    role: admin
  - email: b@x.com
    token: tk-b
    name: Manager
    role: manager
    managed_departments: ["d1"]
""")
    users = list_dev_users()
    assert len(users) == 2
    assert users[0]["email"] == "a@x.com"
    assert users[0]["role"] == "admin"
    assert users[0]["token"] == "tk-a"  # dev 模式暴露 token
    assert users[1]["managed_departments"] == ["d1"]


def test_list_dev_users_no_yaml(yaml_path: Path) -> None:
    """没 yaml 文件 → 返空列表."""
    # yaml_path fixture 设了 env 但没创建文件
    users = list_dev_users()
    assert users == []
