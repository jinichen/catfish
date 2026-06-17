"""Build the /v1/catalog response -- rich model info for Hermes UI.

匿名调用（user=None）是被允许的：catalog 暴露的字段都是公开信息
（display_name / tier / 能力开关 / 状态标志），不含 api_base / UUID / key，
所以让 Hermes 在第一次启动、还没填 token 的时候也能调这个接口列模型。

登录调用会拿到额外字段（authenticated=true），未来 RBAC 上线后
登录态才能看到"你部门的默认模型""你的权限范围"之类。

状态字段（每个模型）：
    - api_key_configured: bool   API key 环境变量有没有值
    - is_reachable: bool|None    上游是否能 TCP 通（启动时探了缓存到 app.state）
                                 None 表示 gateway 还没探（首次冷启动 race）
    - status_reason: str         人话解释，例如 "经代理 http://127.0.0.1:7890"
                                 / "外网模型，代理不可达" / "API key 未配"

前端据此画 3 状态：
    ✓ 绿  api_key_configured=True  AND is_reachable=True
    ⚠ 黄  api_key_configured=True  AND is_reachable!=True
    ○ 灰  api_key_configured=False
"""

from __future__ import annotations

from typing import Any

from .auth import User
from .config import Config


def build_catalog(
    config: Config,
    user: User | None,
    upstream_status: dict[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    """组装 Hermes / Companion 用的模型选择清单。

    user=None 表示匿名浏览。
    upstream_status 来自 app.state.upstream_status；缺省时 is_reachable=None。
    """
    upstream_status = upstream_status or {}
    models: list[dict[str, Any]] = []
    default: str | None = None

    for m in config.models:
        if user is not None and not user.can_access(m):
            continue
        if m.mode == "embedding":
            # embedding 是管道类，不给员工选
            continue

        # —— 三态状态字段 ——
        api_key_configured = bool(m.upstream.is_available)
        cached = upstream_status.get(m.name) or {}
        is_reachable: bool | None = (
            bool(cached["reachable"]) if "reachable" in cached else None
        )
        reason: str = str(cached.get("reason") or "")
        if not api_key_configured:
            # 没配 key 时 reason 用统一文案，比缓存里"API key 未配"更准
            reason = "API key 未配（编辑 .env 加 key）"

        models.append(
            {
                "id": m.name,
                "display_name": m.display_name,
                "tier": m.tier,
                "recommended_for": m.recommended_for,
                "context_window": m.context_window,
                "cost_tier": m.cost_tier,
                "supports_tool_use": m.supports_tool_use,
                "supports_vision": m.supports_vision,
                # —— 状态三态 ——
                "api_key_configured": api_key_configured,
                "is_reachable": is_reachable,
                "status_reason": reason,
            }
        )
        # 默认模型的选择：仅从配了 key 的里挑
        if m.default and api_key_configured and default is None:
            default = m.name

    # P3.5.29 Phase 6 (6/17 鸿波) — 真**role_resolver("chat_default") 真优先 override**.
    # 真**客户改 roles.yaml chat_default → 全代码跟着走** (含 Companion picker default,
    # chat.ts store init, 前端 useCatalog hook). 真**前端 0 改动**, 真**catalog
    # default 已经被 chat.ts:27 / store init 真兜底**.
    #
    # 真**fallback chain**:
    #   1. role_resolver("chat_default") 真**存在 catalog 真 api_key_configured** → 用
    #   2. m.default (models.yaml `default: true`) 老路径 → 用
    #   3. 第一个 api_key_configured → 用
    #   4. models[0] 兜底
    #
    # 真**roles 没 load**: roles_module.resolve_or_none 真**返 None**, 真**走 2-4 老路径**.
    role_default: str | None = None
    try:
        from . import roles as roles_module
        role_default = roles_module.resolve_or_none("chat_default")
    except Exception:
        role_default = None

    if role_default:
        for entry in models:
            if entry["id"] == role_default and entry["api_key_configured"]:
                default = role_default
                break

    if default is None and models:
        # 兜底：选第一个 api_key_configured 的，全没配则选第一个
        for entry in models:
            if entry["api_key_configured"]:
                default = entry["id"]
                break
        if default is None:
            default = models[0]["id"]

    return {
        "authenticated": user is not None,
        "models": models,
        "default": default,
        # P1：上线 RBAC 后，登录态才有部门默认；匿名态回默认同一个值。
        "your_dept_default": default,
    }
