"""memory_save_versioned tool — 抽自 adapter.py (5/21 拆分).

跟 hermes 内置 memory_save 同模式但版本化: 老 inline block 被 strip, 新值
push 进 revision list, 保留 prev_value. 跟 catfish-memory plugin (BL-MM2)
联动: SOUL 要求 LLM 引用旧值, gateway inject 时带 'last_value: X'.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("catfish.adapter")

# inline 备注前缀, 测试 / verify 用同一个常量 (跟 adapter.py 保持一致)
_MM3_INLINE_PREFIX = "_(BL-MM3 上次值, 已废"
_MM3_INLINE_MAX_OLD_LEN = 200  # 旧值塞 inline 时截到这么长


def _r():
    """快捷拿 ToolRegistry singleton (从 adapter.py 取, 避免循环 import 卡顿)."""
    from . import adapter  # noqa: PLC0415 — 延迟 import 防 circular
    return adapter._r()

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


