"""catfish_wiki_create / catfish_wiki_update 的入口 (11/08 从 catfish_tools 的直通改成先过 wiki_guard):

  create: 相似名查重 (「云堤」vs「云堤公司」) → 关系按类型定义校验 → 引用不存在的页点名 → 再建
  update: base_mtime 对不上 (别人 / 界面 / 蒸馏在你读之后改过) 就不覆写, 把现在的内容交回去合并 →
          关系校验 → 再写 (wiki_files.update_wiki_file 里会先备份、再把 updated 换成今天)
真正的文件读写还在 wiki_files (那边已经到 800 行), 这里只做把关。
"""
from __future__ import annotations

from typing import Any

from . import wiki_files, wiki_guard


_PLAIN_REASON = {"unresolved_relation": "related 里引用的页找不到", "untyped_relation": "关系没写类型 (rel)",
                 "ambiguous_relation": "related 里引用的名字对应好几页"}


def _plain(r: dict[str, Any], missing: list[str]) -> dict[str, Any]:
    """写入结果里的待确认原因由代码换成人话; 引用不存在的页点名。"""
    w = str(r.get("warning") or "")
    for code, text in _PLAIN_REASON.items():
        w = w.replace(code, text)
    if missing:
        w = (w + " " if w else "") + wiki_guard.missing_message(missing)
        r["missing_relations"] = missing
    if w:
        r["warning"] = w
    return r


def tool_wiki_create(args: dict) -> dict[str, Any]:
    title = str(args.get("title", "")).strip()
    related = list(args.get("related") or [])
    from .wiki_names import name_owners  # noqa: PLC0415
    # 名字完全一样的另有规则 (wiki_files: 「已是 X 的名字或别名」); 这里只管「像」
    if title and not args.get("allow_similar") and not name_owners(title):
        if hits := wiki_guard.similar_names(title):
            return {"ok": False, "error": wiki_guard.similar_message(title, hits), "similar": hits,
                    "rel_path": hits[0]["rel_path"]}
    bad, missing = wiki_guard.relation_problems(str(args.get("subtype") or ""), related, self_title=title, rel_path="new:")
    if bad:
        return {"ok": False, "error": "关系用得不对, 改好再建: " + "; ".join(bad), "relation_problems": bad}
    r = wiki_files.tool_wiki_create(args)
    if r.get("ok"):
        r = _fill_props(r, args)
    return _plain(r, missing) if r.get("ok") else r


def _fill_props(r: dict[str, Any], args: dict) -> dict[str, Any]:
    """11/08: 新建的条目按类型定义直接填属性 —— 调用方给的 props + 正文按固定句式抽到的 (证书编号 / 有效期 …),
    不用等事后「整理属性」。类型没有定义就什么都不做。"""
    from . import wiki_types  # noqa: PLC0415
    rel = str(r.get("rel_path") or "")
    got = wiki_files.read_wiki_file(rel)
    if not got.get("ok"):
        return r
    schema = wiki_types.load_schema()
    fm, body = wiki_files._split_frontmatter(str(got["content"]))
    tk = wiki_types.type_of(fm, schema)
    if not tk:
        return r
    props = {k: v for k, v in wiki_types._regex_extract(body, schema[tk]).items() if v}
    given = args.get("props") if isinstance(args.get("props"), dict) else {}
    props.update({str(k): str(v) for k, v in given.items() if str(v).strip()})
    if not props:
        return r
    # 11/26: 坏的值 (不在选项里 / 格式不对 / 跟别的条目重复 …) 跳过并点名, 好的照写 —— 页已经建了, 不能因为一个属性白建
    w = wiki_types.set_props_file(rel, props, schema, partial=True)
    if w.get("ok"):
        r["props"] = w.get("props") or {}
        r["type"] = tk
        if w.get("aligned"):
            r["aligned"] = w["aligned"]
    if w.get("problems") or not w.get("ok"):
        from . import wiki_constraints  # noqa: PLC0415
        r["props_error"] = ("这几项没写进去: " + wiki_constraints.message(w["problems"])) if w.get("problems") else w.get("error")
    return r


def tool_wiki_update(args: dict) -> dict[str, Any]:
    rel = str(args.get("rel_path", "")).strip()
    base = args.get("base_mtime")
    if rel and base not in (None, ""):
        cur = wiki_files.read_wiki_file(rel)
        if cur.get("ok"):
            try:
                stale = abs(float(cur["mtime"]) - float(base)) > 0.5
            except (TypeError, ValueError):
                stale = False
            if stale:
                return {"ok": False, "stale": True, "error": "这一页在你读过之后被改过 (界面 / 后台整理 / 另一段对话), 没有覆写。"
                        "下面是现在的内容, 把你的改动合并进去再带新的 base_mtime 调一次",
                        "rel_path": rel, "mtime": cur["mtime"], "content": cur["content"]}
    content = args.get("content")
    if args.get("content_file") and not content:
        from .wiki_body_file import read_body_file  # noqa: PLC0415
        text, err = read_body_file(str(args["content_file"]))
        if err:
            return {"ok": False, "error": err}
        content = text
    missing: list[str] = []
    if isinstance(content, str) and rel.startswith(("wiki/entities/", "wiki/concepts/")):
        fm, _ = wiki_files._split_frontmatter(content)
        subtype = wiki_files._parse_field(fm, "entity_type") or wiki_files._parse_field(fm, "concept_type") or ""
        title = wiki_files._parse_field(fm, "title") or ""
        bad, missing = wiki_guard.relation_problems(subtype, wiki_files._parse_related(fm), self_title=title, rel_path=rel)
        if bad:
            return {"ok": False, "error": "关系用得不对, 改好再存: " + "; ".join(bad), "relation_problems": bad}
        if problems := _content_prop_problems(rel, fm):
            from . import wiki_constraints  # noqa: PLC0415
            return {"ok": False, "error": "属性不对, 改好再存: " + wiki_constraints.message(problems), "problems": problems}
    r = wiki_files.tool_wiki_update(args)
    return _plain(r, missing) if r.get("ok") else r


def _content_prop_problems(rel: str, new_fm: str) -> list[dict[str, str]]:
    """11/26: 整篇覆写时文件头里的属性也过一遍约束 —— 只查跟现在那份比改了的 (老数据里已有的问题不挡这次修改)。"""
    from . import wiki_constraints, wiki_types  # noqa: PLC0415
    schema = wiki_types.load_schema()
    tk = wiki_types.type_of(new_fm, schema)
    if not tk:
        return []
    tdef = schema[tk]
    cur = wiki_files.read_wiki_file(rel)
    old_props = wiki_types.read_props(wiki_files._split_frontmatter(str(cur["content"]))[0], tdef) if cur.get("ok") else {}
    defs = {p["key"]: p for p in tdef.get("properties") or []}
    new_props = {k: wiki_types._clean(defs[k], v) for k, v in wiki_types.read_props(new_fm, tdef).items()}
    changes = {k: v for k, v in new_props.items() if v != old_props.get(k, "")}
    changes.update({k: "" for k in old_props if k not in new_props})
    return wiki_constraints.check_props(rel, tk, old_props, changes, schema) if changes else []


def tool_wiki_history(args: dict) -> dict[str, Any]:
    """这一页改过几次、每次的备份在哪 (wiki/.history/)。"""
    rel = str(args.get("rel_path", "")).strip()
    if not rel:
        return {"ok": False, "error": "rel_path 不能空"}
    items = wiki_guard.history(wiki_files._catfish_home(), rel)
    return {"ok": True, "rel_path": rel, "count": len(items), "items": items[: int(args.get("limit") or 20)]}
