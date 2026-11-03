"""会议纪要的背景材料 (11/3): 知识库里会上提到的条目 + 同一系列的上一次会议。

鸿波 11/3 问「生成纪要的时候会去查知识库、历史和邮件吗」—— 原来都不查, 只看转写。定下来:
- 知识库: 会上提到的人 / 项目 / 单位 / 术语 (标题或别名出现在转写、说话人名、热词里) 的条目正文,
  用来纠正转写错字、补全全名和职务、术语写准;
- 上一次会议: 同一系列 (标题去掉日期 / 期数后相同) 最近一次已出纪要的会议, 带上它的「待定问题」
  和「待办」, 本次纪要写清这些事有没有进展;
- 邮件不带: 哪些邮件跟这场会有关判断不准, 容易把会上没说的事写进纪要。

背景只做上面三件事, 不当作本次会上说过的话 (提示词里写明), 纪要末尾列出参考了哪些。
材料都在员工本机 (~/.catfish/wiki, ~/.catfish/meetings), 跟转写一样只有文字经网关去大模型。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import wiki_files

WIKI_SUBDIRS = ("wiki/entities", "wiki/concepts")
MAX_WIKI_ENTRIES = 8
MAX_ENTRY_CHARS = 600
MAX_WIKI_CHARS = 4000
MIN_NAME_CHARS = 2

# 标题里的日期 / 期数 / 次数, 去掉后才是「系列名」: 「资质集采周会 10.27」「第 3 次项目例会」
_SERIES_NOISE = re.compile(
    r"\d{4}[-./年]\d{1,2}([-./月]\d{1,2}日?)?|\d{1,2}[./月]\d{1,2}日?|第?\s*\d+\s*[期次届轮]|[（(][^）)]*[）)]|\d+"
)


def series_key(title: str) -> str:
    return re.sub(r"[\s\-_·:：,，]+", "", _SERIES_NOISE.sub("", title or "")).lower()


def _entry(path: Path) -> dict[str, Any] | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        size = path.stat().st_size
    except OSError:
        return None
    fm, body = wiki_files._split_frontmatter(content)
    if wiki_files._is_tombstone(size, fm, body):
        return None
    if (wiki_files._parse_field(fm, "ontology_status") or "active").strip() == "pending":
        return None  # 还没确认的实体, 跟热词那边一样不用
    title = (wiki_files._parse_field(fm, "title") or path.stem).strip()
    names = [n.strip() for n in [title, *wiki_files._parse_list(fm, "aliases")] if len(n.strip()) >= MIN_NAME_CHARS]
    text = re.sub(r"\n{3,}", "\n\n", body.strip())
    return {"title": title, "names": names, "body": text}


def wiki_context(catfish_home: Path, haystack: str) -> list[dict[str, str]]:
    """会上提到的知识库条目, 按提到的次数排, 最多 MAX_WIKI_ENTRIES 条、总共 MAX_WIKI_CHARS 字。"""
    hits: list[tuple[int, dict[str, Any], str]] = []
    for sub in WIKI_SUBDIRS:
        d = catfish_home / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            e = _entry(path)
            if not e or not e["body"]:
                continue
            count = sum(haystack.count(n) for n in e["names"])
            if count:
                hits.append((count, e, f"{sub}/{path.name}"))
    hits.sort(key=lambda h: (-h[0], h[1]["title"]))
    out: list[dict[str, str]] = []
    used = 0
    for _, e, rel in hits[:MAX_WIKI_ENTRIES]:
        excerpt = e["body"][:MAX_ENTRY_CHARS]
        if used + len(excerpt) > MAX_WIKI_CHARS:
            break
        used += len(excerpt)
        out.append({"title": e["title"], "rel_path": rel, "excerpt": excerpt})
    return out


def previous_meeting(meeting_dir: Path, meta: dict) -> dict[str, Any] | None:
    """同一系列里, 比这场早、已经出了纪要的最近一场。标题去噪后是空的 (没起标题) 不找。"""
    key = series_key(str(meta.get("title") or ""))
    if not key:
        return None
    mine = str(meta.get("created_at") or "")
    best: tuple[str, dict, dict] | None = None
    for other in meeting_dir.parent.iterdir():
        if other == meeting_dir or not (other / "minutes.json").is_file():
            continue
        try:
            om = json.loads((other / "meta.json").read_text(encoding="utf-8"))
            minutes = json.loads((other / "minutes.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        created = str(om.get("created_at") or "")
        if series_key(str(om.get("title") or "")) != key or not created or (mine and created >= mine):
            continue
        if best is None or created > best[0]:
            best = (created, om, minutes)
    if best is None:
        return None
    created, om, minutes = best
    return {
        "meeting_id": om.get("id"),
        "title": om.get("title") or "",
        "date": created[:10],
        "open_questions": [str(x) for x in minutes.get("open_questions") or []][:10],
        "action_items": [
            {"owner": str(it.get("owner") or ""), "task": str(it.get("task") or ""), "due": str(it.get("due") or "")}
            for it in (minutes.get("action_items") or [])[:15]
            if isinstance(it, dict) and str(it.get("task") or "").strip()
        ],
    }


def gather(meeting_dir: Path, meta: dict, transcript_text: str, names: dict[str, str]) -> dict[str, Any]:
    home = meeting_dir.parent.parent
    haystack = "\n".join([transcript_text, *names.values(), *[str(h) for h in meta.get("hotwords") or []]])
    prev = previous_meeting(meeting_dir, meta)
    return {"wiki": wiki_context(home, haystack), "previous": prev}


def prompt_block(ctx: dict[str, Any]) -> str:
    """拼进给大模型的话里。没有背景就是空串, 提示词跟原来一样。"""
    parts: list[str] = []
    if ctx.get("wiki"):
        parts.append("【知识库里会上提到的条目】")
        parts += [f"- {w['title']}: {w['excerpt']}" for w in ctx["wiki"]]
    prev = ctx.get("previous")
    if prev and (prev["open_questions"] or prev["action_items"]):
        parts.append(f"【上一次同系列会议「{prev['title']}」({prev['date']}) 遗留的事】")
        parts += [f"- 待定问题: {q}" for q in prev["open_questions"]]
        for it in prev["action_items"]:
            extra = " · ".join(x for x in (it["owner"], it["due"] and f"截止 {it['due']}") if x)
            parts.append(f"- 待办: {it['task']}" + (f" ({extra})" if extra else ""))
    if not parts:
        return ""
    return (
        "\n背景材料 (只用来: 纠正转写里的错字、补全人名全称 / 职务和术语写法、判断上次遗留的事本次有没有进展。"
        "背景里的内容**不是**本次会上说的话, 不要写进摘要 / 决议 / 待办; 遗留事项只有本次转写里确实提到才写进展):\n"
        + "\n".join(parts)
        + "\n"
    )


def references_note(ctx: dict[str, Any]) -> str:
    """纪要末尾的「参考了哪些背景」。没有就空串。"""
    bits: list[str] = []
    if ctx.get("wiki"):
        bits.append("知识库 " + "、".join(f"「{w['title']}」" for w in ctx["wiki"]))
    prev = ctx.get("previous")
    if prev:
        bits.append(f"上次会议「{prev['title']}」({prev['date']})")
    return ("参考的背景材料: " + "; ".join(bits) + "。") if bits else ""


def references(ctx: dict[str, Any]) -> dict[str, Any]:
    """写进 minutes.json, 界面 / 以后排查用。"""
    prev = ctx.get("previous")
    return {
        "wiki": [{"title": w["title"], "rel_path": w["rel_path"]} for w in ctx.get("wiki") or []],
        "previous_meeting": {"meeting_id": prev["meeting_id"], "title": prev["title"], "date": prev["date"]} if prev else None,
    }
