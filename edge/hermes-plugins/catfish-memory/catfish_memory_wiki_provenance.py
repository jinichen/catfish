"""9/17: related[].rel 受控词表 + sources 粒度 —— 从 catfish_memory_wiki.py 拆出
(那个文件已经 724 行, 再加这一段就过 800 红线)。

被 catfish_memory_wiki._write_wiki_files 调用; 词表在 edge/contracts/wiki_relation_vocab.json,
Rust (wiki_frontmatter.rs) 和 TS (wikiRelationshipTasks.ts) 读同一份。
"""
from __future__ import annotations

from pathlib import Path
import re

try:
    from .catfish_memory_helpers import logger  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import logger  # noqa: F401
try:
    from .catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body


# ─────────────────────────────────────────────────────────────
# 9/17: related[].rel 受控词表 + sources 粒度 —— 写入侧真的验, 不只在 prompt 里要求
#
# 本机实测 (752 篇): 500 多条 related 里 418 条 rel=「关联」(84%), sources 里 51 篇
# 还是 `employee_journal` 这种等于没写的值。prompt 从 7/9、8/4 起就要求了
# `journal:YYYY-MM-DD` 和带类型的 rel, 但写入侧从没验过 —— "要求了但不验证"跟
# "没要求"的结果一样。这一段把两件事变成确定性的:
#   · rel 归一到 edge/contracts/wiki_relation_vocab.json, 表外 → 兜底「关联」并
#     记 WARNING; 「关联」不算有类型 → pending, 由人在关系工作台定
#   · sources 只收 journal:YYYY-MM-DD / raw/sources/<stem> / manual; 日期还要真
#     出现在本轮蒸馏读进去的日志里 (LLM 编的日期直接丢); 空了就用本轮实际读过的
#     日志日期 + 上传资料回填 —— 每一篇都能回答"这条从哪来"
# ─────────────────────────────────────────────────────────────


def _load_relation_vocab() -> tuple[frozenset, dict, str]:
    import json as _json
    p = Path(__file__).resolve().parents[2] / "contracts" / "wiki_relation_vocab.json"
    try:
        d = _json.loads(p.read_text(encoding="utf-8"))
        return frozenset(d["relations"].keys()), dict(d["aliases"]), str(d["fallback"])
    except (OSError, ValueError, KeyError) as e:
        logger.warning("读不到关系词表 %s (%s), 关系归一化本次跳过", p, e)
        return frozenset(), {}, "关联"


_RELATIONS, _RELATION_ALIASES, _RELATION_FALLBACK = _load_relation_vocab()


def _canon_relation(raw: str) -> tuple[str, bool]:
    """把一个 rel 归一到词表。返 (归一化后的值, 是否落在词表内)。

    表外**不保留原词**: 归到兜底「关联」。词表要长就改 contracts 文件, 不让
    它在数据里悄悄长 —— 9/17 之前就是这么长出 持有主体/采用口径/同期项目 的。
    """
    v = (raw or "").strip().strip('"').strip("'")
    if not v:
        return _RELATION_FALLBACK, False
    if not _RELATIONS:  # 词表读不到 → 原样放行
        return v, True
    canon = _RELATION_ALIASES.get(v, v)
    if canon in _RELATIONS:
        return canon, True
    return _RELATION_FALLBACK, False


_TYPED_REL_RE = re.compile(r'(\{[^{}]*?\brel\s*:\s*)("?)([^",}]*)("?)(\s*[,}])')


def _normalize_relations(rel_path: str, content: str) -> str:
    """写盘前把 related 里每条 typed relation 的 rel 归一到词表。

    只改 `{name, rel}` 块里的 rel 值, 不动裸 wikilink (那本来就是 untyped →
    pending)。表外的值归兜底并 WARNING。
    """
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return content
    m = re.search(r"^related:\s*\[(.*)\]\s*$", fm, re.MULTILINE)
    if not m:
        return content
    changed = False
    fallen: list[str] = []

    def _sub(mm: "re.Match[str]") -> str:
        nonlocal changed
        raw = mm.group(3).strip()
        canon, known = _canon_relation(raw)
        if not known and raw and canon != raw:
            fallen.append(raw)
        if canon == raw:
            return mm.group(0)
        changed = True
        return f'{mm.group(1)}"{canon}"{mm.group(5)}'

    new_line = "related: [" + _TYPED_REL_RE.sub(_sub, m.group(1)) + "]"
    if fallen:
        logger.warning(
            "catfish-memory wiki: %s 的关系类型 %s 不在词表内, 归到「%s」等人确认 "
            "(词表: contracts/wiki_relation_vocab.json)",
            rel_path, "/".join(sorted(set(fallen))), _RELATION_FALLBACK,
        )
    if not changed:
        return content
    new_fm = fm[: m.start()] + new_line + fm[m.end():]
    return f"---\n{new_fm}\n---\n{body}"


