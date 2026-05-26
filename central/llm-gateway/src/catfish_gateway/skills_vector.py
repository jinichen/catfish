"""STUB — DEPRECATED 5/26 (跟 skills_loader 同批砍).

5/25 BL-SKILLS-VECTOR (BM25 RAG) 是 5/25 sprint 才 ship 的功能, 5/26 audit
发现 skills_loader 整套是死代码 (gateway 中央扫自己 home 永远空), BM25 RAG
基于错假设 (gateway 能看到员工 skill) 也没意义. 跟 skills_loader 同批砍.

详见 skills_loader.py 顶部 DEPRECATED 说明.
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "skills_vector 5/26 砍 — 5/25 ship 的 BM25 RAG 基于错假设, 跟 skills_loader 同批."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
