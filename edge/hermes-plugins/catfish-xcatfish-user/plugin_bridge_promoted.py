"""P53 (10/13): 模型用 tool_call 桥去调「已提升为核心」的 catfish 工具时, 照转, 别报错。

# 病 (10/13 13:52, 微信「福富的资质列表」)

模型第一步就想查知识库, 发的是

    tool_call(name="mcp__catfish_tools__catfish_wiki_search", arguments={...})

hermes 回: `'…catfish_wiki_search' is not a deferrable tool. If it appears in the
model-facing tools list already, call it directly instead of via tool_call.`

catfish_wiki_search 是 P43 提升进核心的 (可见, 不被 defer), 所以 tool_call 桥不收。
道理没错, 但 qwen-flash 没照提示直接重调, 而是绕去 search_files → wiki_list →
**写 Python 读 .md**, 一个问题跑了 15 次 execute_code, 微信里弹两次审批。
「查个资料要人批两次代码」的根子在这里, 不在审批本身。

# 改法

tool_call 的两道关:
  1. `resolve_underlying_call` —— 名字必须 deferrable, 否则报上面那句
  2. `scoped_deferrable_names(current_defs)` —— 名字必须在本会话的 deferrable 集合里

只对 `mcp__catfish_tools__` 开头、**已注册**、且**在本会话工具列表里**的名字放行:
  · 被 defer 的 catfish 工具本来就能走 tool_call; 被 P43 提升的那几个本来就能直接调。
    放行后能调到的工具集合 **一个都没多**, 只是两条路都通。
  · 别的工具 (hermes 核心 / 其它 MCP) 照旧走原逻辑, 不碰。
  · 模型漏写前缀 (`catfish_wiki_search`) 也认, 补上前缀。
  · 工具自己的审批 / 参数校验照常发生 (dispatch 递归回 handle_function_call)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.xcatfish_user.plugin")

CATFISH_PREFIX = "mcp__catfish_tools__"


def _registered(name: str) -> bool:
    try:
        from tools.registry import registry
        return registry.get_entry(name) is not None
    except Exception:  # noqa: BLE001
        return False


def canonical_catfish_name(name: str, registered=_registered):
    """catfish 工具名 → 带前缀的注册名; 不是 catfish 工具 / 没注册 → None。"""
    name = (name or "").strip()
    if not name:
        return None
    cand = name if name.startswith(CATFISH_PREFIX) else (
        CATFISH_PREFIX + name if name.startswith("catfish_") else None)
    if cand and registered(cand):
        return cand
    return None


#: 原函数说"不是 deferred 工具"的几种说法 —— v0.21 一句; v0.22 (10/14) 拆成两句:
#: 已直接列出的 ("is a directly-listed tool") / 名字不认识 (漏前缀时, "is not a known tool name")
NOT_DEFERRABLE_MARKERS = (
    "is not a deferrable tool",
    "is a directly-listed tool",
    "is not a known tool name",
)


def _single_entry(args):
    """tool_call 参数里的唯一一条 (name, arguments); 解析不了 / 不止一条 → (None, err)。

    v0.22 起参数可以是 {"name", "arguments"} 也可以是 {"calls": [...]}, 交给原生的
    normalize_tool_call_entries 统一解析 (arguments 是 JSON 字符串也由它处理)。"""
    try:
        from tools.tool_search import normalize_tool_call_entries
    except ImportError:
        normalize_tool_call_entries = None
    if normalize_tool_call_entries is not None:
        entries, err = normalize_tool_call_entries(args or {})
        if err:
            return None, err
        if len(entries) != 1:
            return None, "multi"
        return (entries[0]["name"], entries[0]["arguments"]), None
    # 老版本: 只有 name / arguments
    raw = (args or {}).get("arguments")
    if raw is None:
        raw = {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"tool_call 'arguments' is not valid JSON: {e}"
    if not isinstance(raw, dict):
        return None, "tool_call 'arguments' must be an object"
    return (str((args or {}).get("name") or ""), raw), None


def wrap_resolve(orig, registered=_registered):
    def patched(args):
        name, raw_args, err = orig(args)
        if err and any(m in err for m in NOT_DEFERRABLE_MARKERS):
            entry, perr = _single_entry(args)
            canon = canonical_catfish_name(str(entry[0] or ""), registered) if entry else None
            if canon:
                logger.info("P53 tool_call → 已提升的 catfish 工具 %s, 直接转发", canon)
                return canon, entry[1], None
            if entry is None and perr != "multi" and canonical_catfish_name(
                    str((args or {}).get("name") or ""), registered):
                # 是 catfish 工具但参数坏了: 报参数的错 (模型能照着改), 别报"不是 deferred 工具"
                return None, {}, perr
        return name, raw_args, err

    patched._catfish_p53 = True
    return patched


def wrap_scoped(orig):
    def patched(tool_defs):
        names = set(orig(tool_defs))
        for td in tool_defs or []:
            n = ((td or {}).get("function") or {}).get("name", "")
            if n.startswith(CATFISH_PREFIX):
                names.add(n)       # 本会话列表里本来就有的 catfish 工具
        return frozenset(names)

    patched._catfish_p53 = True
    return patched


def _patch_p53_bridge_promoted_catfish_tools() -> None:
    try:
        import tools.tool_search as ts
    except ImportError as e:
        logger.warning("P53: tools.tool_search import 失败 (%s), skip", e)
        return
    for attr, wrap in (("resolve_underlying_call", wrap_resolve),
                       ("scoped_deferrable_names", wrap_scoped)):
        orig = getattr(ts, attr, None)
        if orig is None:
            logger.warning("P53: tools.tool_search.%s 不在 (hermes 改了?), skip", attr)
            return
        if not getattr(orig, "_catfish_p53", False):
            setattr(ts, attr, wrap(orig))
    logger.info("P53 tool_call 转发已提升的 catfish 工具 patched")
