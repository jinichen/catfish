"""STUB — DEPRECATED 5/26 (跟 skills_loader 同批砍, 死代码不是隐私违规).

详见 skills_loader.py 顶部 DEPRECATED 说明.
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "skills_inject 5/26 砍 — 跟 skills_loader 同批 (gateway 死代码, plugin 真接管)."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
