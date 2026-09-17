"""9/17: related[].rel 受控词表 + sources 粒度 —— 从 catfish_memory_wiki.py 拆出
(那个文件已经 724 行, 再加这一段就过 800 红线)。

被 catfish_memory_wiki._write_wiki_files 调用; 词表在 edge/contracts/wiki_relation_vocab.json,
Rust (wiki_frontmatter.rs) 和 TS (wikiRelationshipTasks.ts) 读同一份。
"""
from __future__ import annotations

import logging
from pathlib import Path
import re

# 不从 helpers 拿 logger: helpers → wiki → 这里, 直接 import 本模块会绕成环。
# logging.getLogger 同名返回同一对象, 跟 helpers 的是一个。
logger = logging.getLogger("catfish.memory.plugin")
try:
    from .catfish_memory_fm import _parse_frontmatter_lists, _rel_item_name, _split_frontmatter_body, _split_top_level
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_fm import _parse_frontmatter_lists, _rel_item_name, _split_frontmatter_body, _split_top_level


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


# ─────────────────────────────────────────────────────────────
# 9/17 (semantica 第 2 条): 冲突检测 —— 合并前 diff, 不静默覆盖
#
# _merge_wiki_file 的策略原话: "title / *_type: 用新 (允许 reclassify)"; related 并集
# 时 "新的带 rel → 升级"。也就是说同一个实体这次蒸馏说 department、上次说 org,
# 或者「中电福富」的关系上次是 隶属 这次是 协作 —— **后写的赢, 没有任何人知道**。
# 员工在 UI 里改过的条目 (_append_as_appendix) 正文受保护, 但 frontmatter 的
# related 走的是同一套并集, 关系照样被换。
#
# 这里做的事: 写盘前把旧文件和最终内容的 frontmatter 对一遍, 关键字段 (类型、
# typed relation 的 rel) 值不同就
#   1. **保留旧值** (先到先得, 跟 authored_by 的精神一致: 机器可以提出, 改不了人定的)
#   2. 把新值记进 `conflicts:` 字段, 关系工作台列成"冲突"任务, 员工二选一
# 只看 frontmatter, 不看正文 —— 正文的冲突要靠语义判断, 不是 diff 该干的事。
#
# frontmatter 形态 (跟 related 同款 inline map, 读侧 wiki_read.rs 用 split_top_level 解):
#   conflicts: [{field: "entity_type", current: "org", proposed: "department", seen: "journal:2026-09-17"},
#               {field: "rel:中电福富", current: "隶属", proposed: "协作", seen: "..."}]
# ─────────────────────────────────────────────────────────────

_CONFLICT_SCALAR_FIELDS = ("entity_type", "concept_type")
_CONFLICT_KV_RE = re.compile(r'(\w+)\s*:\s*"((?:[^"\\]|\\.)*)"')


def _fm_scalar(fm: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.*?)\s*$", fm, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""


def _typed_rels(fm: str) -> dict[str, str]:
    """name → rel, 只收带类型的 (兜底「关联」和裸 wikilink 不算, 它们本来就是"还没定")。"""
    out: dict[str, str] = {}
    for item in _parse_frontmatter_lists(fm).get("related", []):
        if _is_untyped_relation(item):
            continue
        name = _rel_item_name(item).strip()
        rel = _rel_of_item(item)
        if name and rel:
            out[name] = rel
    return out


def parse_conflicts(fm: str) -> list[dict[str, str]]:
    m = re.search(r"^conflicts:\s*\[(.*)\]\s*$", fm, re.MULTILINE)
    if not m:
        return []
    out = []
    for item in _split_top_level(m.group(1)):
        if not item.startswith("{"):
            continue
        d = {k: v.replace('\\"', '"') for k, v in _CONFLICT_KV_RE.findall(item)}
        if d.get("field") and "proposed" in d:
            out.append(d)
    return out


