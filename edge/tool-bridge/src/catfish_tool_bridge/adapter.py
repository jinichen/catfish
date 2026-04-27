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

import time

from . import audit, catfish_tools, skill_watcher

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
    """返回 OpenAI tool calling 兼容的 tool definitions。

    输出顺序: catfish 原生 tools 排前面 (优先曝光给 LLM, prompt 里它们更早被
    扫到), 然后是 hermes 的 builtin tools 按字母序。
    """
    r = _r()
    out: List[Dict[str, Any]] = list(catfish_tools.CATFISH_NATIVE_TOOLS)

    names = sorted(r.get_all_tool_names())
    for name in names:
        # 防止重名 —— 极端情况下 catfish 想 "覆写" hermes 的 tool
        if catfish_tools.is_native(name):
            continue
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

    每次调用末尾会写一行 audit 事件到 ~/.hermes/.catfish_audit.jsonl
    (用于 Skill lifecycle 阶段 4 健康面板 / 30 天提醒 / 失败率告警 / billing).
    audit 写失败不影响 dispatch 主流程.
    """
    # 给 skill_watcher 标记"现在 LLM 在繁忙地用工具", 防它在 LLM 调用循环中突然
    # 重启 tool-bridge. 这是廉价操作 (一次 lock + 时间戳更新)。
    skill_watcher.mark_dispatch()

    start = time.time()
    result = await _do_dispatch(name, args)
    latency_ms = (time.time() - start) * 1000

    # 写 audit (永远不抛, 不影响主流程返回)
    audit.write_event(
        tool=name,
        ok=result["ok"],
        args=args,
        error=result.get("error"),
        latency_ms=latency_ms,
    )
    return result


async def _do_dispatch(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """实际 dispatch 逻辑. 抽出来让 dispatch_tool 可以包 audit."""
    # 先看 catfish 原生 tool —— 这些不走 hermes registry, 也不要求 toolset
    # 可用性检查 (它们就是 catfish 自己的代码, 一定在)
    if catfish_tools.is_native(name):
        try:
            raw = await asyncio.to_thread(catfish_tools.dispatch_native, name, args)
            return {"ok": True, "tool": name, "result": raw, "error": None}
        except Exception as e:
            logger.exception("native dispatch failed: %s", name)
            return {
                "ok": False, "tool": name, "result": None,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[:2000],
            }

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
        "tool_count": len(r.get_all_tool_names()) + len(catfish_tools.CATFISH_NATIVE_TOOLS),
        "native_tool_count": len(catfish_tools.CATFISH_NATIVE_TOOLS),
        "toolsets": list(r.get_registered_toolset_names()) + ["catfish_native"],
    }
