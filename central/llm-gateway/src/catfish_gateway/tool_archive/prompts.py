"""BL-Q3-ARCHIVE — prompt 替换文本 + 摘要 prompt (5/11).

把超 4KB 的 tool message 内容替换成 LLM 可读的引用文本:

  [已归档: archive_ref=abc12345, tool=execute_code, 12.4KB / 287 行]
  📝 摘要 (haiku): ...
  📂 头部 (前 500B): ...
  📂 尾部 (后 500B): ...
  💡 看不全? catfish_read_tool_archive(ref="abc12345", grep="...")

设计文档 §8.
"""
from __future__ import annotations

#: 头尾预览字节数 (跟 archiver.py 一致)
PREVIEW_HEAD_BYTES = 500
PREVIEW_TAIL_BYTES = 500


def _safe_preview(text: str, n_bytes: int, *, head: bool) -> str:
    """字节级安全切. errors='ignore' 兜底切碎的多字节字符."""
    encoded = text.encode("utf-8")
    if head:
        return encoded[:n_bytes].decode("utf-8", errors="ignore")
    return encoded[-n_bytes:].decode("utf-8", errors="ignore")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.2f}MB"


def _indent_preview(text: str) -> str:
    """前面加 > 让 markdown 渲染成 quote, LLM 一眼看出是预览不是当前内容."""
    return "\n".join(f"> {line}" for line in text.splitlines())


def build_replacement_text(
    *,
    ref: str,
    content: str,
    tool_name: str | None,
    content_bytes: int,
    lines: int,
    summary: str | None = None,
    summary_error: str | None = None,
) -> str:
    """根据 archive 元信息构造替换文本.

    summary 三态:
      - 有值 → 显示
      - None + error 有值 → 显示"摘要失败"
      - None + 无 error → 显示"生成中" (worker 还没跑到)
    """
    head_preview = _safe_preview(content, PREVIEW_HEAD_BYTES, head=True)
    tail_preview = _safe_preview(content, PREVIEW_TAIL_BYTES, head=False)

    if summary:
        summary_line = f"📝 摘要 (haiku): {summary.strip()}"
    elif summary_error:
        summary_line = "📝 摘要 (生成失败, 看头尾或直接 read 拿全文)"
    else:
        summary_line = "📝 摘要 (生成中…若需中段, 直接调 read tool 不等摘要)"

    tool_str = tool_name or "tool"
    size_str = _fmt_size(content_bytes)

    return (
        f"[已归档: archive_ref={ref}, tool={tool_str}, {size_str} / {lines} 行]\n"
        f"\n"
        f"{summary_line}\n"
        f"\n"
        f"📂 头部 (前 {PREVIEW_HEAD_BYTES}B):\n"
        f"{_indent_preview(head_preview)}\n"
        f"\n"
        f"📂 尾部 (后 {PREVIEW_TAIL_BYTES}B):\n"
        f"{_indent_preview(tail_preview)}\n"
        f"\n"
        f"💡 看不全? 调 catfish_read_tool_archive(ref=\"{ref}\", grep=\"...\") "
        f"或 (ref, line_range=\"40-80\") 拿中段. **不要凭空编中段内容**."
    )


# ── 摘要 prompt (反幻觉, 保关键 fact) ───────────────────────────────


SUMMARY_SYSTEM_PROMPT = """你是 tool output 摘要器. 输入是 LLM 工具执行结果 \
(代码运行 / 浏览器快照 / 文件读取等), 输出**简短中文摘要 (50-120 字)**, 用于让另一个 \
LLM 快速判断"要不要看全文". 严格遵守:

✅ 必须包含 (按优先级):
1. 执行结果 (成功 / 失败 / 部分)
2. 关键数字 (count / size / 用时 / 行数)
3. 报错信息**最后一行** (含 exception 类型 + 值, 原样引用不改写)
4. 关键文件路径 / URL
5. 主要操作类型 (跑 N 个 test / 截了一个页面 / 读了 K 行代码)

❌ 严禁:
1. 凭空推断 / 加未在原文出现的事实
2. 复述命令 / 输出格式 (用户已经看到了)
3. 客套话 ("以下是..." / "总结如下" / "希望有帮助")
4. 推测原因 (除非原文里说了)
5. markdown 格式 (输出纯文本)
6. 超过 120 字 (硬上限)

输出: 一行纯文本摘要, 别加引号 / 引导语 / 标题."""


#: 喂给摘要器的原文上限. 超过这个就头 25K + 尾 25K (避免摘要器自己撑爆 context).
MAX_SUMMARIZE_INPUT_BYTES = 50_000


def truncate_for_summary(content: str) -> str:
    """超过 50KB 的内容头 25K + 尾 25K 喂摘要器."""
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_SUMMARIZE_INPUT_BYTES:
        return content
    half = MAX_SUMMARIZE_INPUT_BYTES // 2
    head = encoded[:half].decode("utf-8", errors="ignore")
    tail = encoded[-half:].decode("utf-8", errors="ignore")
    cut = len(encoded) - MAX_SUMMARIZE_INPUT_BYTES
    return f"{head}\n\n...[中段省略 {cut} 字节, 摘要器只看头尾]...\n\n{tail}"


def build_summary_user_prompt(content: str, tool_name: str | None) -> str:
    truncated = truncate_for_summary(content)
    return (
        f"工具: {tool_name or 'unknown'}\n"
        f"内容 ({len(content.encode('utf-8'))} 字节, {content.count(chr(10)) + 1} 行):\n"
        f"---\n"
        f"{truncated}\n"
        f"---\n"
        f"摘要:"
    )


__all__ = [
    "PREVIEW_HEAD_BYTES",
    "PREVIEW_TAIL_BYTES",
    "SUMMARY_SYSTEM_PROMPT",
    "MAX_SUMMARIZE_INPUT_BYTES",
    "build_replacement_text",
    "build_summary_user_prompt",
    "truncate_for_summary",
]
