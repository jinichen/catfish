"""把 `hermes approvals suggest` 接出来给 Companion 用 (P47, 8/9).

# 这是什么功能

hermes 的 `hermes_cli/approvals_suggest.py` 会挖 `~/.hermes/state.db`: 找出那些
被危险命令分类器判过、但**实际执行了**的命令 (说明员工当时批准了), 把反复出现
的模式排名, 提议加进 `command_allowlist` —— 以后同款命令不再弹审批。

它自己的 docstring 把安全姿态写得很死:

  · **Never auto-applies.** 默认只出提议, 要显式 --apply 才写
  · **Hardline 命令永不提议**  (detect_hardline_command 匹配的直接丢掉)
  · **破坏性 / 提权 / 凭据 / 混淆类永不提议** —— `rm -rf build/` 批准 100 次也
    不会变成 allowlist 条目
  · 危险根命令永不变成通配 (`rm *` / `sudo *` …)

# 本模块只做一件事: 把它接到 HTTP 上

**挖掘、排名、安全过滤、写盘全部调 hermes 自己的函数**, 一行都不重写:

    scan_approval_history()   挖 state.db
    build_proposals()         排名 + 安全过滤
    apply_proposals()         写 command_allowlist (走 tools.approval)

GET  /api/catfish/approval-suggestions        列提议
POST /api/catfish/approval-suggestions/apply  应用选中的

GET 的返回形状**逐字段对齐** hermes `--json` 的输出 (approvals_suggest.py:445),
这样两边看到的是同一个东西, 出问题好对账。

# 一处**故意跟 CLI 不一样**: 按 pattern 应用, 不按序号

hermes CLI 是 `--apply 1,3` —— 按序号。那在命令行里没问题: 你看完列表, 当场
输序号, 中间没有时间差。

UI 不是。渲染和点击之间隔着任意长的时间, 而这期间提议列表**会变** —— 新会话被
挖进来、别的地方改了 allowlist, 排名就变了。这时候按序号应用, 加进去的可能不是
员工看到的那一条。

**而这是一张安全 allowlist**: 加错一条 = 一个本该弹审批的命令从此静默放行。

所以协议改成: 客户端把**它显示过的 pattern 原文**送回来; 服务端重新算一遍提议,
**逐条核对 pattern 仍在提议集合里**才应用, 对不上的拒掉并原样报回去。
"确认你看到的那一条", 不是"确认第几行"。

这不是重写 hermes 的逻辑 —— 挖掘和安全过滤仍然全是它的; 变的只是"选哪几条"
这个交互协议, 而它本来就属于调用方。
"""
from __future__ import annotations

import logging
from typing import Any

# 双模式 import: hermes 里是包内加载 (相对), 单测里是绝对加载。
#
# 这个坑 plugin.py 的 `_import_sibling` 已经记了 —— 包名带 dash + hermes 用
# `spec_from_file_location` 装载, relative 和 absolute 会各自在一种场景下失败。
# 这里只需要两段 (plugin_route_auth 自己不引任何 sibling, 不会再套一层)。
try:
    from . import plugin_route_auth
except ImportError:  # pragma: no cover - 绝对加载路径 (单测 / 直接 import)
    import plugin_route_auth  # type: ignore[no-redef]

logger = logging.getLogger("catfish.xcatfish_user.approvals_bridge")

ROUTE_LIST = "/api/catfish/approval-suggestions"
ROUTE_APPLY = "/api/catfish/approval-suggestions/apply"

#: 扫描窗口 / 门槛的默认值 —— 跟 hermes CLI 的 default 一致 (subcommands/approvals.py)。
#: 不自己另定一套, 免得 UI 和命令行看到的结果对不上。
DEFAULT_DAYS = 90
DEFAULT_MIN_COUNT = 2
DEFAULT_LIMIT = 20

#: 参数上限 —— 防有人拿 ?limit=999999 把 state.db 扫爆。
MAX_LIMIT = 100
MAX_DAYS = 3650

#: 失败原因码 (封闭词表, 会显示到 UI)。
REASON_HERMES_MISSING = "hermes_module_missing"   # 装的 hermes 没这个功能
REASON_DB_MISSING = "session_db_missing"          # state.db 不在
REASON_SCAN_FAILED = "scan_failed"                # 挖掘本身出错 (已记日志)
REASON_NOTHING_MATCHED = "no_matching_proposal"   # 送回来的 pattern 不在提议里


def _load_hermes():
    """拿 hermes 的 approvals_suggest 模块; 拿不到返 None。"""
    try:
        from hermes_cli import approvals_suggest  # noqa: PLC0415
        return approvals_suggest
    except Exception as e:  # noqa: BLE001
        logger.warning("P47: 拿不到 hermes_cli.approvals_suggest: %s", e)
        return None


def _clamp(raw: Any, default: int, low: int, high: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, v))