def _quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_conflicts(conflicts: list[dict[str, str]]) -> str:
    items = []
    for c in conflicts:
        keys = ("field", "current", "proposed", "seen")
        items.append("{" + ", ".join(f"{k}: {_quote(c.get(k, ''))}" for k in keys if k in c) + "}")
    return "conflicts: [" + ", ".join(items) + "]"


def detect_conflicts(old_text: str, new_text: str, seen: str = "") -> list[dict[str, str]]:
    """旧文件 vs 将要写入的内容, frontmatter 关键字段值不同的清单。空 = 没冲突。"""
    old_fm, _ = _split_frontmatter_body(old_text)
    new_fm, _ = _split_frontmatter_body(new_text)
    if not old_fm or not new_fm:
        return []
    found: list[dict[str, str]] = []
    for key in _CONFLICT_SCALAR_FIELDS:
        cur, new = _fm_scalar(old_fm, key), _fm_scalar(new_fm, key)
        if cur and new and cur.lower() != new.lower():
            found.append({"field": key, "current": cur, "proposed": new, "seen": seen})
    old_rels, new_rels = _typed_rels(old_fm), _typed_rels(new_fm)
    for name, rel in new_rels.items():
        cur = old_rels.get(name)
        if cur and cur != rel:
            found.append({"field": f"rel:{name}", "current": cur, "proposed": rel, "seen": seen})
    return found


def _restore_old_value(fm: str, conflict: dict[str, str]) -> str:
    field, cur = conflict["field"], conflict["current"]
    if field.startswith("rel:"):
        name = field[4:]
        m = re.search(r"^related:\s*\[(.*)\]\s*$", fm, re.MULTILINE)
        if not m:
            return fm
        items = []
        for item in _split_top_level(m.group(1)):
            if item.startswith("{") and _rel_item_name(item).strip() == name:
                item = re.sub(r'(\brel\s*:\s*)"?[^",}]*"?', lambda mm: f'{mm.group(1)}"{cur}"', item, count=1)
            items.append(item)
        return fm[: m.start()] + "related: [" + ", ".join(items) + "]" + fm[m.end():]
    return re.sub(rf"^{re.escape(field)}:\s*.*$", f"{field}: {cur}", fm, count=1, flags=re.MULTILINE)


def record_conflicts(rel_path: str, final_text: str, old_text: str, seen: str = "") -> str:
    """把 old→final 的冲突记进 final 的 `conflicts:`, 并把冲突字段改回旧值。

    旧文件里已有的 conflicts 也带过来 (merge 会把 frontmatter 换成新的, 不带就丢了);
    同一个 field 只留最新的 proposed; 员工已经处理过 (旧值和新值一致) 的自动消掉。
    """
    old_fm, _ = _split_frontmatter_body(old_text)
    fm, body = _split_frontmatter_body(final_text)
    if not fm:
        return final_text
    fresh = detect_conflicts(old_text, final_text, seen)
    carried = parse_conflicts(old_fm) + parse_conflicts(fm)
    merged: dict[str, dict[str, str]] = {}
    for c in carried + fresh:
        merged[c["field"]] = c
    for c in fresh:
        fm = _restore_old_value(fm, c)
    # 已经不冲突的 (当前值 == proposed, 说明员工采纳了) 自动清掉
    live = []
    for c in merged.values():
        f = c["field"]
        now = _typed_rels(fm).get(f[4:], "") if f.startswith("rel:") else _fm_scalar(fm, f)
        if now and now.lower() == c.get("proposed", "").lower():
            continue
        if now:
            c["current"] = now
        live.append(c)
    if fresh:
        logger.warning(
            "catfish-memory wiki: %s 有 %d 处跟已有内容冲突 (%s) —— 保留旧值, 新值记进 conflicts 等员工定",
            rel_path, len(fresh), "; ".join(f"{c['field']}: {c['current']} vs {c['proposed']}" for c in fresh),
        )
    fm = re.sub(r"^conflicts:\s*\[.*\]\s*$\n?", "", fm, flags=re.MULTILINE).rstrip("\n")
    if live:
        fm = fm + "\n" + _render_conflicts(live)
    return f"---\n{fm}\n---\n{body}"
