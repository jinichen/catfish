"""配额配置 —— 数据类 + quotas.yaml 的读写与增删改。

2026-08-15 从 quota.py 切出来。纯搬迁, 逻辑一行未改。

这里是"配额定多少"; "用了多少"在 quota_audit.py, "还能不能用"在 quota.py。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

import logging

logger = logging.getLogger("catfish.gateway.quota")

from .quota_base import _quota_config_path, _YAML_RT

@dataclass
class UserQuota:
    tokens_per_minute: int = 100_000
    tokens_per_day: int = 1_000_000


@dataclass
class ModelQuota:
    tokens_per_day: int = 0  # 0 = 不限


@dataclass
class DepartmentQuota:
    tokens_per_day: int = 0  # 0 = 不限


@dataclass
class QuotaConfig:
    """所有 quota 配置 (defaults + overrides 合并)."""

    default_user: UserQuota
    user_overrides: dict[str, UserQuota]  # email → UserQuota
    model_quotas: dict[str, ModelQuota]   # model_name → ModelQuota
    department_quotas: dict[str, DepartmentQuota]  # dept_name → DeptQuota

    def per_user_for(self, email: str) -> UserQuota:
        return self.user_overrides.get(email, self.default_user)

    def per_model_for(self, model: str) -> ModelQuota:
        return self.model_quotas.get(model, ModelQuota(tokens_per_day=0))

    def per_department_for(self, dept: str) -> DepartmentQuota:
        return self.department_quotas.get(dept, DepartmentQuota(tokens_per_day=0))


# P3.5.93 (6/23 鸿波): 全 quotas.yaml CRUD helpers.
#
# 设计:
#   - 公共: _load_yaml_rt() 读 + _write_yaml_rt(data) 写, 保 comments + key 序
#   - update_default_per_user / put_per_model / put_per_department / put_user_override /
#     put_dept_override + 对应 delete + get_full_config_dict
#   - 共用 _ensure_dict(parent, key) 处理 yaml `key:` 后只有 comment 时 None 的坑
#     (老 update_department_quota 5/2 注释里踩过)
#   - 失败一律返 (False, "原因"), 调用方决定 raise / log
#
# 老接口: update_department_quota(name, tokens_per_day) 保留, 内部转 put_dept_override.
# 跟之前 BL-RBAC-DAY7 时代 manager UI 行为一致 (manager 改 dept = 写 override 不是
# default). 老 endpoint /api/quota/department/{dept} 仍走这个.


def _load_yaml_rt() -> Any:
    """读 quotas.yaml 走 ruamel round_trip. 文件不存在返空 CommentedMap.

    返 ruamel CommentedMap (dict 子类) — 直接当 dict 用, 修改后 _write_yaml_rt
    会保 comments. 不要拿这个返值跑 yaml.safe_dump, 会丢 comments.
    """
    p = _quota_config_path()
    if not p.exists():
        return _YAML_RT.load("{}\n") or {}
    try:
        with p.open(encoding="utf-8") as f:
            data = _YAML_RT.load(f)
        return data if data is not None else _YAML_RT.load("{}\n")
    except Exception as e:
        logger.warning("_load_yaml_rt: 解析 %s 失败 (返空): %s", p, e)
        return _YAML_RT.load("{}\n")


def _write_yaml_rt(data: Any) -> tuple[bool, str]:
    """写回 quotas.yaml. 保 comments + 顺序 (ruamel round_trip)."""
    p = _quota_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("w", encoding="utf-8") as f:
            _YAML_RT.dump(data, f)
        return True, ""
    except Exception as e:
        msg = f"写 quotas.yaml 失败: {e}"
        logger.warning("_write_yaml_rt: %s (%s)", msg, p)
        return False, msg


def _ensure_dict(parent: Any, key: str) -> Any:
    """parent[key] 如不是 dict (含 None / 缺失), 创建空 dict 写回 parent.

    yaml 里 `key:` 后只有 comment 或空时 safe_load/round_trip_load 返 None,
    setdefault 不会替换 None. 老 update_department_quota 5/2 注释里踩过坑.
    """
    existing = parent.get(key)
    if not isinstance(existing, dict):
        # ruamel CommentedMap 也是 dict 子类
        from ruamel.yaml.comments import CommentedMap
        new_map = CommentedMap()
        parent[key] = new_map
        return new_map
    return existing


def get_full_config_dict() -> dict[str, Any]:
    """返当前 quotas.yaml 完整 dict (defaults + overrides), 给 admin UI 读.

    返普通 dict (不是 CommentedMap, 避免 JSON 序列化奇怪), 失了 comments 但
    UI 不需要. 内部值仍是 ruamel 类型, FastAPI/pydantic 会 serialize.
    """
    data = _load_yaml_rt()
    # 转纯 dict 给 JSON
    return _deep_to_plain(data)


def _deep_to_plain(obj: Any) -> Any:
    """递归把 ruamel CommentedMap / CommentedSeq 转纯 dict / list (JSON 友好)."""
    if isinstance(obj, dict):
        return {k: _deep_to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_to_plain(v) for v in obj]
    return obj


def update_default_per_user(
    tokens_per_minute: int, tokens_per_day: int,
) -> tuple[bool, str]:
    """改全员默认 per_user quota (defaults.per_user)."""
    if tokens_per_minute < 0 or tokens_per_day < 0:
        return False, "tokens_per_minute / tokens_per_day 不能负"
    data = _load_yaml_rt()
    defaults = _ensure_dict(data, "defaults")
    per_user = _ensure_dict(defaults, "per_user")
    per_user["tokens_per_minute"] = int(tokens_per_minute)
    per_user["tokens_per_day"] = int(tokens_per_day)
    ok, msg = _write_yaml_rt(data)
    if ok:
        logger.info(
            "update_default_per_user: tokens_per_minute=%d tokens_per_day=%d",
            tokens_per_minute, tokens_per_day,
        )
    return ok, msg


def put_per_model(name: str, tokens_per_day: int) -> tuple[bool, str]:
    """加/改单 model quota (defaults.per_model.<name>)."""
    if not name.strip():
        return False, "model name 不能空"
    if tokens_per_day < 0:
        return False, "tokens_per_day 不能负"
    data = _load_yaml_rt()
    defaults = _ensure_dict(data, "defaults")
    per_model = _ensure_dict(defaults, "per_model")
    from ruamel.yaml.comments import CommentedMap
    entry = CommentedMap()
    entry["tokens_per_day"] = int(tokens_per_day)
    per_model[name] = entry
    ok, msg = _write_yaml_rt(data)
    if ok:
        logger.info("put_per_model: %s tokens_per_day=%d", name, tokens_per_day)
    return ok, msg


def delete_per_model(name: str) -> tuple[bool, str]:
    """删 model quota (defaults.per_model.<name>). 不存在算成功 (idempotent)."""
    if not name.strip():
        return False, "model name 不能空"
    data = _load_yaml_rt()
    defaults = data.get("defaults")
    if isinstance(defaults, dict):
        per_model = defaults.get("per_model")
        if isinstance(per_model, dict) and name in per_model:
            del per_model[name]
            ok, msg = _write_yaml_rt(data)
            if ok:
                logger.info("delete_per_model: %s 删了", name)
            return ok, msg
    return True, ""  # 不存在 = idempotent OK


def put_per_department(name: str, tokens_per_day: int) -> tuple[bool, str]:
    """加/改单部门默认 quota (defaults.per_department.<name>).

    跟 put_dept_override 区别: 这是 defaults 节, override 是 overrides 节.
    overrides 优先级高 (load_quota_config 后写覆盖 default).
    """
    if not name.strip():
        return False, "department 不能空"
    if tokens_per_day < 0:
        return False, "tokens_per_day 不能负"
    data = _load_yaml_rt()
    defaults = _ensure_dict(data, "defaults")
    per_dept = _ensure_dict(defaults, "per_department")
    from ruamel.yaml.comments import CommentedMap
    entry = CommentedMap()
    entry["tokens_per_day"] = int(tokens_per_day)
    per_dept[name] = entry
    ok, msg = _write_yaml_rt(data)
    if ok:
        logger.info("put_per_department: %s tokens_per_day=%d", name, tokens_per_day)
    return ok, msg


def delete_per_department(name: str) -> tuple[bool, str]:
    """删部门默认 quota (defaults.per_department.<name>). Idempotent."""
    if not name.strip():
        return False, "department 不能空"
    data = _load_yaml_rt()
    defaults = data.get("defaults")
    if isinstance(defaults, dict):
        per_dept = defaults.get("per_department")
        if isinstance(per_dept, dict) and name in per_dept:
            del per_dept[name]
            ok, msg = _write_yaml_rt(data)
            if ok:
                logger.info("delete_per_department: %s 删了", name)
            return ok, msg
    return True, ""


def put_user_override(
    email: str, tokens_per_minute: int, tokens_per_day: int,
) -> tuple[bool, str]:
    """加/改用户 override (overrides.users.<email>)."""
    if not email.strip() or "@" not in email:
        return False, "email 格式不对"
    if tokens_per_minute < 0 or tokens_per_day < 0:
        return False, "tokens 不能负"
    data = _load_yaml_rt()
    overrides = _ensure_dict(data, "overrides")
    users = _ensure_dict(overrides, "users")
    from ruamel.yaml.comments import CommentedMap
    entry = CommentedMap()
    entry["tokens_per_minute"] = int(tokens_per_minute)
    entry["tokens_per_day"] = int(tokens_per_day)
    users[email] = entry
    ok, msg = _write_yaml_rt(data)
    if ok:
        logger.info(
            "put_user_override: %s tokens_per_minute=%d tokens_per_day=%d",
            email, tokens_per_minute, tokens_per_day,
        )
    return ok, msg


def delete_user_override(email: str) -> tuple[bool, str]:
    """删用户 override (overrides.users.<email>). Idempotent."""
    if not email.strip():
        return False, "email 不能空"
    data = _load_yaml_rt()
    overrides = data.get("overrides")
    if isinstance(overrides, dict):
        users = overrides.get("users")
        if isinstance(users, dict) and email in users:
            del users[email]
            ok, msg = _write_yaml_rt(data)
            if ok:
                logger.info("delete_user_override: %s 删了", email)
            return ok, msg
    return True, ""


def put_dept_override(name: str, tokens_per_day: int) -> tuple[bool, str]:
    """加/改部门 override (overrides.departments.<name>). 优先级高于 defaults."""
    if not name.strip():
        return False, "department 不能空"
    if tokens_per_day < 0:
        return False, "tokens_per_day 不能负"
    data = _load_yaml_rt()
    overrides = _ensure_dict(data, "overrides")
    depts = _ensure_dict(overrides, "departments")
    from ruamel.yaml.comments import CommentedMap
    entry = CommentedMap()
    entry["tokens_per_day"] = int(tokens_per_day)
    depts[name] = entry
    ok, msg = _write_yaml_rt(data)
    if ok:
        logger.info("put_dept_override: %s tokens_per_day=%d", name, tokens_per_day)
    return ok, msg


def delete_dept_override(name: str) -> tuple[bool, str]:
    """删部门 override (overrides.departments.<name>). Idempotent."""
    if not name.strip():
        return False, "department 不能空"
    data = _load_yaml_rt()
    overrides = data.get("overrides")
    if isinstance(overrides, dict):
        depts = overrides.get("departments")
        if isinstance(depts, dict) and name in depts:
            del depts[name]
            ok, msg = _write_yaml_rt(data)
            if ok:
                logger.info("delete_dept_override: %s 删了", name)
            return ok, msg
    return True, ""


# ── 老接口 (5/2 BL-D9, /api/quota/department/{dept} PUT 用) ────
#
# 保留以兼容现有 endpoint. 内部转 put_dept_override (写 overrides.departments).
# 行为不变 — manager UI 改部门 = 写 override.


def update_department_quota(department: str, tokens_per_day: int) -> bool:
    """改部门 quota → overrides.departments.<dept>. 真路径走 put_dept_override.

    跟 5/2 老 BL-D9 实现行为一致 (写 override 节). 返 bool 而不是 (ok, msg)
    保 API 兼容 — /api/quota/department/{dept} PUT endpoint 现在仍这签名.
    """
    ok, msg = put_dept_override(department, tokens_per_day)
    if not ok:
        logger.warning("update_department_quota 失败: %s", msg)
    return ok


def load_quota_config(path: Path | None = None) -> QuotaConfig:
    """从 yaml 加载. 文件不存在走全部默认 (per-user 限, model/dept 不限)."""
    p = path or _quota_config_path()
    if not p.exists():
        logger.info("quotas.yaml 不存在 (%s), 走全部默认", p)
        return QuotaConfig(
            default_user=UserQuota(),
            user_overrides={},
            model_quotas={},
            department_quotas={},
        )

    try:
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("quotas.yaml 解析失败 %s, 走默认: %s", p, e)
        return QuotaConfig(
            default_user=UserQuota(),
            user_overrides={},
            model_quotas={},
            department_quotas={},
        )

    defaults = data.get("defaults", {}) or {}
    overrides = data.get("overrides", {}) or {}

    # default_user
    pu = defaults.get("per_user", {}) or {}
    default_user = UserQuota(
        tokens_per_minute=int(pu.get("tokens_per_minute", 100_000)),
        tokens_per_day=int(pu.get("tokens_per_day", 1_000_000)),
    )

    # model quotas
    pm = defaults.get("per_model", {}) or {}
    model_quotas: dict[str, ModelQuota] = {}
    for model_name, cfg in pm.items():
        if not isinstance(cfg, dict):
            continue
        model_quotas[model_name] = ModelQuota(
            tokens_per_day=int(cfg.get("tokens_per_day", 0)),
        )

    # department quotas
    pd = defaults.get("per_department", {}) or {}
    department_quotas: dict[str, DepartmentQuota] = {}
    for dept_name, cfg in pd.items():
        if not isinstance(cfg, dict):
            continue
        department_quotas[dept_name] = DepartmentQuota(
            tokens_per_day=int(cfg.get("tokens_per_day", 0)),
        )

    # overrides
    user_overrides: dict[str, UserQuota] = {}
    for email, cfg in (overrides.get("users", {}) or {}).items():
        if not isinstance(cfg, dict):
            continue
        user_overrides[email] = UserQuota(
            tokens_per_minute=int(cfg.get("tokens_per_minute", default_user.tokens_per_minute)),
            tokens_per_day=int(cfg.get("tokens_per_day", default_user.tokens_per_day)),
        )

    for dept_name, cfg in (overrides.get("departments", {}) or {}).items():
        if not isinstance(cfg, dict):
            continue
        department_quotas[dept_name] = DepartmentQuota(
            tokens_per_day=int(cfg.get("tokens_per_day", 0)),
        )

    return QuotaConfig(
        default_user=default_user,
        user_overrides=user_overrides,
        model_quotas=model_quotas,
        department_quotas=department_quotas,
    )


# ── sqlite store ────────────────────────────────────────────
