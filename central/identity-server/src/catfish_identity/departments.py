"""Departments store — per-dept RBAC (allowed_models / quota).

BL-RBAC-DAY3A (5/17 凌晨, 原 5/16 sprint plan 推迟):
跟 users 表分开维护. 客户接入时一次性配 4-10 个部门, 之后基本不变.
admin 后台 (catfish-web /admin/access) 才会改, 不是高频写.

设计:
  departments.allowed_models = JSONB []
    空 [] = 全允许 (开放默认, 安全开发期用)
    [m1, m2] = 只允许这俩 (政企客户落地常态)

  users.allowed_models = JSONB NULL (默认)
    NULL = 继承 dept 设置
    [] = 用户级 override 解锁 (e.g. 部门只 deepseek 但某员工特批用全部)
    [m1] = 用户级 override 收紧 (e.g. 默认全允许但实习生只 flash)

合并决议: get_effective_allowed_models(user) →
  user.allowed_models if user.allowed_models is not None
  else dept.allowed_models if dept else []

返回的 [] 上层 (gateway) 解读为"全允许".
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("catfish.identity.departments")


@dataclass
class Department:
    """部门 RBAC 配置. 跟 IdentityUser 同设计, dataclass + dict 序列化."""

    name: str
    allowed_models: list[str] = field(default_factory=list)
    quota_models_day: int = 0  # 0 = 不限 (BL-F14 兼容)
    description: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    # BL-RBAC-DAY4 (5/17): per-dept allowed_tools 白名单 (gateway tools_sanitizer 过滤).
    # [] = 全允许 (开放默认); [t1, t2] = 只这俩 + ALWAYS_ON_TOOLS 兜底.
    allowed_tools: list[str] = field(default_factory=list)

    def is_open(self) -> bool:
        """allowed_models 为空 = 全允许 (开放默认)."""
        return not self.allowed_models

    def is_tools_open(self) -> bool:
        """allowed_tools 为空 = 全允许."""
        return not self.allowed_tools

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "allowed_models": self.allowed_models,
            "quota_models_day": self.quota_models_day,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "allowed_tools": self.allowed_tools,
        }


class DepartmentRegistry:
    """Async PG-backed registry. yaml fallback 不实现 — departments 表才装 (5/17),
    yaml 时代不存在这个概念, 直接 PG only.

    跟 UserRegistry 同样 lazy_loaded singleton 模式.
    """

    def __init__(self) -> None:
        self._loaded = False
        self._cache: dict[str, Department] = {}

    async def load_from_pg(self) -> bool:
        """从 departments 表加载到内存. 失败返 False, 不抛 (上层 fallback 空 dict)."""
        from .db import get_pool  # noqa: PLC0415  延迟 import 防循环

        pool = await get_pool()
        if pool is None:
            return False
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT name, allowed_models, quota_models_day, description, "
                    "created_at, updated_at, "
                    # BL-RBAC-DAY4 (5/17): allowed_tools 加入
                    "allowed_tools "
                    "FROM departments"
                )
        except Exception as e:
            logger.warning("PG departments 加载失败: %s", e)
            return False

        loaded: dict[str, Department] = {}
        for row in rows:
            am = row["allowed_models"]
            if isinstance(am, str):
                try:
                    am = json.loads(am)
                except Exception:
                    am = []
            if not isinstance(am, list):
                am = []
            # BL-RBAC-DAY4: allowed_tools 解析 (跟 allowed_models 同套路)
            at = row.get("allowed_tools")
            if isinstance(at, str):
                try:
                    at = json.loads(at)
                except Exception:
                    at = []
            if not isinstance(at, list):
                at = []
            loaded[row["name"]] = Department(
                name=row["name"],
                allowed_models=[str(m) for m in am],
                quota_models_day=int(row["quota_models_day"] or 0),
                description=row["description"] or "",
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                updated_at=row["updated_at"].isoformat() if row.get("updated_at") else None,
                allowed_tools=[str(t) for t in at],
            )
        self._cache = loaded
        self._loaded = True
        logger.info("PG departments 加载: %d 个部门", len(loaded))
        return True

    async def get(self, name: str) -> Optional[Department]:
        """按名拿. 未加载 → 先 load_from_pg."""
        if not self._loaded:
            await self.load_from_pg()
        return self._cache.get(name)

    async def list_all(self) -> list[Department]:
        if not self._loaded:
            await self.load_from_pg()
        return list(self._cache.values())


# Singleton (gateway / identity admin 共享)
_global_registry: DepartmentRegistry | None = None


def get_global_registry() -> DepartmentRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = DepartmentRegistry()
    return _global_registry


async def get_effective_allowed_models(
    user_email: str,
    user_allowed_models: list[str] | None,
    user_department: str,
) -> list[str]:
    """合并 user.allowed_models + dept.allowed_models → 最终允许模型列表.

    决议规则:
      1. user.allowed_models is not None → 用 user 的 (override)
      2. user.allowed_models is None → 继承 dept.allowed_models
      3. dept 不存在 → 返 [] (上层 gateway 解读为"全允许")

    返 []  = 全允许. 返 [m1, m2] = 只这俩.
    """
    if user_allowed_models is not None:
        # 用户级 override (空 list 也算 override = 解锁)
        return user_allowed_models

    if not user_department:
        # 没 department → 全允许 (开发期 / 兼容老 yaml 用户没 department 字段)
        return []

    dept = await get_global_registry().get(user_department)
    if dept is None:
        logger.warning(
            "user %s 的 department=%r 不在 departments 表, fallback 全允许",
            user_email, user_department,
        )
        return []
    return dept.allowed_models


async def get_effective_allowed_tools(
    user_email: str,
    user_allowed_tools: list[str] | None,
    user_department: str,
) -> list[str]:
    """BL-RBAC-DAY4 (5/17): 合并 user.allowed_tools + dept.allowed_tools.

    决议规则跟 get_effective_allowed_models 完全一致:
      1. user.allowed_tools is not None → 用 user 的 (override)
      2. user.allowed_tools is None → 继承 dept.allowed_tools
      3. dept 不存在 → 返 [] (上层 gateway 解读为"全允许")

    返 [] = 全允许. 返 [t1, t2] = 只这俩 + gateway 端 ALWAYS_ON_TOOLS 兜底.
    """
    if user_allowed_tools is not None:
        return user_allowed_tools

    if not user_department:
        return []

    dept = await get_global_registry().get(user_department)
    if dept is None:
        logger.warning(
            "user %s 的 department=%r 不在 departments 表, tools fallback 全允许",
            user_email, user_department,
        )
        return []
    return dept.allowed_tools
