"""适配层 —— 把 hermes ToolRegistry 包装成 JSON-RPC 友好的接口。

核心两个动作:
    list_tools()         → OpenAI tool calling 兼容的 schema 数组
    dispatch_tool(...)   → 同步/异步 dispatch + 截断超大结果

为啥要适配:
    hermes 的 registry.dispatch 内部签名跟 hermes 自己的 agent context 强相关,
    我们 Companion 这边没有那个 context —— 所以这层做"最小可工作"的封装,
    把不需要 context 的 tool 直接调通,有 context 依赖的 tool 报清楚错。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import traceback
from typing import Any, Dict, List

logger = logging.getLogger("catfish.tool_bridge.adapter")

# bootstrap 后由 server 注入
_registry_module = None


def install_registry(registry_module) -> None:
    """server 启动后调一次,把 hermes 的 tools.registry 模块塞进来"""
    global _registry_module
    _registry_module = registry_module


def _r():
    """快捷拿 ToolRegistry singleton"""
    if _registry_module is None:
        raise RuntimeError("registry 还没 bootstrap — 先调 install_registry")
    return _registry_module.registry


# ============================================================
# tools/list
# ============================================================

def list_tools() -> List[Dict[str, Any]]:
    """返回 OpenAI tool calling 兼容的 tool definitions。"""
    r = _r()
    names = sorted(r.get_all_tool_names())
    out = []
    for name in names:
        try:
            entry = r.get_entry(name)
            schema = r.get_schema(name)
            toolset = r.get_toolset_for_tool(name) or ""
            # 用 toolset 维度判可用性（hermes 的 check_tool_availability
            # 返回 tuple, 不是 bool, 不能直接用）
            available = bool(r.is_toolset_available(toolset)) if toolset else True
            out.append({
                "name": name,
                "description": getattr(entry, "description", "") or "",
                "input_schema": schema or {},
                "emoji": r.get_emoji(name) or "",
                "toolset": toolset,
                "available": available,
            })
        except Exception as e:
            logger.warning("list_tools: skip %s — %s", name, e)
    return out


# ============================================================
# tools/dispatch
# ============================================================

async def dispatch_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """调用一个 tool。

    返回的字典固定 shape:
        {"ok": bool, "result": <jsonable> | None, "error": str | None,
         "tool": name, "stderr": str | None}
    """
    r = _r()
    if name not in r.get_all_tool_names():
        return {
            "ok": False, "tool": name, "result": None,
            "error": f"unknown tool: {name}",
        }

    # 用 toolset 维度判可用性 (而不是 check_tool_availability，因为它返回 tuple)
    toolset = r.get_toolset_for_tool(name)
    if toolset and not r.is_toolset_available(toolset):
        return {
            "ok": False, "tool": name, "result": None,
            "error": f"toolset '{toolset}' 在当前环境不可用（依赖未装/env 未配/平台不支持）",
        }

    try:
        dispatch_fn = r.dispatch
        if inspect.iscoroutinefunction(dispatch_fn):
            raw = await dispatch_fn(name, args)
        else:
            # to_thread 跑 sync dispatch 避免堵 asyncio loop
            raw = await asyncio.to_thread(dispatch_fn, name, args)

        # hermes 的 max_result_size 是 per-tool 配置，需要传 name
        try:
            max_size = r.get_max_result_size(name)
        except Exception:
            max_size = 100_000  # 兜底 100KB
        truncated = _truncate_for_ipc(raw, max_size)
        return {
            "ok": True, "tool": name, "result": truncated, "error": None,
        }
    except Exception as e:
        logger.exception("dispatch_tool failed: %s", name)
        return {
            "ok": False,
            "tool": name,
            "result": None,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[:2000],
        }


def _truncate_for_ipc(value: Any, max_size: int) -> Any:
    """把超大字符串/列表截断,免得 socket 一条 line 撑爆。"""
    import json
    try:
        as_json = json.dumps(value, ensure_ascii=False)
    except Exception:
        return repr(value)[:max_size]
    if len(as_json) <= max_size:
        return value
    return {
        "_truncated": True,
        "preview": as_json[:max_size],
        "original_size_bytes": len(as_json),
    }


# ============================================================
# health
# ============================================================

def health() -> Dict[str, Any]:
    r = _r()
    return {
        "ok": True,
        "tool_count": len(r.get_all_tool_names()),
        "toolsets": list(r.get_registered_toolset_names()),
    }
