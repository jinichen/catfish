"""P3.5.29 (6/17 鸿波) — roles 抽象层真**单元测试**.

测试覆盖:
1. load + resolve 真**正常路径**
2. fallback_chain 真**递归 resolve role refs**
3. fallback_chain 真**循环 ref** raise CircularRoleError
4. UnknownRoleError 真**未定义 role**
5. rbac_default_allowed 真**展开 + 去重**
6. to_public_dict 真**完整 payload**
7. reload 真**重新加载**
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from catfish_gateway import roles as roles_module


@pytest.fixture(autouse=True)
def reset_module_state():
    """每个 test 前真**重置**模块状态."""
    roles_module._roles = None
    roles_module._fallback_chain = None
    roles_module._rbac_default_allowed = None
    roles_module._loaded_path = None
    yield
    roles_module._roles = None
    roles_module._fallback_chain = None
    roles_module._rbac_default_allowed = None
    roles_module._loaded_path = None


def _write_yaml(content: str) -> pathlib.Path:
    """写临时 yaml 文件, 返路径."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return pathlib.Path(tmp.name)


# ─── load + resolve 真正常路径 ──────────────────────────────────


def test_load_and_resolve():
    path = _write_yaml(
        """
roles:
  chat_default: catfish-private-main
  rate_fast: catfish-public-qwen-flash
"""
    )
    roles_module.load_roles(path)
    assert roles_module.resolve(roles_module.Role.CHAT_DEFAULT) == "catfish-private-main"
    assert roles_module.resolve("rate_fast") == "catfish-public-qwen-flash"


def test_resolve_unknown_raises():
    path = _write_yaml("roles:\n  chat_default: main\n")
    roles_module.load_roles(path)
    with pytest.raises(roles_module.UnknownRoleError):
        roles_module.resolve("nonexistent_role")


def test_resolve_or_none_returns_none():
    path = _write_yaml("roles:\n  chat_default: main\n")
    roles_module.load_roles(path)
    assert roles_module.resolve_or_none("nonexistent") is None


def test_resolve_without_load_raises():
    with pytest.raises(roles_module.RolesNotLoadedError):
        roles_module.resolve("chat_default")


# ─── fallback_chain 真**递归 + 循环检测** ──────────────────────


def test_fallback_chain_resolves_role_refs():
    path = _write_yaml(
        """
roles:
  chat_default: catfish-private-main
  rate_fast: catfish-public-qwen-flash
fallback_chain:
  - chat_default
  - rate_fast
"""
    )
    roles_module.load_roles(path)
    chain = roles_module.resolve_fallback_chain()
    assert chain == ["catfish-private-main", "catfish-public-qwen-flash"]


def test_fallback_chain_allows_direct_model_name():
    """fallback_chain 真**直接写 model name** (不是 role ref) 兼容."""
    path = _write_yaml(
        """
roles:
  chat_default: catfish-private-main
fallback_chain:
  - chat_default
  - catfish-public-deepseek-flash
"""
    )
    roles_module.load_roles(path)
    chain = roles_module.resolve_fallback_chain()
    assert chain == ["catfish-private-main", "catfish-public-deepseek-flash"]


def test_fallback_chain_circular_raises():
    """A → A 真**自循环** raise."""
    path = _write_yaml(
        """
roles:
  loop_role: loop_role
fallback_chain:
  - loop_role
"""
    )
    roles_module.load_roles(path)
    with pytest.raises(roles_module.CircularRoleError):
        roles_module.resolve_fallback_chain()


# ─── RBAC 真**展开 + 去重** ─────────────────────────────────────


def test_rbac_default_allowed():
    path = _write_yaml(
        """
roles:
  chat_default: catfish-private-main
  vision: catfish-private-vision
  public_flash: catfish-public-qwen-flash
rbac_default_allowed:
  employee:
    - chat_default
  admin:
    - chat_default
    - vision
    - public_flash
"""
    )
    roles_module.load_roles(path)
    assert roles_module.list_models_for_rbac("employee") == ["catfish-private-main"]
    assert roles_module.list_models_for_rbac("admin") == [
        "catfish-private-main",
        "catfish-private-vision",
        "catfish-public-qwen-flash",
    ]


def test_rbac_unknown_role_raises():
    path = _write_yaml(
        """
roles:
  chat_default: main
rbac_default_allowed:
  employee:
    - chat_default
"""
    )
    roles_module.load_roles(path)
    with pytest.raises(roles_module.UnknownRoleError):
        roles_module.list_models_for_rbac("nonexistent_rbac_role")


def test_rbac_dedup_keeps_order():
    """RBAC 真**list 含重复 role ref** → 去重保序."""
    path = _write_yaml(
        """
roles:
  chat_default: catfish-private-main
  also_main: catfish-private-main
rbac_default_allowed:
  employee:
    - chat_default
    - also_main
    - chat_default
"""
    )
    roles_module.load_roles(path)
    # 真**去重保序** — 第一次出现保留
    assert roles_module.list_models_for_rbac("employee") == ["catfish-private-main"]


# ─── to_public_dict 真完整 payload ──────────────────────────────


