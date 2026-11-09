"""分享到部门之前: 这一页发出去是什么样、来源和关系同事那边能不能看到 (11/09)。

# 为什么有这个

11/09 鸿波看「市场部」这页: 正文就一句话, 有价值的东西在来源 `raw/sources/…福富组织架构20260423` 里;
关系指向「中电福富信息科技有限公司」等别的条目。分享只发这一个 md:
  · 来源是发布人电脑上的路径 —— 同事那边点不开, 也不知道找谁要
  · 对话 / 日志来源 (journal: / session:) 更是只在发布人的对话记录里
  · 关系只存名字, 同事那边按名字在**他自己的**知识库里找: 没有就是死链, 恰好同名就连到他那条上 (内容可能完全不同)

# 这里做三件事

1. plan(): 分享对话框的数据 —— 这一页改写后的样子、每条来源 (能不能把原件一起分享)、每条关系
   (同事已经能看到 / 本机有、可以一起分享 / 本机都没有), 以及每一项的内容扫描结果 (凭据拒发、其余警告)。
   一次算好, 员工发之前就看到全部警告, 不用发一次被拦一次。
2. rewrite_for_share(): 发出去那份的改写 (本机文件不动)。来源路径换成人话; 原件一起分享了的换成
   [[原件 · 名称]] 链接, 同事装了就能点开。
3. 原件作为单独一页分享: 标题「原件 · 名称」, 一份原件被好几页引用时只发一次 (wiki_publish 里处理)。

关系本身不改写: 同事那边解析时优先找同一个发布人分享的同名页 (Companion wikiResolve), 不会串到他自己的同名条目上。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.wiki_share")

RAW_PREFIX = "raw/sources/"
SOURCE_TITLE_PREFIX = "原件 · "
#: 原件当一页发, 跟知识页同一个上限 (中央 1MB)
MAX_SOURCE_BYTES = 1024 * 1024
_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".csv"}
_CONVERSATION = re.compile(r"^(journal:|session:|employee_journal$)")
_BODY_RAW = re.compile(r"raw/sources/[^\s\)\]\"'，。、；;,|]+")
_BODY_CONV = re.compile(r"\b(?:journal|session):[0-9A-Za-z_\-:]+")


def _home() -> Path:
    from .wiki_files import _catfish_home  # noqa: PLC0415
    return _catfish_home()


# ── 来源 ──────────────────────────────────────────────────────


def source_kind(ref: str) -> str:
    r = ref.strip().strip("\"'")
    if r.startswith(RAW_PREFIX):
        return "raw"
    if _CONVERSATION.match(r):
        return "conversation"
    if r == "manual":
        return "manual"
    return "other"


def source_name(ref: str) -> str:
    """raw/sources/1783325598-福富组织架构20260423(.md) → 福富组织架构20260423"""
    base = ref.strip().strip("\"'").replace("\\", "/").split("/")[-1]
    base = re.sub(r"\.(md|markdown|txt|csv)$", "", base, flags=re.I)
    return re.sub(r"^\d{9,}[_-]", "", base) or base


def source_doc_title(ref: str) -> str:
    return f"{SOURCE_TITLE_PREFIX}{source_name(ref)}"


def source_key(ref: str) -> str:
    """原件的规范引用 (不带扩展名差异): raw/sources/<文件名去 .md>"""
    r = ref.strip().strip("\"'").replace("\\", "/")
    return re.sub(r"\.(md|markdown)$", "", r, flags=re.I)


def source_path(ref: str) -> Path | None:
    """原件在本机哪 (只认 ~/.catfish/wiki/raw/sources/ 下的文本文件); 不在 / 不是文本返 None"""
    r = ref.strip().strip("\"'").replace("\\", "/")
    if not r.startswith(RAW_PREFIX) or ".." in r:
        return None
    root = (_home() / "wiki" / "raw" / "sources").resolve()
    rel = r[len(RAW_PREFIX):]
    for cand in (rel, f"{rel}.md"):
        p = (root / cand).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            return None
        if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES:
            return p
    return None


def source_publish_path(ref: str) -> str:
    """原件当一页发时的 wiki_rel_path (发布记录按它记 file_id, 再发是更新同一页)"""
    p = source_path(ref)
    name = p.name if p else f"{source_key(ref).split('/')[-1]}.md"
    return f"{RAW_PREFIX}{name}"


def _label(ref: str, attached: set[str]) -> str:
    kind = source_kind(ref)
    if kind == "raw":
        if source_key(ref) in attached:
            return source_doc_title(ref)
        return f"原件「{source_name(ref)}」(在发布人那里, 没随这页分享)"
    if kind == "conversation":
        return "发布人的对话记录 (没随这页分享)"
    return ref.strip().strip("\"'")


# ── 改写发出去那份 ─────────────────────────────────────────────


def _replace_sources_field(fm: str, new_values: list[str]) -> str:
    """frontmatter 里的 sources (一行 [..] 或多行 - x) 换成 new_values, 其余行一字不动"""
    lines = fm.split("\n")
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        ln = lines[i]
        if not replaced and re.match(r"^sources\s*:", ln):
            replaced = True
            i += 1
            # 多行写法: 后面缩进的 - 项都属于它
            while i < len(lines) and re.match(r"^\s+-\s", lines[i]):
                i += 1
            out.append("sources: [" + ", ".join(json.dumps(v, ensure_ascii=False) for v in new_values) + "]")
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def source_refs(fm: str) -> list[str]:
    """frontmatter 的 sources: 一行 [..] (wiki_files._parse_list 那种) 或多行 - x 都认。
    多行写法 Companion 读不出来, 但发出去的头部里一样会带着本机路径, 所以这里也要认。"""
    from .wiki_files import _parse_list  # noqa: PLC0415
    refs = _parse_list(fm, "sources")
    if refs:
        return refs
    lines = fm.split("\n")
    for i, ln in enumerate(lines):
        if re.match(r"^sources\s*:\s*$", ln):
            out = []
            for nxt in lines[i + 1:]:
                m = re.match(r"^\s+-\s+(.*)$", nxt)
                if not m:
                    break
                v = m.group(1).strip().strip("\"'")
                if v:
                    out.append(v)
            return out
    return []


def rewrite_for_share(fm: str, body: str, attached: set[str] | None = None) -> tuple[str, str]:
    """发出去那份: 来源换成人话 / 原件链接, 正文里的本机路径和对话编号同样处理。本机文件不动。
    attached: 一起分享了的原件 (source_key)。"""
    attached = attached or set()
    refs = source_refs(fm)
    new_fm = _replace_sources_field(fm, [_label(r, attached) for r in refs]) if refs else fm

    def raw_sub(m: re.Match[str]) -> str:
        ref = m.group(0)
        if source_key(ref) in attached:
            return f"[[{source_doc_title(ref)}]]"
        return f"原件「{source_name(ref)}」(在发布人那里, 没随这页分享)"

    new_body = _BODY_RAW.sub(raw_sub, body)
    new_body = _BODY_CONV.sub("发布人的对话记录", new_body)
    return new_fm, new_body


# ── 扫描 (跟 wiki_publish 同一套) ──────────────────────────────


def scan(name: str, text: str) -> dict[str, Any]:
    """{blocked: 拒发原因 | None, warnings: [...]} —— 凭据拒发, 个人信息 / 内网地址 / 自配敏感词警告"""
    from . import wiki_publish  # noqa: PLC0415
    leaks, warnings = wiki_publish.scan_text(name, text)
    blocked = ("含疑似凭据 (密码 / 密钥 / 私钥), 不能分享: " + "; ".join(leaks)) if leaks else None
    return {"blocked": blocked, "warnings": warnings}


# ── 中央上已经有哪些 (同事能看到的) ─────────────────────────────


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_·・.,，。:：()（）「」\"'【】\[\]]", "", (s or "").lower())


def hub_titles() -> set[str] | None:
    """我能看到的部门知识库里已有的页标题 (归一化)。拿不到 (没登录 / 中央连不上) 返 None。"""
    import httpx  # noqa: PLC0415

    from . import wiki_publish  # noqa: PLC0415
    token = wiki_publish._read_id_token()
    if not token:
        return None
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{wiki_publish.GATEWAY_URL}/v1/wiki/documents?include_stale=false",
                           headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            return None
        docs = r.json().get("documents") or []
    except Exception as e:  # noqa: BLE001
        logger.info("读部门知识库列表失败 (按「不知道」处理): %s", e)
        return None
    return {_norm(str(d.get("title") or "")) for d in docs if not d.get("stale_after_unpublish")}


# ── 分享对话框的数据 ───────────────────────────────────────────


def _resolve_local(name: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """按标题 / 别名唯一命中本机条目 (多个命中不猜)"""
    n = _norm(name)
    if not n:
        return None
    hits = [it for it in items if _norm(it["title"]) == n or any(_norm(a) == n for a in it.get("aliases") or [])]
    return hits[0] if len(hits) == 1 else None


def plan(params: dict[str, Any]) -> dict[str, Any]:
    from . import wiki_publish  # noqa: PLC0415
    from .wiki_files import _merge_related_with_body, _parse_related, list_wiki_files  # noqa: PLC0415

    rel_path = str(params.get("wiki_rel_path") or "").strip()
    if not rel_path.startswith("wiki/") or ".." in rel_path:
        return {"ok": False, "error": f"wiki_rel_path 不合法: {rel_path!r}"}
    path = (_home() / rel_path)
    if not path.is_file():
        return {"ok": False, "error": f"知识页不存在: {rel_path}"}
    text = path.read_text(encoding="utf-8")
    fm, body = wiki_publish._parse_frontmatter(text)
    title = wiki_publish._extract_field(fm, "title") or path.stem
    if wiki_publish._extract_field(fm, "entity_type") == "customer":
        return {"ok": False, "error": "客户档案只存在你本机, 不能整页分享到部门 (里面有客户原话)。"
                                      "要让主管看到进展, 用 设置 →「🤝 销售跟进」→「发布给主管」, 只发阶段 / 金额 / 下一步。"}
    attached = {source_key(r) for r in (params.get("attach_sources") or []) if isinstance(r, str)}

    published = wiki_publish._load_publish_state()
    on_hub = hub_titles()
    items = list_wiki_files(limit=100000).get("items", [])

    # 来源: 原件能不能一起分享 (本机有、是文本、不超 1MB), 已经分享过没有
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in source_refs(fm):
        kind = source_kind(ref)
        key = source_key(ref) if kind == "raw" else ref
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {"ref": ref, "kind": kind, "label": _label(ref, set())}
        if kind == "raw":
            p = source_path(ref)
            size = p.stat().st_size if p else 0
            entry.update(
                key=key,
                title=source_doc_title(ref),
                publish_path=source_publish_path(ref),
                size_bytes=size,
                attachable=bool(p) and size <= MAX_SOURCE_BYTES,
                reason=None if p and size <= MAX_SOURCE_BYTES else ("原件超过 1MB" if p else "本机找不到原件或不是文本"),
                shared=source_publish_path(ref) in published
                or (on_hub is not None and _norm(source_doc_title(ref)) in on_hub),
                scan=scan(p.name, p.read_text(encoding="utf-8", errors="replace")) if p and size <= MAX_SOURCE_BYTES else None,
            )
        sources.append(entry)

    # 关系: 同事能看到 (已在部门知识库) / 本机有、可以一起分享 / 本机都没有
    relations: list[dict[str, Any]] = []
    for r in _merge_related_with_body(_parse_related(fm), body):
        name = str(r["name"])
        if name.startswith(SOURCE_TITLE_PREFIX):
            continue
        local = _resolve_local(name, items)
        on_hub_by_name = on_hub is not None and _norm(name) in on_hub
        entry = {"name": name, "rel": r.get("rel")}
        if local and local["rel_path"] != rel_path:
            shared = local["rel_path"] in published or on_hub_by_name or (on_hub is not None and _norm(local["title"]) in on_hub)
            entry.update(status="shared" if shared else "local", rel_path=local["rel_path"], title=local["title"])
            if not shared:
                ltext = (_home() / local["rel_path"]).read_text(encoding="utf-8", errors="replace")
                lfm, _ = wiki_publish._parse_frontmatter(ltext)
                if wiki_publish._extract_field(lfm, "entity_type") == "customer":
                    entry.update(status="private", reason="客户档案只存在你本机, 不能分享到部门")
                else:
                    entry["scan"] = scan(Path(local["rel_path"]).name, ltext)
        elif local:
            continue  # 指向自己
        else:
            entry["status"] = "shared" if on_hub_by_name else "missing"
        relations.append(entry)

    new_fm, new_body = rewrite_for_share(fm, body, attached)
    from .wiki_files import _parse_field, _parse_list  # noqa: PLC0415
    # 11/09: 预览按知识页的样子显示 (头部字段一条条列, 正文渲染), 不给员工看 YAML; 解析口径跟知识页同一套
    fields = {
        "title": _parse_field(new_fm, "title") or title,
        "entity_type": _parse_field(new_fm, "entity_type") or _parse_field(new_fm, "concept_type"),
        "aliases": _parse_list(new_fm, "aliases"),
        "tags": _parse_list(new_fm, "tags"),
        "related": _parse_related(new_fm),
        "sources": source_refs(new_fm),
        # 从原件里摘的原话 —— 会跟着发出去, 预览里要单独让员工看到
        "evidence": _parse_list(new_fm, "evidence"),
    }
    return {
        "ok": True,
        "title": title,
        "rel_path": rel_path,
        "shared_before": rel_path in published,
        "hub_known": on_hub is not None,
        "scan": scan(path.name, f"---\n{new_fm}\n---\n\n{new_body}"),
        "preview": {"frontmatter_yaml": new_fm, "body_md": new_body, "fields": fields},
        "sources": sources,
        "relations": relations,
    }
