"""STUB — DEPRECATED 5/26 (gateway 不读员工本机 output/).

# 砍的真原因

老 list_recent 扫 ~/.catfish/output/ 列最近 24h 写的文件, 给 timeout 友好错  # noqa: BOUNDARY
误段 inject "📁 过去 24h 鲶鱼已写文件" 列表. gateway 中央代码读员工 fs,
SaaS 化即破. 5/26 audit 抓.

# 替代方案

Companion 端在 chat timeout 时自己显示 toast 列本地 outputs (BL-X 后续做,
Companion 已有 reveal_in_finder Tauri 命令). gateway timeout 错误段只留通用
建议 ("切大模型 / 新建会话") 不再列文件.

# fail-loud stub

任何 `from .recent_outputs import list_recent` 立刻抛 RuntimeError.
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "recent_outputs.list_recent 5/26 砍 — gateway 不读员工 .catfish/output/. "
    "Companion 自己在 timeout toast 里列本地 outputs (BL-X)."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