def test_to_public_dict():
    path = _write_yaml(
        """
roles:
  chat_default: catfish-private-main
  rate_fast: catfish-private-main
fallback_chain:
  - chat_default
rbac_default_allowed:
  employee:
    - chat_default
"""
    )
    roles_module.load_roles(path)
    payload = roles_module.to_public_dict()
    assert payload["roles"] == {
        "chat_default": "catfish-private-main",
        "rate_fast": "catfish-private-main",
    }
    assert payload["fallback_chain"] == ["catfish-private-main"]
    assert payload["rbac_default_allowed"] == {
        "employee": ["catfish-private-main"],
    }
    assert payload["loaded_from"] == str(path)


# ─── schema 真**错** 真**raise** ────────────────────────────────


def test_load_missing_roles_section_raises():
    path = _write_yaml("not_roles:\n  foo: bar\n")
    with pytest.raises(ValueError, match="roles"):
        roles_module.load_roles(path)


def test_load_nonexistent_file_raises():
    with pytest.raises(FileNotFoundError):
        roles_module.load_roles("/nonexistent/path/roles.yaml")


def test_load_roles_not_dict_raises():
    path = _write_yaml("roles:\n  - not_a_dict\n")
    with pytest.raises(ValueError, match="roles 段"):
        roles_module.load_roles(path)


# ─── reload 真**重新加载** ──────────────────────────────────────


def test_reload_swaps_mapping():
    path = _write_yaml("roles:\n  chat_default: model_v1\n")
    roles_module.load_roles(path)
    assert roles_module.resolve("chat_default") == "model_v1"

    # 真**改 yaml + reload**
    path.write_text("roles:\n  chat_default: model_v2\n", encoding="utf-8")
    roles_module.reload_roles(path)
    assert roles_module.resolve("chat_default") == "model_v2"


# ─── 真**production roles.yaml 加载** smoke 测试 ────────────────


def test_production_roles_yaml_loads():
    """真**仓库** config/roles.yaml 真**能加载** + 真**resolve 真**预期 role."""
    # 默认 path
    roles_module.load_roles()
    # 真**核心 role 都应该存在**
    assert roles_module.resolve(roles_module.Role.CHAT_DEFAULT)
    assert roles_module.resolve(roles_module.Role.RATE_FAST)
    assert roles_module.resolve(roles_module.Role.VISION)
    assert roles_module.resolve(roles_module.Role.EMBEDDING)
    # fallback_chain 真**resolve 不 raise** (没循环)
    chain = roles_module.resolve_fallback_chain()
    assert len(chain) > 0
    # rbac employee 真有
    assert roles_module.list_models_for_rbac("employee")


# ─── Phase 7 (6/17 鸿波) — schema validation ──────────────────────


def test_rbac_stale_role_ref_raises_at_load():
    """真**rbac_default_allowed** 真**引用 yaml 真**未定义 role** → load_roles raise.

    真**production 红线**: 客户改 roles.yaml `chat_default: customer-x-main`
    真**忘改** rbac `manager: [OLD_RENAMED_ROLE, vision]` 真**OLD_RENAMED_ROLE
    真**stale** → 真**alembic migration 真**展开** 真**stale name 真**写 db**, RBAC
    真**永远 stale**.

    真**load 时 hard fail** 真**好过 silent 部署 bug**.
    """
    path = _write_yaml(
        """
roles:
  chat_default: main
  vision: vlm
rbac_default_allowed:
  employee:
    - chat_default
  manager:
    - chat_default
    - OLD_REMOVED_ROLE
    - vision
"""
    )
    with pytest.raises(ValueError, match=r"未定义 role ref.*OLD_REMOVED_ROLE"):
        roles_module.load_roles(path)


def test_rbac_multiple_stale_roles_listed_all():
    """真**多 stale** 真**一次 raise 真**列全**, 真**真**客户 真**一次改完**."""
    path = _write_yaml(
        """
roles:
  chat_default: main
rbac_default_allowed:
  employee:
    - chat_default
    - STALE_ONE
  admin:
    - STALE_TWO
    - chat_default
"""
    )
    with pytest.raises(ValueError) as excinfo:
        roles_module.load_roles(path)
    assert "STALE_ONE" in str(excinfo.value)
    assert "STALE_TWO" in str(excinfo.value)


def test_rbac_all_valid_no_raise():
    """真**rbac 真**全 valid** 真**load 真 OK** (真**P3.5.29 Phase 7 真**真**不破老 happy path**)."""
    path = _write_yaml(
        """
roles:
  chat_default: main
  vision: vlm
  summarize: long
rbac_default_allowed:
  employee:
    - chat_default
  manager:
    - chat_default
    - vision
    - summarize
"""
    )
    roles_module.load_roles(path)
    assert roles_module.list_models_for_rbac("employee") == ["main"]
    assert roles_module.list_models_for_rbac("manager") == ["main", "vlm", "long"]


def test_no_rbac_section_loads_ok():
    """真**rbac_default_allowed 真**完全缺** 真**OK** — 真**rbac 段可选** (P3.5.29 Phase 1)."""
    path = _write_yaml(
        """
roles:
  chat_default: main
"""
    )
    roles_module.load_roles(path)
    # 真**有 role 但 0 rbac**
    assert roles_module.resolve("chat_default") == "main"
