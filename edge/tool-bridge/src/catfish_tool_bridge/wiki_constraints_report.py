"""约束检查 (11/26): 把知识库里已经存在的不合规列出来, 能一键改的给改法; 属性跟关系对齐。

写入口只拦「这次改到的值」(wiki_constraints), 老数据里已有的问题在这里看:
  知识体系 → 关系整理 →「约束检查」(Companion wiki_constraints_report / wiki_constraints_fix → RPC), 小鲶用
  catfish_wiki_query {type, check: true}。

问题种类 (kind):
  missing_required  必填没填 (只提示)
  bad_value         已经写着的值不合规 (不在选项里 / 不是日期 / 格式不对 …), 改法: 换一个值 (set_prop)
  duplicate         同类里编号 / 登记号 / 信用代码 / 邮箱重复
  rule              属性之间对不上 (颁发日期晚于有效期至 …)
  relation_max      超过「最多几个」(一个人隶属两个部门)
  relation_cycle    隶属链绕成环
  link_relation     属性写了 (人的「部门」= 企发部), 关系里没有 → 改法: 补关系 (add_relation)
  link_prop         关系里有 (隶属 企发部), 属性没填 → 改法: 填属性 (set_prop)
  link_conflict     属性和关系各说各的 (部门写 A, 隶属的是 B) → 本条出边的可以「关系改成跟属性一样」(replace_relation)

自动对齐 (align_links): 改属性时, link 属性的值能唯一对上一页、类型也对、关系里还没有、加了也不超「最多几个」, 就顺手补上那条关系
(dir=in 的写在对方那一页, 比如证书的「持有单位」→ 公司那页加「持有 证书」)。补之前两页都先备份 (wiki/.history)。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import wiki_constraints as wc
from . import wiki_files

logger = logging.getLogger("catfish.tool_bridge.wiki_constraints_report")


# ── 改关系 (只动 related 那一行) ─────────────────────────────────

def _write_related(rel_path: str, mutate) -> dict[str, Any]:
    from . import wiki_guard  # noqa: PLC0415
    if not rel_path.startswith(("wiki/entities/", "wiki/concepts/")):
        return {"ok": False, "error": "只能改自己知识库里的条目"}
    home = wiki_files._catfish_home()
    path = home / rel_path
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"读不到 {rel_path}: {e}"}
    fm, _body = wiki_files._split_frontmatter(content)
    if not fm:
        return {"ok": False, "error": "这条知识没有标准文件头"}
    rows = fm.splitlines()
    at = next((i for i, r in enumerate(rows) if r.startswith("related:")), -1)
    if at >= 0 and not rows[at].rstrip().endswith("]"):
        return {"ok": False, "error": "related 不是一行写完的, 不自动改, 请在页面上改"}
    related = wiki_files._parse_related(fm) if at >= 0 else []
    new_related = mutate([dict(r) for r in related])
    if new_related is None:
        return {"ok": True, "changed": False}
    line = "related: [" + ", ".join(wiki_files._relation_render(r if r.get("rel") else r["name"]) for r in new_related) + "]"
    if at >= 0:
        rows[at] = line
    else:
        rows.append(line)
    new = wiki_guard.touch_updated(content.replace(fm, "\n".join(rows), 1))
    wiki_guard.snapshot(home, rel_path)
    tmp = path.with_suffix(".md.rel-tmp")
    try:
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": f"写 {rel_path} 失败: {e}"}
    return {"ok": True, "changed": True}


def add_relation(rel_path: str, name: str, rel: str) -> dict[str, Any]:
    def mutate(related):
        if any(r.get("name", "").strip().lower() == name.strip().lower() and r.get("rel") == rel for r in related):
            return None
        return related + [{"name": name, "rel": rel}]
    return _write_related(rel_path, mutate)


def replace_relation(rel_path: str, old_name: str, rel: str, name: str) -> dict[str, Any]:
    def mutate(related):
        kept = [r for r in related if not (r.get("name", "").strip().lower() == old_name.strip().lower() and r.get("rel") == rel)]
        if len(kept) == len(related):
            return None
        return kept + [{"name": name, "rel": rel}]
    return _write_related(rel_path, mutate)


# ── 属性 ↔ 关系 ─────────────────────────────────────────────────

def _link_state(kb: wc.KB, node: str, link: dict[str, Any], value: str, edges: set) -> dict[str, Any]:
    """link 属性现在跟关系的关系: target = 属性值对上的页; linked = 关系里 (这个 rel, 对的类型) 连着的页。"""
    rel = str(link.get("rel") or "")
    want_type = str(link.get("type") or "")
    direction = str(link.get("dir") or "out")
    # out = 「本条 rel 对方」, in = 「对方 rel 本条」; 反向写法 (包含 ↔ 隶属) 统一成一种后再找
    flipped, crel = wc.canonical_edge("A", rel, "B", kb.inverse)[0] == "B", wc.canonical_edge("A", rel, "B", kb.inverse)[1]
    node_is_src = (direction == "out") != flipped
    linked = {d for s, r, d in edges if r == crel and s == node} if node_is_src else {s for s, r, d in edges if r == crel and d == node}
    linked = {x for x in linked if kb.type.get(x) == want_type}
    target = kb.resolve(value) if value else None
    if target and kb.type.get(target) != want_type:
        target = None
    return {"rel": rel, "dir": direction, "target": target, "linked": linked}


def align_links(rel_path: str, type_key: str, changes: dict[str, str], schema: dict[str, Any]) -> list[dict[str, str]]:
    """改了 link 属性以后顺手把关系补上 (见模块说明)。失败只记日志, 不影响属性已经写进去。"""
    defs = {p["key"]: p for p in (schema.get(type_key) or {}).get("properties") or []}
    todo = [(k, v) for k, v in changes.items() if v and isinstance((defs.get(k) or {}).get("link"), dict)]
    if not todo:
        return []
    try:
        kb = wc.KB()
    except Exception as e:  # noqa: BLE001
        logger.warning("对齐关系: 读知识库失败: %s", e)
        return []
    out = []
    for key, value in todo:
        link = defs[key]["link"]
        st = _link_state(kb, rel_path, link, value, kb.edges())
        label = defs[key].get("label", key)
        if not st["target"]:
            continue
        if st["target"] in st["linked"]:
            continue
        if st["linked"]:
            out.append({"key": key, "result": "skipped",
                        "message": f"{label}写的是「{kb.label(st['target'])}」, 关系里{st['rel']}的是"
                                   f"「{'」「'.join(kb.label(x) for x in st['linked'])}」, 没动关系 (在约束检查里定)"})
            continue
        if st["dir"] == "out":
            new_out = [(r, d, kb.label(d)) for r, d in kb.out.get(rel_path, [])] + [(st["rel"], st["target"], kb.label(st["target"]))]
            if wc.relation_problems(kb, rel_path, type_key, new_out):
                continue
            w = add_relation(rel_path, kb.label(st["target"]), st["rel"])
            text = f"补了关系: 本条{st['rel']}「{kb.label(st['target'])}」"
        else:
            src = st["target"]
            new_out = [(r, d, kb.label(d)) for r, d in kb.out.get(src, [])] + [(st["rel"], rel_path, kb.label(rel_path))]
            if wc.relation_problems(kb, src, kb.type.get(src, ""), new_out):
                continue
            w = add_relation(src, kb.label(rel_path), st["rel"])
            text = f"补了关系: 「{kb.label(src)}」{st['rel']}本条"
        if w.get("ok") and w.get("changed"):
            out.append({"key": key, "result": "added", "message": text})
    return out


# ── 约束检查 ────────────────────────────────────────────────────

def report(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """{ok, summary: {kind: 条数}, types: [{key, label, entries, missing: {属性名: 条数}}], issues: [...]}。
    params.type 只看一类; params.limit 截 issues (小鲶用, 界面不传 = 全部)。"""
    from . import wiki_types  # noqa: PLC0415
    params = params or {}
    only = str(params.get("type") or "").strip() or None
    kb = wc.KB()
    schema = kb.schema
    if only and only not in schema:
        return {"ok": False, "error": f"没有这个类型: {only}", "types": {k: v.get("label", k) for k, v in schema.items()}}
    edges = kb.edges()
    issues: list[dict[str, Any]] = []
    types: dict[str, dict[str, Any]] = {}
    seen_unique: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for rel, tk, fm, _body in wc.own_typed(schema, only):
        tdef = schema[tk]
        title = wc.title_of(fm, rel)
        props = wiki_types.read_props(fm, tdef)
        t = types.setdefault(tk, {"key": tk, "label": tdef.get("label", tk), "entries": 0, "missing": {}})
        t["entries"] += 1
        base = {"rel_path": rel, "title": title, "type": tk, "type_label": tdef.get("label", tk)}
        missing = wc.missing_required(props, tdef)
        for m in missing:
            t["missing"][m] = t["missing"].get(m, 0) + 1
        if missing:
            issues.append({**base, "kind": "missing_required", "message": f"必填没填: {'、'.join(missing)}"})
        for p in tdef.get("properties") or []:
            v = props.get(p["key"], "")
            if v and (why := wc.value_problem(p, wiki_types._clean(p, v))):
                # 有选项的在界面上给下拉, 说一句「不在选项里」就够 (证书等级 22 个选项, 每行都列一遍太长)
                short = "不在选项里" if p.get("options") and why.startswith("只能是") else why
                issues.append({**base, "kind": "bad_value", "key": p["key"], "label": p.get("label", p["key"]), "value": v,
                               "message": f"{p.get('label', p['key'])}「{v}」{short}", "options": list(p.get("options") or []),
                               "fix": {"op": "set_prop", "key": p["key"]}})
            elif v and p.get("unique"):
                seen_unique.setdefault((tk, p["key"], wc.unique_key(v)), []).append((rel, title))
            link = p.get("link")
            if isinstance(link, dict) and rel in kb.type:
                issues += _link_issues(kb, base, p, link, v, edges)
        for r in wc.rule_problems(props, tdef):
            issues.append({**base, "kind": "rule", "key": r["key"], "message": f"{r['error']} ({r['detail']})"})
    for (tk, key, _norm), hits in seen_unique.items():
        if len(hits) > 1:
            label = next((p.get("label", key) for p in schema[tk]["properties"] if p["key"] == key), key)
            for rel, title in hits:
                others = "」「".join(t for r, t in hits if r != rel)
                issues.append({"rel_path": rel, "title": title, "type": tk, "type_label": schema[tk].get("label", tk),
                               "kind": "duplicate", "key": key,
                               "message": f"{label}跟「{others}」重复 (是同一个东西就在「可能重复」里合并, 不是就改正编号)"})
    issues += _relation_issues(kb, edges, only)
    summary: dict[str, int] = {}
    for i in issues:
        summary[i["kind"]] = summary.get(i["kind"], 0) + 1
    limit = params.get("limit")
    shown = issues[: int(limit)] if limit else issues
    return {"ok": True, "summary": summary, "total": len(issues), "types": list(types.values()), "issues": shown,
            **({"note": f"只列了前 {len(shown)} 条, 全部在知识体系 → 关系整理 →「约束检查」"} if len(shown) < len(issues) else {})}


def _link_issues(kb: wc.KB, base: dict[str, Any], p: dict[str, Any], link: dict[str, Any], value: str,
                 edges: set) -> list[dict[str, Any]]:
    st = _link_state(kb, base["rel_path"], link, value, edges)
    label = p.get("label", p["key"])
    names = [kb.label(x) for x in sorted(st["linked"])]
    if value and st["target"] and st["target"] not in st["linked"]:
        if st["linked"]:
            fix = ({"op": "replace_relation", "rel": st["rel"], "old": names[0], "name": kb.label(st["target"])}
                   if st["dir"] == "out" and len(names) == 1 else None)
            return [{**base, "kind": "link_conflict", "key": p["key"], "message":
                     f"{label}写的是「{kb.label(st['target'])}」, 关系里{st['rel']}的是「{'」「'.join(names)}」",
                     "fix": fix, "alt_fix": {"op": "set_prop", "key": p["key"], "value": names[0]} if len(names) == 1 else None}]
        if st["dir"] == "out":
            fix = {"op": "add_relation", "rel": st["rel"], "name": kb.label(st["target"])}
            text = f"{label}是「{kb.label(st['target'])}」, 关系里没有「{st['rel']} {kb.label(st['target'])}」"
        else:
            fix = {"op": "add_relation", "rel": st["rel"], "name": base["title"], "on": st["target"]}
            text = f"{label}是「{kb.label(st['target'])}」, 「{kb.label(st['target'])}」那页没有「{st['rel']} {base['title']}」"
        return [{**base, "kind": "link_relation", "key": p["key"], "message": text, "fix": fix}]
    if not value and len(st["linked"]) == 1:
        return [{**base, "kind": "link_prop", "key": p["key"], "message": f"{label}没填, 关系里{st['rel']}的是「{names[0]}」",
                 "fix": {"op": "set_prop", "key": p["key"], "value": names[0]}}]
    return []


def _relation_issues(kb: wc.KB, edges: set, only: str | None) -> list[dict[str, Any]]:
    out = []
    for node, tk in kb.type.items():
        if only and tk != only:
            continue
        for spec in kb.max_specs(node):
            got = kb.targets(edges, node, spec)
            if len(got) > int(spec["max"]):
                to = "/".join(kb.type_label(t) for t in spec["to"] if t in kb.schema)
                out.append({"rel_path": node, "title": kb.label(node), "type": tk, "type_label": kb.type_label(tk),
                            "kind": "relation_max", "message": f"最多{spec['rel']}{spec['max']}个{to}, 现在是"
                            f"「{'」「'.join(kb.label(x) for x in sorted(got))}」(在页面上删掉不对的那条)"})
    for rel in sorted(kb.acyclic):
        for cyc in _cycles(edges, rel):
            if only and not any(kb.type.get(n) == only for n in cyc):
                continue
            chain = " → ".join(kb.label(n) for n in cyc + [cyc[0]])
            out.append({"rel_path": cyc[0], "title": kb.label(cyc[0]), "type": kb.type.get(cyc[0], ""),
                        "type_label": kb.type_label(kb.type.get(cyc[0], "")), "kind": "relation_cycle",
                        "message": f"「{rel}」绕成了环: {chain} (删掉其中写反的那条)"})
    return out


def _cycles(edges: set, rel: str) -> list[list[str]]:
    """每个环报一次 (按环上最小的节点起头)。"""
    nxt: dict[str, list[str]] = {}
    for s, r, d in edges:
        if r == rel:
            nxt.setdefault(s, []).append(d)
    found: dict[tuple[str, ...], list[str]] = {}
    for start in sorted(nxt):
        stack = [(start, [start])]
        while stack:
            cur, path = stack.pop()
            for d in nxt.get(cur, []):
                if d == start:
                    i = path.index(min(path))
                    cyc = path[i:] + path[:i]
                    found.setdefault(tuple(sorted(cyc)), cyc)
                elif d not in path and len(path) < 12:
                    stack.append((d, path + [d]))
    return list(found.values())


def fix(params: dict[str, Any]) -> dict[str, Any]:
    """一键改: items=[{rel_path, op: set_prop|add_relation|replace_relation, key?, value?, rel?, name?, old?, on?}]。
    set_prop 照样过写入校验; 改关系照样过关系校验。"""
    from . import wiki_guard, wiki_types  # noqa: PLC0415
    items = params.get("items") or []
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "items 为空"}
    done, errors = 0, []
    for it in items:
        rel = str((it or {}).get("rel_path") or "")
        op = str((it or {}).get("op") or "")
        try:
            if op == "set_prop":
                r = wiki_types.set_props_file(rel, {str(it["key"]): str(it.get("value") or "")})
            elif op in ("add_relation", "replace_relation"):
                target = str(it.get("on") or rel)
                r = _checked_relation_write(wiki_guard, target, it, op)
            else:
                r = {"ok": False, "error": f"不认识的改法: {op or '(空)'}"}
        except (KeyError, TypeError) as e:
            r = {"ok": False, "error": f"参数不对: {e}"}
        if r.get("ok"):
            done += 1
        else:
            errors.append({"rel_path": rel, "error": r.get("error")})
    return {"ok": not errors, "done": done, "errors": errors}


def _checked_relation_write(wiki_guard, target: str, it: dict[str, Any], op: str) -> dict[str, Any]:
    got = wiki_files.read_wiki_file(target)
    if not got.get("ok"):
        return got
    fm, _ = wiki_files._split_frontmatter(str(got["content"]))
    related = [r for r in wiki_files._parse_related(fm)
               if not (op == "replace_relation" and r.get("name", "").strip().lower() == str(it["old"]).strip().lower()
                       and r.get("rel") == it["rel"])]
    related.append({"name": str(it["name"]), "rel": str(it["rel"])})
    subtype = wiki_files._parse_field(fm, "entity_type") or wiki_files._parse_field(fm, "concept_type") or ""
    bad, _missing = wiki_guard.relation_problems(re.sub(r'^["\']|["\']$', "", subtype), related,
                                                 self_title=wc.title_of(fm, target), rel_path=target)
    if bad:
        return {"ok": False, "error": "; ".join(bad)}
    if op == "add_relation":
        return add_relation(target, str(it["name"]), str(it["rel"]))
    return replace_relation(target, str(it["old"]), str(it["rel"]), str(it["name"]))
