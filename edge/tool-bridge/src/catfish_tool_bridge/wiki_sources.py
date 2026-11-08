"""上传的原始资料 (wiki/raw/sources/*.md, 原件转存) 里提到某个条目的段落 (11/08)。

「整理属性」原来只读条目正文: 人 / 组织 / 项目的正文多是零散一句, 50 条里只有十来条能补到东西。
可属性往往就在上传的花名册、组织架构、项目台账、周报里 —— 这里按标题 / 别名在原件里找提到它的地方, 每处取前后
一段, 交给模型连同正文一起看 (摘出来的值照样必须原样出现在给它看的文字里, 防编)。

文件按 mtime 缓存在进程里, 一次整理属性要查几十个名字, 不重复读盘。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import wiki_files

WINDOW = 220          # 提到的地方前后各取多少字
PER_NAME = 3          # 一个条目最多取几段
MAX_CHARS = 1500      # 交给模型的原件段落总上限
_cache: dict[str, Any] = {"stamp": None, "docs": []}


def _sources_dir() -> Path:
    return wiki_files._catfish_home() / "wiki" / "raw" / "sources"


def _docs() -> list[tuple[str, str]]:
    """[(文件名, 正文)], 按目录里各文件的 mtime 缓存。"""
    d = _sources_dir()
    if not d.is_dir():
        return []
    files = sorted(p for p in d.glob("*.md") if p.is_file())
    stamp = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if _cache["stamp"] == stamp:
        return _cache["docs"]
    docs = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _fm, body = wiki_files._split_frontmatter(text)
        title = wiki_files._parse_field(_fm, "filename") or wiki_files._parse_field(_fm, "title") or p.stem
        docs.append((title, body))
    _cache.update(stamp=stamp, docs=docs)
    return docs


def snippets_about(title: str, aliases: list[str] | None = None, *, limit: int = MAX_CHARS) -> str:
    """原件里提到这个名字的段落, 拼成一段文字 (每段前面标明出自哪份资料); 没提到返空串。"""
    names = [n.strip() for n in [title, *(aliases or [])] if n and len(n.strip()) >= 2]
    if not names:
        return ""
    pat = re.compile("|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True)))
    out: list[str] = []
    total = 0
    for doc_title, body in _docs():
        taken = 0
        last_end = -1
        for m in pat.finditer(body):
            if m.start() < last_end:
                continue
            start = max(0, m.start() - WINDOW)
            end = min(len(body), m.end() + WINDOW)
            piece = re.sub(r"\s+", " ", body[start:end]).strip()
            if not piece:
                continue
            out.append(f"《{doc_title}》…{piece}…")
            total += len(piece)
            taken += 1
            last_end = end
            if taken >= PER_NAME or total >= limit:
                break
        if total >= limit:
            break
    return "\n".join(out)[:limit + 200]
