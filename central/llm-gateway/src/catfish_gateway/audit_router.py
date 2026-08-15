"""审计查询路由 —— 从 app.py 拆出 (8/15)。

/api/audit/me · /me/perf · /global · /global/perf · /department/{d} · /events

app.py 3881 行是 CLAUDE.md §1 红线的 4.8 倍。8/1 那次已经把模型配置管理搬去
admin_models_router.py, 并在文件里留了话:「app.py 本身仍远超红线, 需要一轮
专门的拆分」。这是那一轮的第一刀。

跟着搬的还有 `_require_admin` —— app.py 里只有本组这几个路由用它
(扫过全部顶层函数确认)。注意 facts_router / advisory_router 各自也有一份
同名函数, **不是重复实现**: 逻辑都是一行 `user.is_admin()`, 但 403 的文案
各写各的领域 ("不能访问全局聚合" / "不能访问 FACT 系统" / "不能 publish
advisory")。合并会把这层信息抹平, 所以维持现状。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, Header, HTTPException

from .auth import User, get_current_user, resolve_effective_user_email



# 这几个是**无状态**的守卫 / body 模型 —— 只有路由本身需要 app,
# 它们不需要, 所以留在模块级。放进 register() 里就成了局部定义,
# 别的模块 import 不到 (quota_router 要引 audit_router._require_admin)。
def _require_admin(user: User) -> None:
    if not user.is_admin():
        raise HTTPException(
            status_code=403,
            detail=f"role={user.role} 不能访问全局聚合 (admin only)",
        )


def register_audit_routes(app) -> None:
    """把 audit 这一组路由挂到 app 上。

    用 register(app) 而不是 APIRouter —— 跟 admin_models_router /
    admin_providers_router 同一套 (app.py:621 那段注释定的规矩:
    "路由要挂在同一个 app 上, 没法用 from X import *, 所以改用显式 register")。
    """
    @app.get("/api/audit/me")
    async def api_audit_me(
        user: User = Depends(get_current_user),
        x_catfish_user: str | None = Header(default=None, alias="X-Catfish-User"),
    ) -> dict[str, Any]:
        """员工自查: 中央对我存了啥 metadata. 不需 RBAC, 谁登录返谁的.

        BL-AUTH-DECOUPLE-A1-API-ME-FIX (6/1): 跟 /api/me + /api/quota/me 同款 resolve.
        chat 写 quota_events 用 effective_user_email (真员工 chenhongbo@ffcs.cn),
        audit_summary_user_since 查也要按 effective 查, 才能找回真员工 audit 记录.
        """
        from . import quota

        from .db import fetch_user_metadata

        effective_email = resolve_effective_user_email(user, x_catfish_user)

        # 查真员工 department (同 /api/me 处理) — service token 时 user.department 是
        # service 维度 (infra), 真员工 department 在 identity users 表.
        real_meta = await fetch_user_metadata(effective_email)
        real_department = real_meta["department"] if real_meta else user.department

        now_ms = int(time.time() * 1000)
        day_cutoff = now_ms - 86_400_000

        summary = quota.audit_summary_user_since(effective_email, day_cutoff)

        # 6/2 BL-PRIVACY-CARD-QUOTA-PROGRESS (鸿波 6/2 凌晨): PrivacyCard 把"今天用了
        # 7.99M"改成 quota 进度条. 这里加 quota_day_limit 字段, 客户端就能渲染
        # 已用/上限 = 百分比. /api/quota/me 早已返这字段, 但 PrivacyCard 调的是
        # /api/audit/me — 不给员工拼两个 API, 直接在这条加上.
        # tokens_per_day=0 表示该 user 不限 (yaml overrides 没配 → 走 default_user 1M).
        quota_cfg = quota.load_quota_config()
        user_q = quota_cfg.per_user_for(effective_email)
        quota_day_limit = user_q.tokens_per_day  # 0 = 不限

        return {
            "user_email": effective_email,
            "department": real_department,
            "since_ms": day_cutoff,
            # 让客户端知道"中央存的字段长这样", 防员工担心还有别的没暴露
            "schema_note": "本端点只返 metadata: count / tokens / model / 时间戳. 中央不存 prompt / response 文本.",
            "quota_day_limit": quota_day_limit,
            **summary,  # request_count / total_tokens / by_model / first_seen_ts / last_seen_ts
        }
    # P3.5.59 Phase 2 (6/22 鸿波 catch "是不是应该把中央端完成"):
    # 单员工 LLM perf 聚合 — 走 gateway_audit 表 (含 latency_ms / ttft_ms).
    # 跟 /api/audit/me 区别: /api/audit/me 走 quota_events 表 (没 latency),
    # 本 endpoint 走 gateway_audit 表 (有 latency / ttft / status).
    # Companion PerfCard LLM section 调这个拿真 latency 分位.
    @app.get("/api/audit/me/perf")
    async def api_audit_me_perf(
        hours: int = 24,
        user: User = Depends(get_current_user),
        x_catfish_user: str | None = Header(default=None, alias="X-Catfish-User"),
    ) -> dict[str, Any]:
        """单员工 LLM perf 聚合 — Companion PerfCard 用.

        返字段: request_count / ok_count / error_count / total_tokens /
        latency_p50/p95/p99_ms / ttft_p50/p95_ms / by_model / source.

        Privacy: 全 metadata, 跟 /api/audit/me 同合同, 中央不返 prompt/response.
        """
        from . import metrics as _metrics

        effective_email = resolve_effective_user_email(user, x_catfish_user)
        hours = max(1, min(720, hours))  # 1h - 30d
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - hours * 3_600_000
        summary = _metrics.query_perf_summary_user(effective_email, cutoff_ms)
        return {
            "user_email": effective_email,
            "since_ms": cutoff_ms,
            "window_hours": hours,
            "schema_note": (
                "本端点只返 latency / token / model metadata. "
                "中央不存 prompt / response 文本. source=pg|jsonl|none 标数据源."
            ),
            **summary,
        }
    @app.get("/api/audit/department/{department}")
    async def api_audit_department(
        department: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """部门级 audit 聚合 — manager 看本部门员工总用量分布."""
        from . import quota  # 懒 import (audit 数据从 quota_events sqlite 也能算)

        if not user.can_manage_department(department):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"role={user.role} 无权访问部门 {department} 的 audit. "
                    f"managed_departments={user.managed_departments}"
                ),
            )

        now_ms = int(time.time() * 1000)
        day_cutoff = now_ms - 86_400_000

        summary = quota.audit_summary_dept_since(department, day_cutoff)
        return {
            "department": department,
            "since_ms": day_cutoff,
            **summary,  # request_count / total_tokens / by_model / by_user
            "viewer_role": user.role,
        }
    @app.get("/api/audit/global")
    async def api_audit_global(
        user: User = Depends(get_current_user),
        since_hours: int = 24,
        model: str | None = None,
        dept: str | None = None,
        user_email: str | None = None,
    ) -> dict[str, Any]:
        """全员 audit 聚合 — admin 看请求总数 / 模型分布 / 部门分布 / top 员工.

        BL-AUDIT-UX-P1 (5/17): 加 since_hours 时间窗 + 上期对照.
        BL-AUDIT-UX-P2 (5/17): 加 drill-down filter (model/dept/user_email).
          点 audit 页某行 → 前端把该值塞进 URL query, /api/audit/global 收到后
          把 SQL 加 WHERE. 上期 trend 跟当前期同 filter 才有意义.

          since_hours: 1-720 (1 小时-30 天), 默认 24h.
          model: catalog ID (例 'catfish-public-nvidia-nemotron'), None=全部
          dept: 部门名 (含 '(未分组)' 合成桶), None=全部
          user_email: 员工 email (含 '(未分组员工)' 合成桶), None=全部
        """
        from . import quota
        _require_admin(user)

        # 钳到合理范围: 1 小时 - 30 天
        hours = max(1, min(720, int(since_hours)))
        window_ms = hours * 3_600_000

        now_ms = int(time.time() * 1000)
        period_start_ms = now_ms - window_ms
        prev_period_start_ms = period_start_ms - window_ms

        # 空字符串当 None 处理 — 前端 /api/audit/global?model=&dept=eng 这种半填的也兼容
        filter_kwargs = {
            "filter_model": model or None,
            "filter_dept": dept or None,
            "filter_user": user_email or None,
        }

        summary = quota.audit_summary_global_since(period_start_ms, **filter_kwargs)
        prev = quota.audit_period_totals(
            prev_period_start_ms, period_start_ms, **filter_kwargs,
        )

        return {
            "since_ms": period_start_ms,
            "since_hours": hours,
            **summary,
            # BL-AUDIT-UX-P1: 上期对照, 给前端做 trend ↑12% / ↓8% 用
            "previous_request_count": prev["request_count"],
            "previous_total_tokens": prev["total_tokens"],
            "previous_active_users": prev["active_users"],
            "previous_active_departments": prev["active_departments"],
            # BL-AUDIT-UX-P2: 把当前 filter echo 回前端, 显示 pill 用
            "filter": {
                "model": filter_kwargs["filter_model"],
                "dept": filter_kwargs["filter_dept"],
                "user_email": filter_kwargs["filter_user"],
            },
            "viewer_role": user.role,
        }
    # P3.5.60 (6/22 鸿波 catch "继续完成"): 全公司 LLM perf 聚合 endpoint —
    # admin 看全公司 latency p50/p95/p99 + by_model + by_department.
    # 跟 /api/audit/global 区别: 那个走 quota_events (无 latency), 这个走 gateway_audit
    # (有 latency_ms / ttft_ms). web /admin/perf 页用这条.
    @app.get("/api/audit/global/perf")
    async def api_audit_global_perf(
        user: User = Depends(get_current_user),
        since_hours: int = 24,
        model: str | None = None,
        dept: str | None = None,
    ) -> dict[str, Any]:
        """全公司 LLM perf 聚合 — admin only.

        返字段: request_count / ok_count / error_count / total_tokens /
        active_users / active_departments / latency_p50/p95/p99_ms /
        ttft_p50/p95_ms / by_model (含 p50/p99 per model) / by_department
        (含 p50/p99 per dept) / source.

        Privacy: metadata only, 合同跟 /api/audit/global 一致.
        """
        from . import metrics as _metrics
        _require_admin(user)

        hours = max(1, min(720, int(since_hours)))
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - hours * 3_600_000

        summary = _metrics.query_perf_summary_global(
            cutoff_ms,
            model_filter=(model or None),
            dept_filter=(dept or None),
        )

        return {
            "since_ms": cutoff_ms,
            "since_hours": hours,
            "filter": {"model": model or None, "dept": dept or None},
            "viewer_role": user.role,
            "schema_note": (
                "本端点只返 metadata: latency / token / model / department / count. "
                "中央不存 prompt / response 文本. source=pg|jsonl|none 标数据源."
            ),
            **summary,
        }
    @app.get("/api/audit/events")
    async def api_audit_events(
        user: User = Depends(get_current_user),
        since_ms: int | None = None,
        dept: str | None = None,
        user_filter: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """BL-ADMIN-AUDIT (5/12 鸿波): 全员 audit 逐条历史 + 4 维度筛选 + 分页.

        给 catfish-web /admin/quota/events 页用. RBAC 严格 admin only (sysadmin 走
        User.is_admin() 通过).

        Args:
            since_ms: 只看 ts_ms >= 这个的 (默认 24h 前)
            dept: department 过滤
            user_filter: user_email 过滤 (param 名 user_filter 避开跟 user dependency 撞)
            model: model 过滤
            status: 'ok' / 'error' / 'interrupted_resumed' (BL-HERMES013-4)
            limit: 1-200, 默认 50
            offset: ≥0, 默认 0

        Returns:
            {events: [...], total: int, limit, offset, since_ms, viewer_role}
        """
        from . import metrics as _metrics
        _require_admin(user)

        # since_ms 默认 24h 前
        if since_ms is None:
            since_ms = int(time.time() * 1000) - 86_400_000
        since_unix = since_ms // 1000

        # 防御 limit / offset 边界
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))

        events = _metrics.read_events(
            since_unix=since_unix,
            user_filter=user_filter or None,
            model_filter=model or None,
            status_filter=status or None,
            dept_filter=dept or None,
            limit=limit,
            offset=offset,
        )
        total = _metrics.count_events(
            since_unix=since_unix,
            user_filter=user_filter or None,
            model_filter=model or None,
            status_filter=status or None,
            dept_filter=dept or None,
        )

        return {
            "events": events,
            "total": total,
            "limit": limit,
            "offset": offset,
            "since_ms": since_ms,
            "filters": {
                "dept": dept or "",
                "user": user_filter or "",
                "model": model or "",
                "status": status or "",
            },
            "viewer_role": user.role,
        }
