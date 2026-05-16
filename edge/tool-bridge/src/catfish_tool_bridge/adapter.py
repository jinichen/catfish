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
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import time

from . import audit, catfish_tools, sandbox, skill_watcher

logger = logging.getLogger("catfish.tool_bridge.adapter")

# bootstrap 后由 server 注入
_registry_module = None


# ============================================================
# BL-MEMORY-BRIDGE-STORE (5/16 鸿波 6h 实盘排查终点)
# ============================================================
# hermes 0.13 memory 工具 (tools/memory_tool.py:478) 要 dispatch 时 kw['store']
# 注入一个 MemoryStore 实例. hermes CLI 走 AIAgent 注入, catfish tool-bridge
# 是 stateless dispatch 一直没注入 → 工具返
#   '{"error": "Memory is not available. ...", "success": false}'
# 现象: ~/.hermes/MEMORY.md / USER.md 长期不更新 (USER.md 最后 5/3, 那时还有别的
# 路径偶尔触发过). 5/16 鸿波在 6h 长摸排里逐层排除模型/sanitizer/dispatch/SOUL
# 后定位.
#
# 修法: tool-bridge 启动后 lazy 构造一个 MemoryStore singleton, load_from_disk()
# 读现有 entries, dispatch memory 时塞 kw['store']=store. 一次到位, hermes 升级
# 不破 (MemoryStore signature 简单 + 默认值).
#
# 并发: hermes CLI 自己也是单 AIAgent 单 store, 我们仿同样的 in-memory + 文件锁
# 模型, 不比 hermes 更不安全.
# ============================================================

_memory_store_cache: Any = None
_memory_store_init_failed = False

# ============================================================
# BL-TODO-BRIDGE-STORE (5/16 sprint follow-up)
# ============================================================
# hermes 0.13 todo_tool 同 memory_tool 模式 — handler 要 kw['store']=TodoStore.
# 设计上 per-AIAgent / per-session (TodoStore 是纯 in-memory 不持久化), 所以
# catfish 这边按 session_id 路由. session_id 缺失 → 走 __default__ 全局
# singleton (兼容老客户端不传 session_id).
#
# LRU 简化: 上限 50 个 session 防 OOM. catfish 单员工本机, 实际峰值不会到.
# ============================================================

_TODO_STORE_DEFAULT_KEY = "__default__"
_TODO_STORE_MAX_SESSIONS = 50
_todo_store_cache: Dict[str, Any] = {}
_todo_store_init_failed = False


def _todo_persist_dir() -> Path:
    """BL-TODO-STORE-PERSIST: 持久化目录 ~/.catfish/todo_store/. 不存在自动建."""
    d = Path.home() / ".catfish" / "todo_store"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _todo_persist_path(session_id: str) -> Path:
    """每 session 一个 json 文件. session_id 含特殊字符 safe — 沙箱级危险不存在,
    catfish session_id 由我们自家 sessions.rs 生成, 是 timestamp+hash 格式 safe."""
    return _todo_persist_dir() / f"{session_id}.json"


def _load_todo_store_from_disk(session_id: str, store: Any) -> None:
    """从盘读 items 灌进新建的 TodoStore. 失败 silent (空 store 是合理 fallback)."""
    path = _todo_persist_path(session_id)
    if not path.exists():
        return
    try:
        import json  # noqa: PLC0415
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # TodoStore._items 直接 list, 简单赋值
        if isinstance(data, list):
            store._items = data
            logger.info(
                "BL-TODO-STORE-PERSIST: load session=%r %d items from disk",
                session_id, len(data),
            )
    except Exception as e:
        logger.warning(
            "BL-TODO-STORE-PERSIST: load session=%r 失败 (盘文件可能 corrupt): %s",
            session_id, e,
        )


