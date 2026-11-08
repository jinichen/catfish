"""知识库写入把关 (11/08, docs/WIKI-TYPE-SCHEMA.md §下一步 / 鸿波「小鲶基于本体的知识库还有什么需要优化」):

以前的毛病: 整页覆写没备份、改了不更新时间 (界面的「过期检查」只看 updated)、并发写最后写的赢、
查重只看名字完全一样 (「云堤」「云堤公司」两页)、关系不按类型定义校验 (人→证书写「包含」也收)、
引用不存在的页只说「待确认」不说缺哪一页。

这里四件事, 工具写入 (catfish_wiki_create / update / 改属性) 和记忆整理挪进知识库都走:
  snapshot()           改之前把整份文件存进 wiki/.history/<页>/<时间>.md, 每页留最近 HISTORY_KEEP 份
  touch_updated()      frontmatter 的 updated 换成今天 (没有就补一行)
  similar_names()      去掉公司后缀 / 标点 / 大小写 / 全角之后一样的已有条目 —— 建页前拦, 更新时提醒
  relation_problems()  按类型定义 (contracts/wiki_type_schema.json 的 relations) 查 rel 用得对不对,
                       以及 related 指向的名字有没有页 (缺的点名)
"""
from __future__ import annotations

import logging
import re
import shutil
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.wiki_guard")

HISTORY_KEEP = 20
_SUFFIX = re.compile(r"(集团)?(股份)?(有限)?(责任)?公司$|有限公司|股份有限公司|科技公司$|信息科技|信息技术|科技$|公司$")
_STRIP = re.compile(r"[\s\-_·•・.,，。、:：;；()（）\[\]【】「」『』《》<>“”\"'’‘!！?？~～/\\|]+")


# ── 备份 + 更新时间 ───────────────────────────────────────────────

def history_dir(home: Path, rel_path: str) -> Path:
    return home / "wiki" / ".history" / rel_path.replace("\\", "/").removeprefix("wiki/").removesuffix(".md")


def snapshot(home: Path, rel_path: str) -> str | None:
    """写之前把现在这份存一份。返回备份路径 (没有旧文件就 None)。失败不拦写入, 只记日志。"""
    src = home / rel_path
    if not src.is_file():
        return None
    d = history_dir(home, rel_path)
    try:
        d.mkdir(parents=True, exist_ok=True)
        # 秒 + 秒内纳秒: 同一秒内改好几次 (脚本批量改) 也一份不少, 文件名按时间排, 删了旧的也不会重用名字
        dst = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}.md"
        while dst.exists():
            dst = d / f"{dst.stem}x.md"
        shutil.copy2(src, dst)
        olds = sorted(d.glob("*.md"))
        for old in olds[:-HISTORY_KEEP]:
            old.unlink(missing_ok=True)
        return str(dst)
    except OSError as e:
        logger.warning("知识库备份失败 %s: %s", rel_path, e)
        return None


def history(home: Path, rel_path: str) -> list[dict[str, Any]]:
    d = history_dir(home, rel_path)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md"), reverse=True):
        try:
            out.append({"path": str(p), "saved_at": p.name[:-3], "bytes": p.stat().st_size})
        except OSError:
            continue
    return out


def touch_updated(content: str, today: str | None = None) -> str:
    """frontmatter 的 updated 换成今天; 没有这一行就补在 created 后面 (没 created 就补在 title 后面)。没 frontmatter 原样返回。"""
    from .wiki_files import _split_frontmatter  # noqa: PLC0415
    fm, _body = _split_frontmatter(content)
    if not fm:
        return content
    today = today or date.today().isoformat()
    rows = fm.splitlines()
    for i, row in enumerate(rows):
        if row.startswith("updated:"):
            if row.strip() == f"updated: {today}":
                return content
            rows[i] = f"updated: {today}"
            break
    else:
        anchor = next((i for i, r in enumerate(rows) if r.startswith("created:")), None)
        if anchor is None:
            anchor = next((i for i, r in enumerate(rows) if r.startswith("title:")), len(rows) - 1)
        rows.insert(anchor + 1, f"updated: {today}")
    return content.replace(fm, "\n".join(rows), 1)


# ── 相似名 ───────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    s = _STRIP.sub("", s)
    s = _SUFFIX.sub("", s)
    return s or unicodedata.normalize("NFKC", name or "").strip().lower()


def similar_names(title: str, *, exclude: str = "") -> list[dict[str, str]]:
    """跟已有条目的标题 / 别名归一后一样的 (完全一样的归 wiki_names.name_owners 管, 这里管「像」)。"""
    from . import wiki_files  # noqa: PLC0415
    want = normalize_name(title)
    if not want:
        return []
    exact = (title or "").strip().lower()
    out = []
    for item in wiki_files.list_wiki_files(limit=100000).get("items", []):
        if item["rel_path"] == exclude or item["kind"] not in ("entity", "concept"):
            continue
        if (item.get("ontology_status") or "active").lower() in ("rejected", "deprecated"):
            continue
        names = [item["title"], *(item.get("aliases") or [])]
        if any(n.strip().lower() == exact for n in names):
            continue   # 完全一样的另有规则 (那是同一个东西, 直接指过去)
        hit = next((n for n in names if normalize_name(n) == want), None)
        if hit:
            out.append({"rel_path": item["rel_path"], "title": item["title"], "matched": hit})
    return out


