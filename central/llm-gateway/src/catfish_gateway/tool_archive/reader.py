"""BL-Q3-ARCHIVE — catfish_read_tool_archive 工具实现 (5/11).

三种调用模式 (设计文档 §7):
  - ref 全文返 (慎用, max_bytes 兜底)
  - ref + line_range  按行号片段
  - ref + grep        按关键字召回 ± 5 行上下文
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("catfish.gateway.tool_archive.reader")

#: 单次返字节硬上限 (防 LLM 调坏拉全文撑爆 context)
DEFAULT_MAX_BYTES = 8_000
HARD_MAX_BYTES = 32_000  # max_bytes 即使被传 100K 也只到 32K


def _parse_line_range(spec: str) -> tuple[int, int]:
    """'50-120' → (50, 120). '47' → (47, 47). 1-indexed."""
    s = spec.strip()
    if "-" in s:
        a, b = s.split("-", 1)
        start = max(1, int(a.strip()))
        end = max(start, int(b.strip()))
    else:
        start = end = max(1, int(s.strip()))
    return start, end


def _slice_lines(content: str, line_range: str, *, max_bytes: int) -> str:
    start, end = _parse_line_range(line_range)
    lines = content.splitlines(keepends=False)
    if start > len(lines):
        return f"[line {start} 超出范围 (总 {len(lines)} 行)]"
    sliced = lines[start - 1 : end]
    out_lines = []
    for i, ln in enumerate(sliced, start=start):
        out_lines.append(f"{i:>5}│ {ln}")
    out = "\n".join(out_lines)
    encoded = out.encode("utf-8")
    if len(encoded) > max_bytes:
        truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return (
            f"{truncated}\n\n"
            f"[超 max_bytes={max_bytes} 截断, 缩 line_range 或调高 max_bytes]"
        )
    return out


def _grep_with_context(
    content: str, pattern: str, *, ctx_lines: int = 5, max_bytes: int
) -> str:
    """对 content 按 pattern (普通子串, 不是 regex) 召回, 每个 hit ± ctx_lines."""
    lines = content.splitlines(keepends=False)
    pat = pattern  # 普通子串匹配, 不用 regex 避免用户写崩
    hits = [i for i, ln in enumerate(lines) if pat in ln]
    if not hits:
        return f"[未找到 '{pattern}' (共 {len(lines)} 行)]"

    # 合并相邻 hit 的 context 区间
    intervals: list[tuple[int, int]] = []
    for h in hits:
        lo = max(0, h - ctx_lines)
        hi = min(len(lines) - 1, h + ctx_lines)
        if intervals and lo <= intervals[-1][1] + 1:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], hi))
        else:
            intervals.append((lo, hi))

    out_blocks: list[str] = [
        f"[grep '{pattern}' 命中 {len(hits)} 行, 召回 {len(intervals)} 段, ±{ctx_lines} 行上下文]"
    ]
    for lo, hi in intervals:
        block_lines = []
        for i in range(lo, hi + 1):
            marker = "→" if pat in lines[i] else " "
            block_lines.append(f"{i + 1:>5}{marker}│ {lines[i]}")
        out_blocks.append("\n".join(block_lines))

    out = "\n\n---\n\n".join(out_blocks)
    encoded = out.encode("utf-8")
    if len(encoded) > max_bytes:
        truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return (
            f"{truncated}\n\n"
            f"[超 max_bytes={max_bytes} 截断, 缩小 grep 范围或调高 max_bytes]"
        )
    return out


def _full_content(content: str, *, max_bytes: int) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return (
        f"{truncated}\n\n"
        f"[原文 {len(encoded)} 字节, max_bytes={max_bytes} 截断. "
        f"用 grep / line_range 缩小召回范围]"
    )


def read_archive_content(
    *,
    content: str,
    line_range: str | None = None,
    grep: str | None = None,
    max_bytes: int | None = None,
) -> str:
    """工具主入口 — 给定 archive 全文 + 参数, 返用户该看的部分."""
    mb = min(HARD_MAX_BYTES, max_bytes if max_bytes else DEFAULT_MAX_BYTES)
    mb = max(500, mb)  # 至少 500B 避免负数 / 0

    if grep:
        return _grep_with_context(content, grep, max_bytes=mb)
    if line_range:
        return _slice_lines(content, line_range, max_bytes=mb)
    return _full_content(content, max_bytes=mb)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "HARD_MAX_BYTES",
    "read_archive_content",
]