def _persist_todo_store(session_id: str, store: Any) -> None:
    """dispatch 后写盘. 失败 silent (in-memory 仍 OK, 下次重启丢但不阻塞当前调用)."""
    try:
        import json  # noqa: PLC0415
        items = getattr(store, "_items", None)
        if items is None:
            return
        path = _todo_persist_path(session_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(
            "BL-TODO-STORE-PERSIST: persist session=%r 失败: %s",
            session_id, e,
        )


def _get_todo_store(session_id: Optional[str]):
    """Per-session lazy TodoStore. session_id 缺失 → __default__ key 全局共享.

    BL-TODO-STORE-PERSIST (5/16): 新建时先看盘, 有 ~/.catfish/todo_store/<sid>.json
    就反加载. dispatch 后由 _dispatch_via_hermes_registry 调 _persist_todo_store 写盘.

    简化 LRU: 超 50 session 时随机 evict 一个 oldest key (dict insertion order).
    Evict 时同步删盘文件 (避免无限增长).
    """
    global _todo_store_init_failed
    if _todo_store_init_failed:
        return None

    key = session_id or _TODO_STORE_DEFAULT_KEY
    if key in _todo_store_cache:
        return _todo_store_cache[key]

    # LRU evict
    if len(_todo_store_cache) >= _TODO_STORE_MAX_SESSIONS:
        oldest_key = next(iter(_todo_store_cache))
        _todo_store_cache.pop(oldest_key, None)
        # 同步删盘 (防累积)
        try:
            _todo_persist_path(oldest_key).unlink(missing_ok=True)
        except Exception:
            pass
        logger.info(
            "BL-TODO-BRIDGE-STORE: cache 满, evict session=%r (含盘文件)", oldest_key,
        )

    try:
        from tools.todo_tool import TodoStore  # noqa: PLC0415
        store = TodoStore()
        _load_todo_store_from_disk(key, store)  # 反加载
        _todo_store_cache[key] = store
        logger.info(
            "BL-TODO-BRIDGE-STORE: 新建 TodoStore session=%r (cache 大小=%d, 已加载 %d items)",
            key, len(_todo_store_cache), len(getattr(store, "_items", [])),
        )
        return store
    except Exception as e:
        logger.exception(
            "BL-TODO-BRIDGE-STORE: TodoStore 初始化失败, todo 工具将持续返 disabled: %s",
            e,
        )
        _todo_store_init_failed = True
        return None


def _read_hermes_memory_config() -> dict:
    """读 ~/.hermes/config.yaml 的 memory 段. 缺失 / 解析失败返空 dict.

    返回的 dict 用 MemoryStore 默认值兜底 (memory_char_limit=2200, user_char_limit=1375).
    """
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415  # 延迟 import, 避免影响 tool-bridge 启动速度
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("memory") or {}
    except Exception as e:
        logger.warning(
            "BL-MEMORY-BRIDGE-STORE: 读 ~/.hermes/config.yaml memory 段失败, "
            "用 hermes 默认 char_limit: %s",
            e,
        )
        return {}


def _get_memory_store():
    """Lazy + cache hermes MemoryStore singleton.

    第一次调用时构造 + load_from_disk(), 后续返同一实例.
    任何步骤失败 → 标 init_failed, 一直返 None (不反复重试免刷 log).
    """
    global _memory_store_cache, _memory_store_init_failed
    if _memory_store_cache is not None:
        return _memory_store_cache
    if _memory_store_init_failed:
        return None

    try:
        # hermes-agent 已经在 sys.path 里 (bootstrap.bootstrap() 启动时塞的)
        from tools.memory_tool import MemoryStore  # noqa: PLC0415

        cfg = _read_hermes_memory_config()
        store = MemoryStore(
            memory_char_limit=int(cfg.get("memory_char_limit", 2200)),
            user_char_limit=int(cfg.get("user_char_limit", 1375)),
        )
        store.load_from_disk()
        _memory_store_cache = store
        logger.info(
            "BL-MEMORY-BRIDGE-STORE: hermes MemoryStore 初始化成功 "
            "(mem_limit=%d user_limit=%d, mem_entries=%d user_entries=%d)",
            store.memory_char_limit,
            store.user_char_limit,
            len(store.memory_entries),
            len(store.user_entries),
        )
        return store
    except Exception as e:
        logger.exception(
            "BL-MEMORY-BRIDGE-STORE: MemoryStore 初始化失败, "
            "memory tool 将持续返 disabled. 错: %s",
            e,
        )
        _memory_store_init_failed = True
        return None



def install_registry(registry_module) -> None:
    """server 启动后调一次,把 hermes 的 tools.registry 模块塞进来"""
    global _registry_module
    _registry_module = registry_module


def _r():
    """快捷拿 ToolRegistry singleton"""
    if _registry_module is None:
        raise RuntimeError("registry 还没 bootstrap — 先调 install_registry")
    return _registry_module.registry


# ── BL-FIX-MCP-SHORTNAME (5/12 鸿波 Companion 截图) ─────────────────
#
# LLM 调短名 'local_search' (从 SOUL.md 学的), 实际全名 'mcp_catfish_local_search_local_search'.
# dispatch 加 fallback: 短名 → 唯一长名命中改派, 歧义/无命中返友好 error 含候选.

def _resolve_mcp_short_name(short: str, all_names) -> str | None:
    """短名 → 唯一全名 (mcp_<server>_<short>). 多候选 / 无命中返 None.

    匹配规则: 全名以 '_' + short 结尾且以 'mcp_' 开头.
    """
    if not short or "_" in short[:4]:  # 已经是全名 (mcp_xxx) / 不像短名
        # 但还是检查精确匹配 (短名跟某个全名完全相等的极端情况让上层 fallback 走)
        return None
    suffix = "_" + short
    matches = [
        n for n in all_names
        if isinstance(n, str) and n.startswith("mcp_") and n.endswith(suffix)
    ]
    if len(matches) == 1:
        return matches[0]
    return None  # 0 或 ≥2 候选都不猜


def _suggest_mcp_full_names(short: str, all_names) -> list[str]:
    """unknown tool error 时给 LLM 提示用. 返**所有**后缀匹配的 mcp_ 全名."""
    if not short:
        return []
    suffix = "_" + short
    return sorted(
        n for n in all_names
        if isinstance(n, str) and n.startswith("mcp_") and n.endswith(suffix)
    )


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


# ============================================================
# BL-MM3 (5/7): hermes memory_save 包一层版本化
# ============================================================
#
# 背景: hermes memory_save 默认是 silent overwrite — 同 name 第二次写直接覆盖,
#       LLM 看不到旧值, 跨 session 永久记忆"改不删, 留版本"纪律 (BL-MM1) 落不下来.
#
# 方案: read-modify-write 双调用模拟版本数组 — 在 dispatch 层包一层 wrapper:
#       1. 先 memory_recall 取旧值
#       2. 拼新 content + inline 旧值备注 ("---\n_(BL-MM3 上次值, 已废: ...)_)
#       3. 调真 hermes memory_save 写
#       4. 返字段对齐 BL-MM2 (previous_value / overwrite / no_change / summary),
#          让 SOUL § BL-MM1 "回员工时主动 quote 旧值" 纪律生效
#
# 兼容:
#   - hermes 0.10/0.11/0.12 memory_save 接口都是 (name, content), 无变化
#   - read 失败 (memory_recall 抛 / 不可用) → 兜底按"首次记"处理, 不阻塞写
#   - 同值再写 → 仍调 memory_save (因为 hermes 可能有 ts 元数据), 但不加 inline 备注
#
# 跟 BL-MM2 (catfish_remember) 区别:
#   BL-MM2 → 当前 session 内事实 (~/.catfish/session_facts.json), 真存版本数组
#   BL-MM3 → 跨 session 永久记忆 (~/.hermes/memories/*.md), 单 .md 文件 + inline 备注
#   两个一起用 = "session 内硬事实 + 跨 session 偏好" 双层版本化记忆.

# inline 备注前缀, 测试 / verify 用同一个常量
_MM3_INLINE_PREFIX = "_(BL-MM3 上次值, 已废"
_MM3_INLINE_MAX_OLD_LEN = 200  # 旧值塞 inline 时截到这么长


def _extract_recall_text(raw: Any) -> str:
    """从 hermes memory_recall 返回里提取文本.

    hermes 0.10-0.12 memory_recall 返回 shape 不固定:
      - dict: {"content": "..."} / {"text": "..."} / {"value": "..."}
      - list: [{"content": "..."}, ...]  (取第一个的 content)
      - str:  直接是文本
      - None / 空 dict: 没找到
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for k in ("content", "text", "value", "result"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ""
    if isinstance(raw, list):
        if not raw:
            return ""
        first = raw[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return _extract_recall_text(first)
        return ""
    return ""


def _strip_old_inline_block(content: str) -> str:
    """剥掉上一轮 BL-MM3 加的 inline 备注块, 取出"真新值".

    每次 versioned save 都会拼上 `\\n\\n---\\n_(BL-MM3 上次值...)_` 备注.
    下次再写时, 我们 read 回来的 old_text 是"含上轮备注的版本",
    要把上轮备注剥掉, 不然 inline 块会越叠越长.
    """
    if not content:
        return content
    marker = f"\n\n---\n{_MM3_INLINE_PREFIX}"
    idx = content.find(marker)
    if idx == -1:
        return content
    return content[:idx].rstrip()


async def _memory_save_versioned(args: Dict[str, Any]) -> Dict[str, Any]:
    """BL-MM3: hermes memory_save 包一层版本化.

    返回标准 dispatch shape: {"ok", "tool", "result", "error"}.

    args 兼容两种字段名:
      hermes 原生: {"name": "...", "content": "..."}
      catfish 习惯: {"key": "...", "value": "..."} (兼容旧测试)
    """
    name = (args.get("name") or args.get("key") or "").strip()
    new_content = args.get("content") if args.get("content") is not None else args.get("value")
    if isinstance(new_content, str):
        new_content = new_content.strip()
    if not name:
        return {
            "ok": False, "tool": "memory_save", "result": None,
            "error": "memory_save 需要 name (或 key) — BL-MM3 wrapper 拿不到 name 没法 read 旧值",
        }
    if not new_content or not isinstance(new_content, str):
        return {
            "ok": False, "tool": "memory_save", "result": None,
            "error": "memory_save 需要 content (或 value) 字符串",
        }

    r = _r()

    # 1. read 旧值 (失败兜底: 按"首次记"处理, 不阻塞 write)
    old_text_raw = ""
    read_failed = False
    try:
        if "memory_recall" in r.get_all_tool_names():
            disp = r.dispatch
            if inspect.iscoroutinefunction(disp):
                old_resp = await disp("memory_recall", {"query": name})
            else:
                old_resp = await asyncio.to_thread(disp, "memory_recall", {"query": name})
            old_text_raw = _extract_recall_text(old_resp)
    except Exception as e:
        logger.info("memory_save_versioned: read 旧值失败 (%s), 按首次记兜底", e)
        read_failed = True
        old_text_raw = ""

    # 剥掉上一轮的 inline 备注块, 拿到"上一轮的真新值"
    old_text = _strip_old_inline_block(old_text_raw)

    # 2. 拼新 content
    is_overwrite = bool(old_text)
    no_change = is_overwrite and old_text.strip() == new_content.strip()
    if is_overwrite and not no_change:
        prev_truncated = old_text[:_MM3_INLINE_MAX_OLD_LEN].replace("\n", " ")
        ellipsis = "…" if len(old_text) > _MM3_INLINE_MAX_OLD_LEN else ""
        wrapped_content = (
            f"{new_content}\n\n"
            f"---\n"
            f"{_MM3_INLINE_PREFIX}, ts={time.strftime('%Y-%m-%d %H:%M')}: "
            f"{prev_truncated}{ellipsis})_"
        )
    else:
        # 首次 / 同值 → 不加 inline 块
        wrapped_content = new_content

    # 3. 调真 hermes memory_save (传 name + content, 兼容历史 args 里的额外字段)
    save_args = {k: v for k, v in args.items() if k not in ("key", "value")}
    save_args["name"] = name
    save_args["content"] = wrapped_content
    try:
        disp = r.dispatch
        if inspect.iscoroutinefunction(disp):
            raw_save = await disp("memory_save", save_args)
        else:
            raw_save = await asyncio.to_thread(disp, "memory_save", save_args)
    except Exception as e:
        logger.exception("memory_save_versioned: write 失败 (name=%s)", name)
        return {
            "ok": False, "tool": "memory_save", "result": None,
            "error": f"{type(e).__name__}: {e}",
        }

    # 4. 包装返回 — 字段对齐 BL-MM2 (previous_value / overwrite / no_change / summary)
    if no_change:
        summary = (
            f"'{name}' 跨 session 记忆已是这个值, 没改写历史. "
            f"(读旧值后发现 == 新值)"
        )
    elif is_overwrite:
        prev_preview = old_text[:80] + ("…" if len(old_text) > 80 else "")
        summary = (
            f"更新了跨 session 记忆 '{name}'. 上次值: {prev_preview!r}. "
            f"按 BL-MM1 纪律, 你回员工时**必须**主动 quote 旧值 "
            f"(\"我之前记的是 X, 现在改成 Y\"), 不要装作从来没记过."
        )
    else:
        suffix = " (read_old 失败已兜底)" if read_failed else ""
        summary = f"记下了跨 session 记忆 '{name}' (首次){suffix}."

    result_payload: Dict[str, Any] = {
        "type": "ok",
        "name": name,
        "previous_value": old_text if old_text else None,
        "overwrite": is_overwrite,
        "no_change": no_change,
        "read_old_ok": not read_failed,
        "summary": summary,
        # 真 hermes 返回也带回 (供 LLM 看到 path/状态; 主流程会经 scrub_brand 脱敏)
        "raw_save_response": raw_save,
    }
    return {"ok": True, "tool": "memory_save", "result": result_payload, "error": None}


def list_tools() -> List[Dict[str, Any]]:
    """返回 OpenAI tool calling 兼容的 tool definitions。

    输出顺序: catfish 原生 tools 排前面 (优先曝光给 LLM, prompt 里它们更早被
    扫到), 然后 mcp servers 已注册的 tools (BL-D3 Phase 3, 5/9), 最后是
    hermes 的 builtin tools 按字母序。
    """
    r = _r()
    out: List[Dict[str, Any]] = list(catfish_tools.CATFISH_NATIVE_TOOLS)

    # BL-D3 Phase 3 (5/9): 加 MCP server 暴露的 tools (subprocess 启动的).
    # 名带 mcp_<connector>_ 前缀, dispatch_tool 自动路由到 mcp_client.
    from . import mcp_client  # noqa: PLC0415  延迟 import
    mcp_tools = mcp_client.list_all_tools_as_native_schema()
    if mcp_tools:
        out.extend(mcp_tools)

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


# 5/6 安全 P1 G3: execute_code 安全守卫.
#
# 真正的 sandbox 在 hermes 那边 (我们这边只是 dispatcher), 但能在 dispatcher 层
# 拦"明显不合规"的脚本: 越权 path / 外联 / 危险 shell / 凭证读取.
# 不绝对完备 (能被 obfuscate 绕), 但显式拦截 = "鲶鱼明确禁止这种行为", audit 留痕.
#
# 命中时:
#   - 默认: 返回 error (LLM 看到, 不执行)
#   - env CATFISH_EXEC_GUARD=warn: 只 log, 不拦 (开发期调试用)
_EXECUTE_CODE_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    # ── 凭证 / 敏感目录 ────────────────────────────
    ("~/.ssh", "读员工 SSH 私钥 — 严禁"),
    ("/.ssh/id_", "读员工 SSH 私钥 — 严禁"),
    ("~/.aws/credentials", "读 AWS 凭证 — 严禁"),
    ("~/.docker/config.json", "读 Docker registry 凭证 — 严禁"),
    ("/library/keychains", "读 macOS Keychain — 严禁 (用 secret_resolver / keychain://)"),
    ("/etc/shadow", "读 Linux 密码 hash — 严禁"),
    ("/etc/passwd", "读系统账户清单 — 严禁"),
    ("netrc", "读 ~/.netrc 凭证 — 严禁"),
    # ── 网络外联 (data exfil 风险) ────────────────
    ("curl http", "外联网络 — 严禁 (用 catfish_browser_* / catfish_fetch_url, 走 audit)"),
    ("curl -x", "外联网络 — 严禁"),
    ("wget http", "外联网络 — 严禁"),
    ("requests.get(", "Python 外联网络 — 严禁 (走 catfish_fetch_url 留 audit)"),
    ("requests.post(", "Python 外联网络 — 严禁"),
    ("urllib.request.urlopen(", "Python 外联网络 — 严禁"),
    ("urllib2.urlopen(", "Python 外联网络 — 严禁"),
    ("httpx.get(", "Python 外联网络 — 严禁"),
    ("httpx.post(", "Python 外联网络 — 严禁"),
    ("aiohttp.clientsession", "Python 外联网络 — 严禁"),
    ("socket.connect(", "Python raw socket — 严禁"),
    # ── 危险 shell ──────────────────────────────
    ("rm -rf /", "递归删根目录 — 严禁"),
    ("rm -rf ~", "递归删 home — 严禁"),
    (":(){:|:&};:", "fork bomb — 严禁"),
    ("dd if=/dev/", "raw disk 操作 — 严禁"),
    ("mkfs.", "格式化 — 严禁"),
    ("> /dev/sd", "写裸盘 — 严禁"),
    ("chmod 777 /", "全盘权限放开 — 严禁"),
)


def _check_execute_code_security(name: str, args: Dict[str, Any]) -> str | None:
    """检测 execute_code 脚本里的危险操作 (越权/外联/凭证). 命中返回 error 字符串.

    限定 execute_code / shell_exec / python / bash 工具.
    跟 _check_execute_code_misuse 协同: misuse 拦"调错 catfish 工具" (功能错),
    security 拦"做坏事" (安全错).

    env CATFISH_EXEC_GUARD=warn 只 log 不拦 (开发期 / 信任环境用).
    """
    if name not in {"execute_code", "shell_exec", "python", "bash"}:
        return None
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

    hits = [(p, reason) for p, reason in _EXECUTE_CODE_DANGEROUS_PATTERNS if p.lower() in text]
    if not hits:
        return None

    pattern, reason = hits[0]
    mode = (os.environ.get("CATFISH_EXEC_GUARD") or "deny").strip().lower()
    msg = (
        f"🛡️ execute_code 安全守卫拦截: 检测到 {pattern!r} — {reason}. "
        f"鲶鱼禁止 LLM 通过 execute_code 做这些. "
        f"如需读特定文件/调 API, 用对应的 catfish_* 工具走 audit log."
    )
    if mode == "warn":
        # warn 模式只记录不拦 (默认 deny, 开发期调试可设 warn)
        logger.warning("[exec_guard:warn] %s | text 前 200 字: %s", msg, text[:200])
        return None
    return msg


async def dispatch_tool(
    name: str, args: Dict[str, Any], session_id: Optional[str] = None,
) -> Dict[str, Any]:
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

    # BL-MEMORY-PLUMBING-DIAG (5/16): memory 工具入口/出口都打 log, 排
    # "LLM 调了 memory action=add 但 ~/.hermes/memories/ 没动" 真因.
    # 只对 hermes memory 这一个工具加 log, 不污染其它 tool 日志.
    # 直接用 name == "memory" — _is_memory_tool 变量曾撞 _do_dispatch 不同
    # 作用域闯出 NameError, 把所有 memory 调用 fail 掉了 (5/16 16:07 实盘 trap).
    if name == "memory":
        logger.info(
            "BL-MEMORY-PLUMBING-DIAG dispatch IN: name=%s args=%s",
            name, args,
        )

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

    # 5/6 安全 G3 守卫: execute_code 危险操作 (越权/外联/凭证) → 拒绝 + audit 留痕.
    sec_msg = _check_execute_code_security(name, args)
    if sec_msg:
        # 写 audit 留痕 — 客户信安部门能查到"谁在 X 时间试图越权"
        try:
            audit.write_event(
                tool=name,
                ok=False,
                args=args,
                error=sec_msg,
                latency_ms=0.0,
                extra={"security_block": "exec_guard"},
            )
        except Exception:
            pass  # audit 写失败不影响拦截
        return {
            "ok": False,
            "tool": name,
            "result": None,
            "error": sec_msg,
            "stderr": None,
        }

    start = time.time()
    result = await _do_dispatch(name, args, session_id=session_id)
    latency_ms = (time.time() - start) * 1000

    # 写 audit (永远不抛, 不影响主流程返回).
    # 工具结果里 'security_audit' 字段 (例: 'credential_field_filled') 也并入 audit log
    # 让 IT 事后能 grep 谁在啥时候填了密码字段.
    audit_extra: dict[str, Any] = {}
    inner_result = result.get("result")
    if isinstance(inner_result, dict):
        if "security_audit" in inner_result:
            audit_extra["security_audit"] = inner_result["security_audit"]
        # BL-S29.2 (5/7): 沙箱字段进 audit log, 客户信安能 grep
        # `cat ~/.hermes/.catfish_audit.jsonl | jq 'select(.sandbox_used == true)'`
        # 看哪次 execute_code 真在沙箱里跑过.
        if "sandbox_used" in inner_result:
            audit_extra["sandbox_used"] = inner_result["sandbox_used"]
        if "sandbox_kind" in inner_result:
            audit_extra["sandbox_kind"] = inner_result["sandbox_kind"]
        if inner_result.get("timed_out"):
            audit_extra["sandbox_timed_out"] = True

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


# ============================================================
# BL-LINT-B (5/16): _do_dispatch 拆 — 193 LOC → 路由 ~40 LOC + 5 个分支函数.
# 风险低 (我有 test_memory_store_injection.py + test_memory_save_versioned.py
# 守, 测试套 652 个 baseline 也跑). 不改语义, 只拆.
# ============================================================


def _err_result(name: str, e: Exception, prefix: str = "") -> Dict[str, Any]:
    """统一的 dispatch error 返回 shape. 含 traceback 头 2000 字节用于 audit.

    prefix 用于区分 error 来源 (e.g. "sandbox crash:"), 空 = 用 exc 默认格式.
    """
    msg = f"{type(e).__name__}: {e}"
    if prefix:
        msg = f"{prefix} {msg}"
    return {
        "ok": False,
        "tool": name,
        "result": None,
        "error": msg,
        "traceback": traceback.format_exc()[:2000],
    }


async def _dispatch_native_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """catfish 原生 tool — 不走 hermes registry, 不查 toolset 可用性."""
    try:
        raw = await asyncio.to_thread(catfish_tools.dispatch_native, name, args)
        return {"ok": True, "tool": name, "result": raw, "error": None}
    except Exception as e:
        logger.exception("native dispatch failed: %s", name)
        return _err_result(name, e)


async def _dispatch_mcp_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """BL-D3 Phase 3 (5/9): MCP server tools (mcp_<connector>_*) — 透传 tools/call."""
    from . import mcp_client  # noqa: PLC0415  延迟 import 防循环
    try:
        raw = await mcp_client.dispatch_mcp_tool(name, args)
        return {"ok": True, "tool": name, "result": raw, "error": None}
    except Exception as e:
        logger.exception("mcp dispatch failed: %s", name)
        return _err_result(name, e)


async def _dispatch_sandboxed_code(
    name: str, args: Dict[str, Any], sandbox_lang: str,
) -> Dict[str, Any]:
    """BL-S29.2/29.5: execute_code/python/bash/sh 走平台沙箱 (macOS sandbox-exec /
    Linux nsjail / Docker fallback) 隔离子进程, 不转给 hermes dispatch.

    返回 shape 跟 hermes execute_code 兼容, LLM 看不出区别. 加 sandbox_used /
    sandbox_kind 进 audit, 客户信安能 grep.
    """
    # 不同 hermes 工具字段名不一样, 兜底逐个看
    code = (
        args.get("code")
        or args.get("script")
        or args.get("command")
        or args.get("input")
        or ""
    )
    if not code:
        return {
            "ok": False, "tool": name, "result": None,
            "error": f"沙箱模式: 工具 {name} 调用未提供 code/script/command 字段",
        }
    timeout_s = int(args.get("timeout_s") or args.get("timeout") or 30)
    try:
        sb_result = await asyncio.to_thread(
            sandbox.run_in_sandbox,
            code,
            lang=sandbox_lang,
            timeout_s=timeout_s,
        )
    except Exception as e:
        logger.exception("sandbox dispatch crashed: %s", name)
        return _err_result(name, e, prefix="sandbox crash:")

    return {
        "ok": sb_result["ok"],
        "tool": name,
        "result": {
            "stdout": sb_result["stdout"],
            "stderr": sb_result["stderr"],
            "returncode": sb_result["rc"],
            "elapsed_ms": sb_result["elapsed_ms"],
            "timed_out": sb_result["timed_out"],
            "sandbox_used": sb_result["sandbox_used"],
            "sandbox_kind": sb_result["sandbox_kind"],
        },
        "error": None if sb_result["ok"] else (
            f"代码非 0 退出 (rc={sb_result['rc']})"
            + (" [timeout]" if sb_result["timed_out"] else "")
        ),
    }


def _resolve_tool_name_or_error(
    name: str, all_names: List[str],
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """name 不在 registry 时, 试 BL-FIX-MCP-SHORTNAME auto-suffix 匹配.

    返回 (resolved_name, error_dict). 命中 → (resolved, None). 不命中 → (None, error_dict).
    name 已在 registry → (name, None).
    """
    if name in all_names:
        return name, None

    fallback = _resolve_mcp_short_name(name, all_names)
    if fallback:
        logger.info(
            "BL-FIX-MCP-SHORTNAME: dispatch %r → %r (auto-suffix match)",
            name, fallback,
        )
        return fallback, None

    candidates = _suggest_mcp_full_names(name, all_names)
    err_msg = f"unknown tool: {name}"
    if candidates:
        err_msg += (
            f". MCP 工具调用必须用全名, 你大概想调: {', '.join(candidates[:5])}"
        )
    return None, {
        "ok": False, "tool": name, "result": None,
        "error": err_msg,
        "candidates": candidates[:5],
    }


def _build_extra_kwargs_for_hermes(
    name: str, session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """根据 tool name 决定要给 hermes registry.dispatch 透传哪些 kwargs.

    BL-MEMORY-BRIDGE-STORE (5/16): memory 工具 handler 要 kw['store']=MemoryStore.
    BL-TODO-BRIDGE-STORE (5/16): todo 工具同模式, 但 store 是 per-session 的
      (TodoStore in-memory 无持久化, hermes 设计 per-AIAgent). session_id 缺失
      走 __default__ 全局 singleton (兼容老客户端).
    """
    extra_kw: Dict[str, Any] = {}
    if name == "memory":
        mem_store = _get_memory_store()
        if mem_store is not None:
            extra_kw["store"] = mem_store
        # store 拿不到也照常 dispatch — hermes memory_tool 会自己返
        # "Memory is not available", 比我们这层拦截更对齐 hermes 错误格式.
    elif name == "todo":
        todo_store = _get_todo_store(session_id)
        if todo_store is not None:
            extra_kw["store"] = todo_store
    return extra_kw


async def _dispatch_via_hermes_registry(
    name: str, args: Dict[str, Any], registry: Any,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """走 hermes registry.dispatch 主路径. 含 store 注入 + 结果截断 + memory 工具诊断 log."""
    extra_kw = _build_extra_kwargs_for_hermes(name, session_id=session_id)

    try:
        dispatch_fn = registry.dispatch
        if inspect.iscoroutinefunction(dispatch_fn):
            raw = await dispatch_fn(name, args, **extra_kw)
        else:
            # to_thread 跑 sync dispatch 避免堵 asyncio loop. **extra_kw 透传给
            # registry.dispatch(name, args, **kwargs) — hermes 接, lambda handler
            # 内 kw.get('store') 拿到 MemoryStore.
            raw = await asyncio.to_thread(
                dispatch_fn, name, args, **extra_kw,
            )

        try:
            max_size = registry.get_max_result_size(name)
        except Exception:
            max_size = 100_000  # 兜底 100KB
        truncated = _truncate_for_ipc(raw, max_size)

        if name == "memory":
            # 截断打印, 避免长 result 撑爆 log
            raw_repr = repr(raw)[:500] if raw is not None else "None"
            logger.info(
                "BL-MEMORY-PLUMBING-DIAG dispatch OUT ok: name=%s raw_type=%s raw=%s",
                name, type(raw).__name__, raw_repr,
            )
        # BL-TODO-STORE-PERSIST (5/16): todo dispatch 后写盘, 跨进程不丢. 失败 silent.
        if name == "todo" and "store" in extra_kw:
            _persist_todo_store(
                session_id or _TODO_STORE_DEFAULT_KEY, extra_kw["store"],
            )
        return {"ok": True, "tool": name, "result": truncated, "error": None}

    except Exception as e:
        if name == "memory":
            logger.warning(
                "BL-MEMORY-PLUMBING-DIAG dispatch OUT err: name=%s exc=%s: %s",
                name, type(e).__name__, e,
            )
        logger.exception("dispatch_tool failed: %s", name)
        return _err_result(name, e)


async def _do_dispatch(
    name: str, args: Dict[str, Any], session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """实际 dispatch 逻辑. 抽出来让 dispatch_tool 可以包 audit.

    5 个分支按优先级:
      1. catfish native (不走 hermes)
      2. mcp server tools (透传 mcp_client)
      3. sandbox 化的 execute_code (走平台沙箱)
      4. memory_save BL-MM3 版本化 wrapper
      5. hermes registry.dispatch 主路径 (含 store 注入)

    任一分支命中 → 直接返结果. 其它情况落到分支 5.
    """
    # 1. catfish 原生 tool
    if catfish_tools.is_native(name):
        return await _dispatch_native_tool(name, args)

    # 2. MCP server tool (BL-D3 Phase 3)
    from . import mcp_client  # noqa: PLC0415  延迟 import 防循环
    if mcp_client.is_mcp_tool(name):
        return await _dispatch_mcp_tool(name, args)

    # 3. sandboxed execute_code (BL-S29.2/29.5)
    sandbox_lang = sandbox.detect_lang_from_tool_name(name)
    if (
        sandbox_lang is not None
        and sandbox.is_sandbox_enabled()
        and sandbox.is_sandbox_supported()
    ):
        return await _dispatch_sandboxed_code(name, args, sandbox_lang)

    # 4 + 5: 需要 registry. 先解析 tool name (含 mcp 短名 fallback).
    r = _r()
    all_names = r.get_all_tool_names()
    resolved_name, err = _resolve_tool_name_or_error(name, all_names)
    if err is not None:
        return err
    assert resolved_name is not None  # for type checker
    name = resolved_name

    # 4. memory_save BL-MM3 版本化 wrapper (在 hermes dispatch 前拦截)
    if name == "memory_save" and os.environ.get("CATFISH_DISABLE_MM3") != "1":
        return await _memory_save_versioned(args)

    # toolset 可用性 check (用 toolset 维度判, check_tool_availability 返 tuple 不能直接用)
    toolset = r.get_toolset_for_tool(name)
    if toolset and not r.is_toolset_available(toolset):
        return {
            "ok": False, "tool": name, "result": None,
            "error": f"toolset '{toolset}' 在当前环境不可用（依赖未装/env 未配/平台不支持）",
        }

    # 5. hermes registry.dispatch 主路径
    return await _dispatch_via_hermes_registry(name, args, r, session_id=session_id)


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
