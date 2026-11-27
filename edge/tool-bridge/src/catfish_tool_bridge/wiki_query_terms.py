"""按属性查的「业务词 → 属性」对应 + 知识库目录 (11/27)。

起因 (11/27 试验, 本机知识库副本 + 22 道员工口吻的问题, qwen-flash 每题跑 2 遍):
- 线上小鲶核心工具里只有 catfish_wiki_search, 按属性查 (catfish_wiki_query) 藏在 tool_search 后面, 模型不会去找。
  「哪些证书年底前到期 / 谁维护的有几张」只能一条条搜、一条条读再数: 准确率 30%, 每题 token 中位数 28.7 万
  (放宽到 30 轮能到 75%, 但每题约 170 万 token)。
- 把查询并进 catfish_wiki_search (mode=query / browse), type 和 field 能写中文业务说法: 准确率 95%,
  每题 1.3 万 token, 3 次工具调用。直接给原样的 catfish_wiki_query (只认英文 key) 也是 95%, 但 token 多一倍
  —— 模型先写「维护人」被拒, 再按报错改成 maintainer。

对应从哪来 (按顺序认, 认到就停):
- 类型: key → label → 受控词表 (wiki_type_vocab.json, 「资质」「软著」「员工」这类)。
- 属性: key → label → 属性定义里的 terms (wiki_type_schema.json, 「到期」→ valid_until)。
同一类型里一个说法只能对到一个属性 (tests/test_wiki_query_terms.py 钉住), 对不上的原样交给 query 报错。

对上了也要拿员工自己的数据核对: 按业务说法对到的属性如果在他的知识库里一条都没填, 回一句提示,
免得「查不到」被当成「没有」。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

#: browse 里每个属性最多给几个常见取值; 太长的值 (认证范围这种整句) 不当取值给
_TOP_VALUES = 6
_VALUE_MAX = 40
#: 等于条件查不到时, 最多给几个相近的真实取值
_NEAR_MAX = 6


def resolve_type(name: Any, schema: dict[str, dict[str, Any]]) -> str:
    from . import wiki_types  # noqa: PLC0415
    raw = str(name or "").strip()
    if not raw or raw in schema:
        return raw
    for key, tdef in schema.items():
        if raw == tdef.get("label"):
            return key
    return wiki_types.type_key_of_subtype(raw, schema) or raw


def _props(tdef: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in tdef.get("properties") or [] if isinstance(p, dict) and p.get("key")]


def resolve_field(name: Any, tdef: dict[str, Any]) -> tuple[str, str]:
    """→ (属性 key, 怎么对上的: key / label / term / "")。"""
    raw = str(name or "").strip()
    props = _props(tdef)
    for how, pick in (("key", lambda p: [p["key"]]), ("label", lambda p: [p.get("label")]),
                      ("term", lambda p: p.get("terms") or [])):
        for p in props:
            if raw in pick(p):
                return p["key"], how
    return raw, ""


def translate(args: dict[str, Any], schema: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """把 type / where.field / sort / fields 里的中文说法换成 key。→ (新参数, {原说法: key}, 按 term 对上的 key)。"""
    out = dict(args)
    resolved: dict[str, str] = {}
    by_term: list[str] = []
    raw_type = str(args.get("type") or "").strip()
    type_key = resolve_type(raw_type, schema)
    if type_key != raw_type:
        resolved[raw_type] = type_key
    out["type"] = type_key
    tdef = schema.get(type_key)
    if not tdef:
        return out, resolved, by_term

    def one(name: Any) -> Any:
        if not isinstance(name, str):
            return name
        key, how = resolve_field(name, tdef)
        if how in ("label", "term"):
            resolved[name] = key
        if how == "term" and key not in by_term:
            by_term.append(key)
        return key

    where = args.get("where") or []
    if isinstance(where, dict):
        where = [{"field": k, **(v if isinstance(v, dict) else {"eq": v})} for k, v in where.items()]
    out["where"] = [{**c, "field": one(c["field"])} if isinstance(c, dict) and c.get("field") else c for c in where]
    if args.get("sort"):
        out["sort"] = one(args["sort"])
    if isinstance(args.get("fields"), list):
        out["fields"] = [one(f) for f in args["fields"]]
    return out, resolved, by_term


def _values(type_key: str, schema: dict[str, dict[str, Any]]) -> tuple[int, dict[str, Counter]]:
    from . import wiki_types  # noqa: PLC0415
    tdef = schema[type_key]
    total, seen = 0, {p["key"]: Counter() for p in _props(tdef)}
    for _rel, fm, _body in wiki_types._iter_typed(type_key, schema):
        total += 1
        for k, v in wiki_types.read_props(fm, tdef).items():
            if k in seen:
                seen[k][v] += 1
    return total, seen


def browse(args: dict[str, Any]) -> dict[str, Any]:
    """知识库目录: 不带 type → 有哪些类型、各几条; 带 type → 每个属性的名字、说法、填了几条、常见取值。"""
    from . import wiki_types  # noqa: PLC0415
    schema = wiki_types.load_schema()
    raw = str(args.get("type") or "").strip()
    if not raw:
        types = {}
        for key, tdef in schema.items():
            total, _ = _values(key, schema)
            types[key] = {"label": tdef.get("label", key), "count": total,
                          "properties": {p["key"]: p.get("label", p["key"]) for p in _props(tdef)}}
        return {"ok": True, "types": types, "note": "带 type 再看一次: 每个属性填了几条、常见取值"}
    key = resolve_type(raw, schema)
    if key not in schema:
        return {"ok": False, "error": f"没有这个类型: {raw}", "types": {k: v.get("label", k) for k, v in schema.items()}}
    tdef = schema[key]
    total, seen = _values(key, schema)
    props = {}
    for p in _props(tdef):
        c = seen[p["key"]]
        info: dict[str, Any] = {"label": p.get("label", p["key"]), "filled": sum(c.values())}
        if p.get("terms"):
            info["terms"] = p["terms"]
        if p.get("type") == "date":
            info["type"] = "date"
        else:
            top = [v for v, _ in c.most_common() if len(v) <= _VALUE_MAX][:_TOP_VALUES]
            if top:
                info["top"] = top
        props[p["key"]] = info
    status_from = (tdef.get("status") or {}).get("from")
    return {"ok": True, "type": key, "label": tdef.get("label", key), "count": total, "properties": props,
            **({"status_from": status_from} if status_from else {})}


def _near(value: str, values: Counter) -> list[str]:
    v = value.strip()
    if not v:
        return []
    hit = [x for x, _ in values.most_common() if x != v and (v in x or x in v)]
    if not hit and len(v) >= 2:
        need = max(2, len(set(v)) // 2)
        hit = [x for x, _ in values.most_common() if len(set(v) & set(x)) >= need]
    return hit[:_NEAR_MAX]


def query(args: dict[str, Any]) -> dict[str, Any]:
    """wiki_types.query 前后各加一步: 前面把中文说法对到 key, 后面查不到时给相近取值 / 没填的提示。"""
    from . import wiki_types  # noqa: PLC0415
    schema = wiki_types.load_schema()
    real, resolved, by_term = translate(args, schema)
    res = wiki_types.query(real)
    if resolved:
        res["resolved"] = resolved
    type_key = real.get("type")
    if not res.get("ok") or res.get("total") or type_key not in schema:
        return res
    _, seen = _values(type_key, schema)
    near = {}
    for c in real.get("where") or []:
        if isinstance(c, dict) and "eq" in c and c.get("field") in seen:
            got = _near(str(c["eq"]), seen[c["field"]])
            if got:
                near[c["field"]] = got
    if near:
        res["near_values"] = near
        res["note"] = "等于条件没对上任何条目; near_values 是这个属性里实际写着的相近取值, 换成它再查, 或改用 contains"
    empty = [k for k in by_term if not sum(seen.get(k, Counter()).values())]
    if empty:
        res["unfilled"] = empty
        res["note"] = (res.get("note", "") + "; " if res.get("note") else "") + \
            f"{'、'.join(empty)} 在员工知识库里一条都没填, 查不到不代表没有"
    return res
