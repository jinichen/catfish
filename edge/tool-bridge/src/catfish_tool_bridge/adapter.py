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

# 五一 sprint 5/3 收尾: hermes 工具响应里含 ~/.hermes/... 路径, LLM 看到困惑后
# 跟员工说"memory tool 不可用因为 hermes 没初始化, 存到 ~/.hermes/...", 暴露品牌.
#
# **不能直接屏蔽** — memory_save 是真跨 session 永久记忆能力, 屏了鲶鱼就丢这个.
# 改用响应包裹: tool 真返回原样写存储, 但给 LLM 看的 response 把 hermes 字眼 + 路径
# 都过滤掉, LLM 不再因 path 困惑.
#
# 见 dispatch_tool 处理.
#
# 这些 hermes tool 名字本身可能也得 rebrand, 但改名涉及 hermes 内部映射, 风险大,
# Phase 2 后做.

# Hermes 暴露品牌字眼的工具 — 调度后 response 走 sanitizer.
_HERMES_TOOLS_NEEDS_BRAND_SCRUB = {
    "memory",
    "memory_save",
    "memory_load",
    "memory_search",
}


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


# ── 品牌脱敏 (五一 sprint 5/3 加) ───────────────────────────────


def _scrub_brand_leaks(text: str) -> str:
    """从 tool response 文字里清掉 hermes 字眼 + 内部路径, 防 LLM 看到后向员工泄漏.

    替换 (大小写敏感, hermes 大小写都换):
      - ~/.hermes/...       → 鲶鱼本机存储
      - /Users/.../.hermes  → 鲶鱼本机存储
      - hermes / Hermes     → 鲶鱼  (注意: 工具名 hermes_xxx 不动, 只换独立词)
    """
    import re as _re
    # 先换路径 (优先级高于单词替换)
    text = _re.sub(r"~/\.hermes(/[^\s'\")]*)?", "鲶鱼本机存储", text)
    text = _re.sub(r"/Users/[^/\s]+/\.hermes(/[^\s'\")]*)?", "鲶鱼本机存储", text)
    text = _re.sub(r"/home/[^/\s]+/\.hermes(/[^\s'\")]*)?", "鲶鱼本机存储", text)
    # 再换独立的 hermes 词 (\b 词边界, 防误伤 hermes_xxx 工具名)
    text = _re.sub(r"\bhermes\b", "鲶鱼", text, flags=_re.IGNORECASE)
    return text


def scrub_brand_in_result(tool_name: str, result: Any) -> Any:
    """对会泄漏品牌字眼的 hermes tool 响应做脱敏.

    其他 tool 不动 (避免误伤 catfish_skill_install 等真路径返回).
    """
    if tool_name not in _HERMES_TOOLS_NEEDS_BRAND_SCRUB:
        return result
    if isinstance(result, str):
        return _scrub_brand_leaks(result)
    if isinstance(result, dict):
        out: Dict[str, Any] = {}
        for k, v in result.items():
            if isinstance(v, str):
                out[k] = _scrub_brand_leaks(v)
            elif isinstance(v, (dict, list)):
                out[k] = scrub_brand_in_result(tool_name, v)
            else:
                out[k] = v
        return out
    if isinstance(result, list):
        return [scrub_brand_in_result(tool_name, item) for item in result]
    return result


# ============================================================
# tools/dispatch
# ============================================================

#: execute_code 误用守卫 — sandbox 子进程拿不到 hermes session, 调 catfish_*
#: 必死锁. 这些子串只要在脚本里出现, 大概率是模型搞错 (踩过坑 2026-04-28 鸿波 demo).
_EXECUTE_CODE_FORBIDDEN_PATTERNS = (
    "catfish_browser_",
    "catfish_screenshot",
    "catfish_skill_",
    "catfish_tool_bridge",
    "import catfish_",
    "from catfish_",
)


def _check_execute_code_misuse(name: str, args: Dict[str, Any]) -> str | None:
    """检测 execute_code 沙箱误调用 catfish 工具. 命中返回 friendly error 字符串.

    返回 None = OK; 字符串 = 应该立即拒绝 + 把字符串塞进 error 字段.

    为啥拦: hermes execute_code 是 bash/python sandbox 子进程, 跟 hermes 主进程
    完全隔离, 拿不到 tool-bridge unix socket / browser session. 模型在脚本里
    `import catfish_browser_*` 或调对应函数必死锁等 30s timeout, 浪费员工时间.
    SOUL.md § execute_code 红线已经写过纪律, 这里加工程兜底.
    """
    if name not in {"execute_code", "shell_exec", "python", "bash"}:
        return None
    # 拼起来: code / command / input 等常见字段
    text_parts: list[str] = []
    for key in ("code", "command", "input", "script", "args"):
        v = args.get(key)
        if isinstance(v, str):
            text_parts.append(v)
        elif isinstance(v, list):
            text_parts.extend(str(x) for x in v if isinstance(x, str))
    text = "\n".join(text_parts).lower()
    if not text:
        return None
    hits = [p for p in _EXECUTE_CODE_FORBIDDEN_PATTERNS if p.lower() in text]
    if not hits:
        return None
    return (
        f"⚠️ {name} 沙箱里检测到 catfish 工具调用 ({hits[0]}). "
        f"这必失败 — sandbox 子进程拿不到 hermes browser session / tool-bridge socket. "
        f"请用原生 tool calling 直接调 catfish_browser_* 等, 不要写脚本调. "
        f"详见 SOUL.md § execute_code 红线."
    )


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

    # 守卫: execute_code 沙箱误调用 catfish 工具 → 立即拒绝, 不让模型死等 timeout.
    misuse_msg = _check_execute_code_misuse(name, args)
    if misuse_msg:
        return {
            "ok": False,
            "tool": name,
            "result": None,
            "error": misuse_msg,
            "stderr": None,
        }

    start = time.time()
    result = await _do_dispatch(name, args)
    latency_ms = (time.time() - start) * 1000

    # 写 audit (永远不抛, 不影响主流程返回).
    # 工具结果里 'security_audit' 字段 (例: 'credential_field_filled') 也并入 audit log
    # 让 IT 事后能 grep 谁在啥时候填了密码字段.
    audit_extra: dict[str, Any] = {}
    inner_result = result.get("result")
    if isinstance(inner_result, dict) and "security_audit" in inner_result:
        audit_extra["security_audit"] = inner_result["security_audit"]

    audit.write_event(
        tool=name,
        ok=result["ok"],
        args=args,
        error=result.get("error"),
        latency_ms=latency_ms,
        extra=audit_extra or None,
    )

    # 五一 sprint 5/3: hermes memory_* 工具响应里含 ~/.hermes/... 路径 + 'hermes'
    # 字眼, LLM 看到后向员工泄漏品牌. audit 已经写了原始 (内部审计需要), 这里只对
    # 给 LLM 看的 result 和 error 做脱敏. 其他 tool 不动.
    if name in _HERMES_TOOLS_NEEDS_BRAND_SCRUB:
        if result.get("result") is not None:
            try:
                result["result"] = scrub_brand_in_result(name, result["result"])
            except Exception:
                logger.exception("scrub_brand_in_result failed for %s — leaving raw", name)
        # 失败路径 (raise 后 _do_dispatch 把 exception message 塞 error 字段),
        # error 里也常带 ~/.hermes / hermes 字眼, 必须脱敏
        if isinstance(result.get("error"), str):
            try:
                result["error"] = _scrub_brand_leaks(result["error"])
            except Exception:
                pass
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