def similar_message(title: str, hits: list[dict[str, str]]) -> str:
    names = "、".join(f"「{h['title']}」({h['rel_path']})" for h in hits[:3])
    return (f"「{title}」跟已有的 {names} 很像, 多半是同一个东西: 是同一个就用 catfish_wiki_update 改那一条 "
            "(可以把这个写法加进 aliases); 确认是两个不同的东西再带 allow_similar=true 建。")


# ── 关系按类型定义校验 ────────────────────────────────────────────

def _type_index() -> dict[str, tuple[str, str]]:
    """名字 (标题 / 别名, 小写) → (rel_path, 类型 key)。"""
    from . import wiki_files, wiki_types  # noqa: PLC0415
    schema = wiki_types.load_schema()
    idx: dict[str, tuple[str, str]] = {}
    for item in wiki_files.list_wiki_files(limit=100000).get("items", []):
        if item["kind"] not in ("entity", "concept"):
            continue
        if (item.get("ontology_status") or "active").lower() in ("rejected", "deprecated"):
            continue
        tk = wiki_types.type_key_of_subtype(item.get("subtype") or "", schema) or ""
        for n in [item["title"], *(item.get("aliases") or [])]:
            idx.setdefault(n.strip().lower(), (item["rel_path"], tk))
    return idx


def _allowed(schema: dict[str, Any], src: str, rel: str, dst: str) -> bool | None:
    """True 允许 / False 不允许 / None 不归类型定义管 (两头有一头没有类型定义, 或 rel 是词表外 / 自定义的)。
    两头都有定义时按定义来: 合法 = src 的定义里有 {rel, to ∋ dst} 或 dst 的定义里有 {rel, from ∋ src}。
    (11/08 按鸿波机器上 425 条已有关系把内置定义补齐过, 剩下拦的十来条都是真写错的: 证书隶属项目、公司遵循项目…)"""
    if not src or not dst or src not in schema or dst not in schema:
        return None
    from . import wiki_relations  # noqa: PLC0415
    builtin, _aliases, fallback = wiki_relations._load_relation_vocab()
    if rel == fallback or rel not in builtin:
        return None
    src_rels = [r for r in (schema.get(src) or {}).get("relations") or [] if r.get("rel") == rel and r.get("to")]
    dst_rels = [r for r in (schema.get(dst) or {}).get("relations") or [] if r.get("rel") == rel and r.get("from")]
    return any(dst in r["to"] for r in src_rels) or any(src in r["from"] for r in dst_rels)


def relation_problems(subtype: str, related: list[Any], *, self_title: str = "") -> tuple[list[str], list[str]]:
    """返回 (不允许的关系, 找不到页的名字)。related 每项 {name, rel} 或裸名字。"""
    from . import wiki_files, wiki_types  # noqa: PLC0415
    if not related:
        return [], []
    schema = wiki_types.load_schema()
    src = wiki_types.type_key_of_subtype(subtype or "", schema) or ""
    idx = _type_index()
    bad, missing = [], []
    for raw in related:
        name = wiki_files._relation_name(raw)
        rel = (raw.get("rel") if isinstance(raw, dict) else None) or ""
        if not name or name.strip().lower() == (self_title or "").strip().lower():
            continue
        hit = idx.get(name.strip().lower())
        if hit is None:
            missing.append(name)
            continue
        if rel and src and _allowed(schema, src, rel, hit[1]) is False:
            label = lambda k: (schema.get(k) or {}).get("label") or k or "未分类"  # noqa: E731
            ok_rels = sorted({r["rel"] for r in (schema.get(src) or {}).get("relations") or [] if hit[1] in (r.get("to") or [])}
                             | {r["rel"] for r in (schema.get(hit[1]) or {}).get("relations") or [] if src in (r.get("from") or [])})
            bad.append(f"{label(src)}→{label(hit[1])}「{name}」不能用「{rel}」"
                       + (f", 这两类之间能用: {' / '.join(ok_rels)}" if ok_rels else ", 类型定义里这两类之间没有这种关系"))
    return bad, missing


def missing_message(missing: list[str]) -> str:
    names = "、".join(f"「{n}」" for n in missing[:5])
    return (f"related 里的 {names}, 知识库里没有这一页 (标题和别名都没对上): 先用 catfish_wiki_search 找找是不是写法不同, "
            "有就改成那一页的标题; 真没有就先建那一页, 或者先不写这条关系。")
