"""BL-Q3-ARCHIVE — tool message archive + 摘要双层 (5/11).

修 context overflow 真根因. 替代 BL-FIX41 硬切, 实现 lossless 保留 + LLM 主动召回.

模块结构:
  archiver.py        — 主流程 (检测 > 4KB → 写 archive → 替换 prompt content)
  db.py              — PG + jsonl 兜底 (跟 facts_db / mcp-registry 同模板)
  prompts.py         — 替换文本模板 + 摘要 prompt
  reader.py          — catfish_read_tool_archive 工具实现 (grep / line_range)
  router.py          — gateway HTTP 路由 /api/tool-archives/*
  summary_worker.py  — 后台 haiku 摘要 (异步, 不阻塞 chat)
  features.py        — 灰度开关 (跟 BL-FIX41 共存)

设计文档: docs/CATFISH-Q3-ARCHIVE-DESIGN.md
"""

from __future__ import annotations

from .archiver import (
    THRESHOLD_BYTES,
    archive_tool_messages,
    prepare_tool_messages,
)
from .features import is_archive_enabled

__all__ = [
    "THRESHOLD_BYTES",
    "archive_tool_messages",
    "prepare_tool_messages",
    "is_archive_enabled",
]
