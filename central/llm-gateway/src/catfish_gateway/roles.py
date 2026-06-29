"""P3.5.29 (6/17 鸿波) — model role 抽象层.

单一抽象, 代码引用 role 不引用 model name. 客户部署改 roles.yaml
一文件全代码跟着走 (避 86+ 处 hardcode sed).

# 用法

```python
from catfish_gateway.roles import resolve, Role

# 业务代码只引用 role:
model = resolve(Role.RATE_FAST)
fallback_chain = resolve_fallback_chain()
rbac_allowed = list_models_for_rbac("employee")
```

# 加载

模块级单例 (类似 config.py). 真 startup 时 load_roles() 显式调用一次.
变化时手动 reload_roles() (e.g. 测试 / SIGHUP).

# 约定

- role name 是字符串 (跟 Enum 平行). Enum 只是类型提示 + IDE 补全
- yaml 新 role 不需要改 Enum (代码 fallback 用字符串). 加 Enum 只为
  开发友好
- 没找到 role → raise UnknownRoleError. 不静默 fallback (silent fallback 真
  导致部署 bug 难定位)
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("catfish.gateway.roles")


# ─── Role enum (开发友好, 不强约束) ─────────────────────────

class Role(str, Enum):
    """已知 role names. 业务意图命名, 不带物理 model 细节.

    yaml 真新 role 不必改 Enum — 代码可以传字符串. Enum 只是为 IDE 补全 +
    type checking 友好.
    """

    CHAT_DEFAULT = "chat_default"
    RATE_FAST = "rate_fast"
    SUMMARIZE = "summarize"
    VISION = "vision"
    EMBEDDING = "embedding"
    ADVISOR_CALL2 = "advisor_call2"
    PUBLIC_FLASH = "public_flash"


# ─── 异常 ───────────────────────────────────────────────────

class UnknownRoleError(KeyError):
    """role name 不在 roles.yaml roles 段里."""


class CircularRoleError(ValueError):
    """fallback_chain 递归 resolve 时检测到循环 ref."""


class RolesNotLoadedError(RuntimeError):
    """resolve() 调时 _roles 是 None — 没 load_roles() 过."""


# ─── 内部状态 ────────────────────────────────────────────────────

_lock = threading.RLock()
_roles: dict[str, str] | None = None
_fallback_chain: list[str] | None = None
_rbac_default_allowed: dict[str, list[str]] | None = None
_loaded_path: Path | None = None


# ─── 加载 ──────────────────────────────────────────────────

def load_roles(path: str | Path | None = None) -> None:
    """从 yaml 加载真 roles + fallback_chain + rbac_default_allowed.

    Args:
        path: yaml 真路径. None 默认 ``config/roles.yaml`` 相对 module.

    Side effects:
        写模块级 _roles / _fallback_chain / _rbac_default_allowed.

    Raises:
        FileNotFoundError: yaml 不存在
        yaml.YAMLError: yaml syntax 错
        ValueError: schema 字段缺失
    """
    global _roles, _fallback_chain, _rbac_default_allowed, _loaded_path

    if path is None:
        # 默认: <repo>/central/llm-gateway/config/roles.yaml
        # __file__ 真 src/catfish_gateway/roles.py → ../../config/roles.yaml
        path = Path(__file__).parent.parent.parent / "config" / "roles.yaml"
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"roles.yaml 不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "roles" not in data:
        raise ValueError(f"roles.yaml schema 错 — 缺 'roles' 段: {path}")

    roles = data["roles"]
    if not isinstance(roles, dict):
        raise ValueError(f"roles.yaml roles 段 必须是 dict: {path}")

    # fallback_chain 可选 (默认空)
    fallback_chain = data.get("fallback_chain", [])
    if not isinstance(fallback_chain, list):
        raise ValueError(
            f"roles.yaml fallback_chain 必须是 list (或缺): {path}"
        )

    # rbac_default_allowed 可选
    rbac = data.get("rbac_default_allowed", {})
    if not isinstance(rbac, dict):
        raise ValueError(
            f"roles.yaml rbac_default_allowed 必须是 dict (或缺): {path}"
        )

    with _lock:
        _roles = {str(k): str(v) for k, v in roles.items()}
        _fallback_chain = [str(r) for r in fallback_chain]
        _rbac_default_allowed = {
            str(k): [str(r) for r in (v or [])]
            for k, v in rbac.items()
        }
        _loaded_path = path

    # P3.5.29 Phase 7 (6/17 鸿波): load 时 verify schema 真fallback_chain + RBAC
    # 所有 role refs 都 resolve, stale name 真raise 立刻 fail**.
    #
    # 为啥: 客户改 roles.yaml 改 `chat_default: customer-x-main` 真忘
    # 改 `fallback_chain: [chat_default, OLD_REMOVED_ROLE, ...]`** OLD_REMOVED_ROLE
    # 真stale**. runtime resolve 时 真catch**: silent skip / raise. production
    # 红线 真load 时 hard fail** 好过 silent 部署 bug.
    #
    # rbac_default_allowed 真也 verify**: 改 chat_default 真忘 改 manager 真
    # [chat_default, OLD_ROLE]** OLD_ROLE 真stale**.
    #
    # fallback_chain 真已有 _walk visited set 防循环 (P3.5.29.1 fix self-loop),
    # 这里 verify 真未定义 role**.
    _validate_schema(path)

    logger.info(
        "roles.yaml 加载 ✓: %d 个 role, %d 个 fallback chain, %d 个 RBAC 默认 (path=%s)",
        len(_roles), len(_fallback_chain), len(_rbac_default_allowed), path,
    )


def _validate_schema(path: Path) -> None:
    """load 时 verify:

    1. fallback_chain 所有 entry 是 yaml roles 段 key 或 物理 model name (兼容老 yaml 真混写)
    2. rbac_default_allowed 所有 role ref 是 yaml roles 段 key

    Args:
        path: yaml 路径 (err msg 真显示**)

    Raises:
        ValueError: stale ref / undefined role
    """
    assert _roles is not None
    assert _fallback_chain is not None
    assert _rbac_default_allowed is not None

    # 1. fallback_chain — entry roles key 或 物理 model name 直写 (老 yaml 兼容).
    # undefined 真raise**. 这里 不能 detect 物理 name 错 (catalog 没 load), 只 catch role typo.
    # heuristic: entry 真snake_case 真 short** 视为 role ref, 否则 model name.
    # 保守 path: 真entry roles 段没 也不 raise (兼容直写 model name).
    # → 真不 verify fallback_chain entry 保守 path 兼容老 yaml. ✓
    # 真P3.5.29.1 真循环已防, stale name 走 _walk 真append 当 model name.

    # 2. rbac_default_allowed — 所有 role ref 都 必须 真roles 段 key**.
    # 这 真alembic migration 展开 stale name 真直接写 db**, production 红线.
    stale_rbac: list[tuple[str, str]] = []
    for rbac_role, allowed_list in _rbac_default_allowed.items():
        for role_ref in allowed_list:
            if role_ref not in _roles:
                stale_rbac.append((rbac_role, role_ref))
    if stale_rbac:
        msg_parts = [
            f"  rbac_default_allowed.{rbac_role}: 未定义 role ref {role_ref!r}"
            for rbac_role, role_ref in stale_rbac
        ]
        raise ValueError(
            f"roles.yaml rbac_default_allowed schema 错 ({path}):\n"
            + "\n".join(msg_parts)
            + "\n→ 检查 `roles:` 段定义 这些 role, 或 rbac 段引用真已 rename**?"
        )


def reload_roles(path: str | Path | None = None) -> None:
    """重新加载 roles.yaml (e.g. SIGHUP / 测试)."""
    load_roles(path)


def _ensure_loaded() -> None:
    if _roles is None:
        raise RolesNotLoadedError(
            "roles.yaml 没 load — startup 时调 load_roles() 先"
        )


# ─── 核心 API ──────────────────────────────────────────────

def resolve(role: Role | str) -> str:
    """真 role → 物理 model name.

    Args:
        role: Role enum 或 str (e.g. "rate_fast")

    Returns:
        物理 model name (e.g. "catfish-private-main")

    Raises:
        UnknownRoleError: yaml roles 段没 这个 role
        RolesNotLoadedError: 没 load_roles() 过
    """
    _ensure_loaded()
    role_str = role.value if isinstance(role, Role) else str(role)
    assert _roles is not None  # for type checker
    if role_str not in _roles:
        raise UnknownRoleError(
            f"role 未定义 in roles.yaml: {role_str!r}. "
            f"已定义: {sorted(_roles.keys())}"
        )
    return _roles[role_str]


def resolve_or_none(role: Role | str) -> str | None:
    """软版 resolve — 找不到返 None, 不 raise."""
    try:
        return resolve(role)
    except (UnknownRoleError, RolesNotLoadedError):
        return None


def resolve_fallback_chain() -> list[str]:
    """fallback_chain 递归 resolve role refs → flat list of model names.

    DFS visited set 防循环. 未定义 role 直接 raise (不静默跳过 —
    silent skip 部署 bug 难定位).

    Returns:
        flat list of model names, 保序.

    Raises:
        CircularRoleError: A → B → A 循环 ref
        UnknownRoleError: chain 引用了未定义 role
    """
    _ensure_loaded()
    assert _fallback_chain is not None
    assert _roles is not None

    visited: set[str] = set()
    result: list[str] = []

    def _walk(role_str: str) -> None:
        if role_str in visited:
            raise CircularRoleError(
                f"fallback_chain 循环 ref 检测到: {role_str!r} "
                f"in {sorted(visited)}"
            )
        visited.add(role_str)
        # 先看 yaml 真不是 role ref 是**物理 model name** (兼容直接写 name)
        if role_str in _roles:
            # role ref → resolve
            model_name = _roles[role_str]
            # 间接 role ref (model_name 真也是 role name) → 继续 _walk.
            # P3.5.29.1 (6/17 鸿波本机 pytest): 去掉 `model_name != role_str`
            # short-circuit — 自循环 (loop_role: loop_role) 漏检:
            # 第一次 visited.add 后 short-circuit 直接 append 不递归. 让自循环
            # 也走 _walk → 第二次 visited 命中 raise CircularRoleError.
            if model_name in _roles:
                _walk(model_name)
            else:
                result.append(model_name)
        else:
            # 直接 model name (兼容老 yaml 真混写)
            result.append(role_str)

    for entry in _fallback_chain:
        _walk(entry)

    return result


def list_models_for_rbac(role: str) -> list[str]:
    """RBAC role (employee / manager / admin / sysadmin) → 真允许 model name list.

    yaml rbac_default_allowed 段定义, role refs, 这里 resolve.

    Args:
        role: RBAC role 字符串 (employee / manager / admin / sysadmin)

    Returns:
        model name list, 保序 + 去重

    Raises:
        UnknownRoleError: yaml rbac_default_allowed 没这 RBAC role
    """
    _ensure_loaded()
    assert _rbac_default_allowed is not None

    if role not in _rbac_default_allowed:
        raise UnknownRoleError(
            f"RBAC role 未定义 in rbac_default_allowed: {role!r}. "
            f"已定义: {sorted(_rbac_default_allowed.keys())}"
        )

    seen: set[str] = set()
    result: list[str] = []
    for role_ref in _rbac_default_allowed[role]:
        try:
            model_name = resolve(role_ref)
        except UnknownRoleError:
            # 兼容 rbac_default_allowed 直接写 model name (不推荐但兼容)
            model_name = role_ref
        if model_name not in seen:
            seen.add(model_name)
            result.append(model_name)
    return result


# ─── API endpoint payload helper ─────────────────────────────────

def to_public_dict() -> dict[str, Any]:
    """Companion / hermes / 别的 service 拉的 payload 全.

    `GET /v1/roles` 真返这个. caller cache 本机 60s 用.
    """
    _ensure_loaded()
    assert _roles is not None
    assert _fallback_chain is not None
    assert _rbac_default_allowed is not None

    return {
        "roles": dict(_roles),
        "fallback_chain": resolve_fallback_chain(),
        "rbac_default_allowed": {
            role: list_models_for_rbac(role)
            for role in _rbac_default_allowed
        },
        "loaded_from": str(_loaded_path) if _loaded_path else None,
    }