def compute_proposals(
    *,
    days: int = DEFAULT_DAYS,
    min_count: int = DEFAULT_MIN_COUNT,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """算一遍提议。**永不抛。** 返回形状对齐 hermes `--json`。

        {"ok": true,  "db": "...", "days": 90, "proposals": [
            {"n": 1, "pattern": "...", "kind": "glob|class",
             "count": 3, "classes": [...], "examples": [...]}, ...]}
        {"ok": false, "reason": "<REASON_*>", "proposals": []}
    """
    mod = _load_hermes()
    if mod is None:
        return {"ok": False, "reason": REASON_HERMES_MISSING, "proposals": []}

    try:
        db_path = mod.default_db_path()
        if not db_path.exists():
            return {"ok": False, "reason": REASON_DB_MISSING, "proposals": []}

        import tools.approval as approval_module  # noqa: PLC0415

        existing = set(approval_module.load_permanent_allowlist())
        records = mod.scan_approval_history(db_path, days=days)
        proposals = mod.build_proposals(
            records, existing=existing, min_count=min_count, limit=limit,
        )
        return {
            "ok": True,
            "db": str(db_path),
            "days": days,
            "existing_allowlist_size": len(existing),
            "proposals": [
                {
                    "n": i,
                    "pattern": p.pattern,
                    "kind": p.kind,
                    "count": p.count,
                    "classes": sorted(p.classes),
                    "examples": list(p.examples),
                }
                for i, p in enumerate(proposals, 1)
            ],
        }
    except Exception:  # noqa: BLE001
        logger.exception("P47 compute_proposals 出错")
        return {"ok": False, "reason": REASON_SCAN_FAILED, "proposals": []}


def apply_patterns(
    patterns: list[str],
    *,
    days: int = DEFAULT_DAYS,
    min_count: int = DEFAULT_MIN_COUNT,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """把指定 pattern 加进 command_allowlist。**永不抛。**

    先重算一遍提议, 只应用**仍在提议集合里**的 pattern —— 见模块顶部"按 pattern
    不按序号"那段。对不上的原样报回 `rejected`, 不静默忽略: 员工点了三条只进了
    两条, 必须让他知道是哪一条没进、为什么。
    """
    mod = _load_hermes()
    if mod is None:
        return {"ok": False, "reason": REASON_HERMES_MISSING, "applied": [], "rejected": []}

    wanted = [p.strip() for p in (patterns or []) if isinstance(p, str) and p.strip()]
    if not wanted:
        return {"ok": False, "reason": REASON_NOTHING_MATCHED, "applied": [], "rejected": []}

    try:
        fresh = compute_proposals(days=days, min_count=min_count, limit=limit)
        if not fresh.get("ok"):
            return {**fresh, "applied": [], "rejected": wanted}

        by_pattern = {p["pattern"]: p["n"] - 1 for p in fresh["proposals"]}
        indices: list[int] = []
        applied: list[str] = []
        rejected: list[str] = []
        for pat in wanted:
            idx = by_pattern.get(pat)
            if idx is None:
                rejected.append(pat)
                continue
            if idx not in indices:
                indices.append(idx)
                applied.append(pat)

        if not indices:
            logger.warning(
                "P47: 送回来的 %d 条 pattern 一条都不在当前提议里 —— "
                "多半是列表在渲染之后变了 (新会话被挖进来 / allowlist 已改)",
                len(wanted),
            )
            return {
                "ok": False, "reason": REASON_NOTHING_MATCHED,
                "applied": [], "rejected": rejected,
            }

        # 重算出来的 Proposal 对象列表 —— apply_proposals 要的是它, 不是我们的 dict
        db_path = mod.default_db_path()
        import tools.approval as approval_module  # noqa: PLC0415

        existing = set(approval_module.load_permanent_allowlist())
        records = mod.scan_approval_history(db_path, days=days)
        objs = mod.build_proposals(
            records, existing=existing, min_count=min_count, limit=limit,
        )
        merged = mod.apply_proposals(objs, indices)

        logger.info("P47 已加进 command_allowlist: %s (现共 %d 条)", applied, len(merged))
        return {
            "ok": True,
            "applied": applied,
            "rejected": rejected,
            "allowlist_size": len(merged),
        }
    except Exception:  # noqa: BLE001
        logger.exception("P47 apply_patterns 出错")
        return {"ok": False, "reason": REASON_SCAN_FAILED, "applied": [], "rejected": wanted}


def register_routes(router: Any) -> bool:
    """挂两个端点。跟 P44 同时机 (Application.__init__, router 未 freeze)。"""
    from aiohttp import web as _w  # noqa: PLC0415

    async def _list(request):
        denied = plugin_route_auth.check_auth(request)
        if denied is not None:
            return denied
        q = request.query
        return _w.json_response(compute_proposals(
            days=_clamp(q.get("days"), DEFAULT_DAYS, 0, MAX_DAYS),
            min_count=_clamp(q.get("min_count"), DEFAULT_MIN_COUNT, 1, 1000),
            limit=_clamp(q.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT),
        ))

    async def _apply(request):
        denied = plugin_route_auth.check_auth(request)
        if denied is not None:
            return denied
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _w.json_response(
                {"ok": False, "reason": "bad_request", "applied": [], "rejected": []},
                status=400,
            )
        patterns = body.get("patterns") if isinstance(body, dict) else None
        if not isinstance(patterns, list):
            return _w.json_response(
                {"ok": False, "reason": "bad_request", "applied": [], "rejected": []},
                status=400,
            )
        return _w.json_response(apply_patterns(patterns))

    try:
        router.add_get(ROUTE_LIST, _list)
        router.add_post(ROUTE_APPLY, _apply)
        logger.info("P47 routes registered ✓ (%s + /apply)", ROUTE_LIST)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("P47 路由注册失败: %s", e)
        return False