def _rel_of_item(item: str) -> str:
    """typed relation 块里的 rel 值; 裸 wikilink 返空。"""
    v = item.strip()
    if not v.startswith("{"):
        return ""
    mm = re.search(r'\brel\s*:\s*"?([^",}]*)"?', v)
    return mm.group(1).strip() if mm else ""


def _is_untyped_relation(item: str) -> bool:
    """裸 wikilink、没 rel、rel 为空、rel 是兜底「关联」—— 都算没类型。"""
    rel = _rel_of_item(item)
    return not rel or rel == _RELATION_FALLBACK


class Provenance:
    """本轮蒸馏**实际读进去**的资料 —— sources 校验和回填的依据。

    journal_dates: 日志里 `## [YYYY-MM-DD HH:MM]` 抬头出现过的日期
    raw_sources:   本轮喂给 Analysis 的 wiki/raw/sources/<stem>
    """

    __slots__ = ("journal_dates", "raw_sources")

    def __init__(self, journal_dates=(), raw_sources=()):
        self.journal_dates = frozenset(journal_dates)
        self.raw_sources = frozenset(raw_sources)

    @classmethod
    def from_run(cls, journal_text: str, source_paths) -> "Provenance":
        dates = re.findall(r"^## \[(\d{4}-\d{2}-\d{2})", journal_text or "", re.MULTILINE)
        stems = [Path(str(p)).stem for p in (source_paths or [])]
        return cls(dates, stems)

    def fill(self) -> list[str]:
        out = [f"journal:{d}" for d in sorted(self.journal_dates)]
        out += [f"raw/sources/{s}" for s in sorted(self.raw_sources)]
        return out


_JOURNAL_SRC_RE = re.compile(r"^journal:\s*(\d{4}-\d{2}-\d{2})$")
_BARE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _canon_source(raw: str) -> str | None:
    """一条 sources 归一; 认不出的返 None (会被丢掉)。

    收: journal:YYYY-MM-DD · raw/sources/<stem> (wiki/ 前缀和 .md 后缀都剥) · manual
    丢: employee_journal / 空 / 其它裸词
    """
    v = (raw or "").strip().strip('"').strip("'").strip()
    if not v:
        return None
    if v == "manual":
        return v
    m = _JOURNAL_SRC_RE.match(v)
    if m:
        return f"journal:{m.group(1)}"
    if _BARE_DATE_RE.match(v):
        return f"journal:{v}"
    p = v.replace("\\", "/")
    if p.startswith("wiki/"):
        p = p[len("wiki/"):]
    if p.startswith("raw/sources/"):
        stem = p[len("raw/sources/"):]
        if stem.endswith(".md"):
            stem = stem[:-3]
        return f"raw/sources/{stem}" if stem else None
    return None


def _normalize_sources(rel_path: str, content: str, provenance: "Provenance | None" = None) -> str:
    """写盘前把 sources 收敛到可回查的粒度。

    provenance 给了就做两件事: 丢掉本轮日志里不存在的日期 (LLM 编的), 以及
    sources 空了就用本轮实际读过的资料回填。merge 之后的二次调用不传
    provenance —— 老条目带的历史来源是别的轮次的, 不能拿这一轮去否定它。
    """
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return content
    m = re.search(r"^sources:\s*\[(.*)\]\s*$", fm, re.MULTILINE)
    raw_items = _parse_frontmatter_lists(fm).get("sources", []) if m else []
    kept: list[str] = []
    dropped: list[str] = []
    for item in raw_items:
        canon = _canon_source(item)
        if canon is None:
            dropped.append(item)
            continue
        if provenance is not None and canon.startswith("journal:"):
            if provenance.journal_dates and canon[len("journal:"):] not in provenance.journal_dates:
                dropped.append(item)
                continue
        if canon not in kept:
            kept.append(canon)
    filled = False
    if not kept and provenance is not None:
        kept = provenance.fill()
        filled = bool(kept)
    if dropped:
        logger.warning(
            "catfish-memory wiki: %s 的 sources 丢掉 %d 条认不出/对不上的来源 (%s)%s",
            rel_path, len(dropped), ", ".join(dropped[:5]),
            " —— 已按本轮实际读过的资料回填" if filled else "",
        )
    elif filled:
        logger.info("catfish-memory wiki: %s 没写 sources, 按本轮实际读过的资料回填 %d 条", rel_path, len(kept))
    if kept == [c.strip().strip('"') for c in raw_items] and not filled:
        return content
    new_line = "sources: [" + ", ".join(f'"{s}"' for s in kept) + "]"
    if m:
        new_fm = fm[: m.start()] + new_line + fm[m.end():]
    else:
        new_fm = fm.rstrip() + "\n" + new_line
    return f"---\n{new_fm}\n---\n{body}"
