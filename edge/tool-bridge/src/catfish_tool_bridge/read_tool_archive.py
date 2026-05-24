"""catfish_read_tool_archive 工具实现.

# 历史

5/11 BL-Q3-ARCHIVE v1: 走 HTTP POST gateway /api/tool-archives/read 拿片段.
gateway 端读 PG `tool_archives` 表 + content 全文返回.

# 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE 真重构

老路违反 5/17 BOUNDARY (PG 装员工 tool result 全文). 改成本机直读 —
catfish-tool-bridge 这边 archive 写本机, read 也直接读本机文件 + sqlite 索引.

跳过 HTTP, 跳过 gateway, 跳过 PG. 跨员工 portable: catfish_user 字段
(OIDC sub) 匹配才放行, 防别人 Mac 读你的 archive.

# 流程

LLM 看到 `[已归档: archive_ref=abc123]` → 调 catfish_read_tool_archive(ref="abc123")
  → tool-bridge dispatch → 本函数
  → tool_archive_local.read_archive(ref) 读 sqlite 索引 + 文件
  → catfish_user 字段比对当前 OIDC sub (允许 None → None 兜底)
  → grep / line_range / max_bytes 过滤
  → 返片段
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import tool_archive_local

logger = logging.getLogger("catfish.tool_bridge.read_tool_archive")


def _apply_line_range(content: str, line_range: str) -> tuple[str, int]:
    """按 '40-80' / '47' / '40-' / '-80' 切. 返 (sliced, total_lines).

    line_range 非法 → 返原 content + 全行数.
    """
    lines = content.split("\n")
    total = len(lines)
    m = re.match(r"^(\d+)?-(\d+)?$|^(\d+)$", line_range.strip())
    if not m:
        return content, total
    if m.group(3):  # 单行 '47'
        n = int(m.group(3))
        return (lines[n - 1] if 1 <= n <= total else ""), total
    start = int(m.group(1)) if m.group(1) else 1
    end = int(m.group(2)) if m.group(2) else total
    start = max(1, start)
    end = min(total, end)
    if start > end:
        return "", total
    return "\n".join(lines[start - 1:end]), total


def _apply_grep(content: str, pattern: str, context: int = 2) -> str:
    """grep 含上下文. 单行不超过 200 字 (防 binary)."""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"[grep 正则非法: {e}]"
    lines = content.split("\n")
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if rx.search(line):
            for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                keep.add(j)
    if not keep:
        return f"[grep '{pattern}' 0 命中]"
    # 按段输出 (相邻行合并)
    out: list[str] = []
    prev = -2
    for i in sorted(keep):
        if i > prev + 1 and out:
            out.append(f"  ... ({i - prev - 1} 行省略) ...")
        line = lines[i]
        if len(line) > 200:
            line = line[:200] + f"... [+{len(line) - 200} 字]"
        out.append(f"{i + 1:>5}: {line}")
        prev = i
    return "\n".join(out)


def _apply_max_bytes(content: str, max_bytes: int) -> str:
    """超过 max_bytes 截断, 加省略提示."""
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    cut = len(encoded) - max_bytes
    return f"{truncated}\n\n... [截断, 还有 {cut} 字节. 用 grep / line_range 精确拿]"


def read_tool_archive(args: dict[str, Any]) -> dict[str, Any]:
    """LLM 调过来. 参数:
      ref: str (必填) — archive_ref, 16 hex 字符
      line_range: str | None  '40-80' / '47' / '40-' / '-80'
      grep: str | None        正则
      max_bytes: int | None   返回最大字节, 默认 8000

    返:
      {"ok": True, "ref", "content", "total_lines", "total_bytes",
       "tool_name", "summary", "hint"}  或
      {"ok": False, "error"}
    """
    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"ok": False, "error": "ref 必填"}
    if not re.fullmatch(r"[0-9a-f]{16}", ref):
        return {"ok": False, "error": f"ref 格式非法 (应 16 hex 字符): {ref}"}

    payload = tool_archive_local.read_archive(ref)
    if payload is None:
        return {
            "ok": False,
            "error": f"archive {ref} 不存在或已过期 (默认 14 天保留)",
        }

    # 跨员工权限检查 — 别人 Mac 上拷过来的 archive 当前用户读不了.
    # 兼容老 archive (catfish_user 为 None) — 老格式没记 OIDC sub, 一律放行.
    current_user = tool_archive_local._catfish_user()
    archive_user = payload.get("catfish_user")
    if archive_user and current_user and archive_user != current_user:
        logger.warning(
            "[read_tool_archive] 跨用户拒读: ref=%s archive_user=%s current=%s",
            ref, archive_user, current_user,
        )
        return {
            "ok": False,
            "error": f"无权读这条 archive (不是你的: archive 属 {archive_user})",
        }

    content: str = payload.get("content", "")
    total_lines = payload.get("lines") or content.count("\n") + 1
    total_bytes = payload.get("content_bytes") or len(content.encode("utf-8"))

    # 过滤链: grep 优先, 没 grep 走 line_range, 都没就走 max_bytes 截断
    applied = []
    if args.get("grep"):
        content = _apply_grep(content, str(args["grep"]))
        applied.append(f"grep='{args['grep']}'")
    elif args.get("line_range"):
        content, _ = _apply_line_range(content, str(args["line_range"]))
        applied.append(f"line_range='{args['line_range']}'")

    max_bytes = args.get("max_bytes")
    try:
        max_bytes_int = int(max_bytes) if max_bytes else 8000
    except (TypeError, ValueError):
        max_bytes_int = 8000
    content = _apply_max_bytes(content, max_bytes_int)
    if max_bytes_int < total_bytes:
        applied.append(f"max_bytes={max_bytes_int}")

    return {
        "ok": True,
        "ref": ref,
        "content": content,
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "tool_name": payload.get("tool_name"),
        "summary": payload.get("summary"),
        "filters_applied": applied or None,
        "hint": (
            "已拿到片段. 若仍不够, 缩小 grep 范围或精确 line_range. "
            "不要在 prompt 里完整复述这段, 你已经看到了."
        ),
    }


__all__ = ["read_tool_archive"]
