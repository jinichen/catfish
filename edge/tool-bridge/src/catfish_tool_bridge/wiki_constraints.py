"""知识库本体的约束 (11/26, 鸿波「现在的本体是不是少了约束条件」)。

原来只有两条真拦得住: 关系两头的类型 (wiki_guard._allowed) 和属性名必须在定义里。其余都是「建议」:
等级写成「／认证范围／复审」、发布日期写成一整句话、编号重复、一个人隶属两个部门、隶属链绕成环, 全都能写进去。

这里定义并检查 (定义在 contracts/wiki_type_schema.json 和 wiki_relation_vocab.json, 说明见 docs/WIKI-TYPE-SCHEMA.md):
  属性   options 取值范围 / format 格式 / match 正则 / not_future 不晚于今天 / max_len / unique 同类不重复 / required 必填
  类型   rules: date_order (first 不能晚于 then)
  关系   relations[].max 最多几个对象 (反向写法一起算: B 包含 A = A 隶属 B); vocab.acyclic 不能绕成环
必填只提示不拦 (项目的属性大多还没人填, 拦了就什么都存不进去); 其它的在所有写入口都拦:
  catfish_wiki_update props / 整篇覆写、catfish_wiki_create 的 props、界面属性卡片、整理属性。
每处只查这次改到的值 —— 老数据里已经有的问题不挡无关的修改, 在「约束检查」(wiki_constraints_report) 里列出来改。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from . import wiki_files

# 文件头里本来就有的字段, 属性 key 不能跟它们重名 (11/26: 人的「职务」原来 key 是 title, 读出来的是人名, 改职务会改掉标题)
RESERVED_KEYS = frozenset({
    "type", "title", "aliases", "tags", "related", "sources", "evidence", "updated", "created", "entity_type",
    "concept_type", "subtype", "ontology_status", "ontology_reason", "action_refs", "deprecated", "visibility",
    "slug", "kind", "source", "props",
})

FORMATS = {
    "email": "邮箱",
    "phone": "电话",
    "credit_code": "统一社会信用代码",
    "url": "网址",
    "amount": "金额",
}

_NOT_A_VALUE = re.compile(r"待核|待补|待确认|疑似|误录|未录入|未填|占位|不详|未知|暂无|需按.*补")
_QUOTES = "\"'“”‘’「」『』"
_CREDIT_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_CREDIT_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


# ── 单个值 ────────────────────────────────────────────────────────

def normalize(prop: dict[str, Any], value: str) -> str:
    """写之前统一写法: 去掉包在外面的引号 (11/26 实测「“业务资质”」), 信用代码转大写去空格, 邮箱转小写, 电话去空格。"""
    v = (value or "").strip()
    while len(v) >= 2 and v[0] in _QUOTES and v[-1] in _QUOTES:
        v = v[1:-1].strip()
    fmt = prop.get("format")
    if fmt == "credit_code":
        v = re.sub(r"\s", "", v).upper()
    elif fmt == "email":
        v = v.lower()
    elif fmt == "phone":
        v = re.sub(r"\s", "", v)
    return v


def credit_code_ok(v: str) -> bool:
    """GB 32100-2015: 18 位, 最后一位是校验码。"""
    if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", v):
        return False
    total = sum(_CREDIT_CHARS.index(c) * w for c, w in zip(v[:17], _CREDIT_WEIGHTS))
    return _CREDIT_CHARS[(31 - total % 31) % 31] == v[17]


def _format_problem(fmt: str, v: str) -> str | None:
    if fmt == "email" and not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+", v):
        return "不是邮箱 (例 zhangsan@example.com)"
    if fmt == "phone" and not re.fullmatch(r"(\+?86-?)?(1[3-9]\d{9}|0\d{2,3}-?\d{7,8}(-\d{1,6})?|[48]00-?\d{3}-?\d{4})", v):
        return "不是电话 (手机 11 位, 座机 0591-12345678)"
    if fmt == "credit_code" and not credit_code_ok(v):
        return "不是统一社会信用代码 (18 位, 最后一位校验码要对得上)"
    if fmt == "url" and not re.fullmatch(r"(https?://)?[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+(:\d+)?(/\S*)?", v):
        return "不是网址 (例 https://www.example.com)"
    if fmt == "amount" and not re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?\s*(元|万元|万|亿元|亿)?|\d+(\.\d+)?\s*(元|万元|万|亿元|亿)?", v):
        return "不是金额 (写成 120万元 / 1,200,000元)"
    return None


def value_problem(prop: dict[str, Any], value: str, today: date | None = None) -> str | None:
    """这个值能不能写进这个属性; 能就 None, 不能就一句人话。空值 = 清掉这个属性, 总是可以 (必填只提示)。
    value 要先过 wiki_types._clean (日期换成 YYYY-MM-DD、aliases 归一)。"""
    v = (value or "").strip()
    if not v:
        return None
    if _NOT_A_VALUE.search(v):
        return "「待核 / 未录入」这类说明不是值, 没有就留空"
    if prop.get("type") == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return "不是日期 (写成 2027-06-30; 「长期有效」这类不算日期, 留空就行)"
        try:
            d = date.fromisoformat(v)
        except ValueError:
            return "不是真实的日期"
        if prop.get("not_future") and d > (today or date.today()):
            return f"不能晚于今天 ({(today or date.today()).isoformat()})"
        return None
    options = [str(o) for o in prop.get("options") or []]
    if options and v not in options:
        return f"只能是: {' / '.join(options)} (要加新的选项去「类型定义」改)"
    limit = int(prop.get("max_len") or 60)
    if len(v) > limit:
        return f"太长了 (最多 {limit} 字), 这像一段话, 不像一个值"
    if prop.get("format") and (why := _format_problem(str(prop["format"]), v)):
        return why
    if prop.get("match"):
        try:
            if not re.fullmatch(str(prop["match"]), v):
                return str(prop.get("match_hint") or f"格式不对 (要符合 {prop['match']})")
        except re.error:
            pass
    return None


# ── 属性之间 / 同类之间 ────────────────────────────────────────────

def rule_problems(props: dict[str, str], tdef: dict[str, Any], changed: set[str] | None = None) -> list[dict[str, str]]:
    """类型上的 rules。changed 给了就只查涉及这次改动的规则。"""
    out = []
    labels = {p["key"]: p.get("label", p["key"]) for p in tdef.get("properties") or []}
    for rule in tdef.get("rules") or []:
        if rule.get("kind") != "date_order":
            continue
        a, b = str(rule.get("first") or ""), str(rule.get("then") or "")
        if changed is not None and not ({a, b} & changed):
            continue
        va, vb = props.get(a, ""), props.get(b, "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", va) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", vb) and va > vb:
            key = b if changed is not None and b in changed else a
            out.append({"key": key, "label": labels.get(key, key),
                        "error": rule.get("message") or f"{labels.get(a, a)}不能晚于{labels.get(b, b)}",
                        "detail": f"{labels.get(a, a)} {va} 晚于 {labels.get(b, b)} {vb}"})
    return out


def unique_key(value: str) -> str:
    return re.sub(r"[\s\-_/]", "", value).upper()


def own_typed(schema: dict[str, dict[str, Any]], type_key: str | None = None):
    """自家知识库 (wiki/entities、wiki/concepts) 里有类型定义的条目: (rel_path, type_key, fm, body)。部门共享来的不算重复。"""
    from . import wiki_types  # noqa: PLC0415
    home = wiki_files._catfish_home()
    for sub in ("wiki/entities", "wiki/concepts"):
        d = home / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            try:
                fm, body = wiki_files._split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if (wiki_files._parse_field(fm, "deprecated") or "").strip().lower() in ("true", "yes", "1"):
                continue
            if (wiki_files._parse_field(fm, "ontology_status") or "").strip().lower() in ("rejected", "deprecated"):
                continue
            tk = wiki_types.type_of(fm, schema)
            if tk and (type_key is None or tk == type_key):
                yield f"{sub}/{path.name}", tk, fm, body


def title_of(fm: str, rel_path: str) -> str:
    raw = (wiki_files._parse_field(fm, "title") or "").strip().strip('"').strip("'")
    return raw or Path(rel_path).stem


def unique_conflicts(schema: dict[str, dict[str, Any]], type_key: str, rel_path: str,
                     values: dict[str, str]) -> dict[str, list[str]]:
    """{属性 key: [同类里已经用了这个值的条目标题]}。只查 unique 的属性。"""
    from . import wiki_types  # noqa: PLC0415
    tdef = schema[type_key]
    want = {p["key"]: unique_key(values[p["key"]]) for p in tdef.get("properties") or []
            if p.get("unique") and values.get(p["key"])}
    if not want:
        return {}
    hits: dict[str, list[str]] = {}
    for rel, _tk, fm, _body in own_typed(schema, type_key):
        if rel == rel_path:
            continue
        props = wiki_types.read_props(fm, tdef)
        for key, norm in want.items():
            if props.get(key) and unique_key(props[key]) == norm:
                hits.setdefault(key, []).append(title_of(fm, rel))
    return hits


def check_props(rel_path: str, type_key: str, current: dict[str, str], changes: dict[str, str],
                schema: dict[str, dict[str, Any]], today: date | None = None) -> list[dict[str, str]]:
    """这次要写的属性有什么问题: [{key, label, value, error}]。changes 是已经 _clean 过的值 (空 = 删)。"""
    tdef = schema[type_key]
    defs = {p["key"]: p for p in tdef.get("properties") or []}
    out = []
    for key, value in changes.items():
        if key in defs and (why := value_problem(defs[key], value, today)):
            out.append({"key": key, "label": defs[key].get("label", key), "value": value, "error": why})
    merged = {**current, **changes}
    merged = {k: v for k, v in merged.items() if v}
    bad_keys = {p["key"] for p in out}
    for p in rule_problems(merged, tdef, set(changes) - bad_keys):
        out.append({**p, "value": merged.get(p["key"], "")})
    for key, titles in unique_conflicts(schema, type_key, rel_path,
                                        {k: v for k, v in changes.items() if v and k not in bad_keys}).items():
        out.append({"key": key, "label": defs[key].get("label", key), "value": changes[key],
                    "error": f"跟「{'」「'.join(titles[:3])}」重复了, 同一类里{defs[key].get('label', key)}不能重复"
                             " (是同一个东西就去关系整理里合并)"})
    return out


def message(problems: list[dict[str, str]]) -> str:
    return "; ".join(f"{p['label']}「{p.get('value', '')}」{p['error']}" if p.get("value") else f"{p['label']}: {p['error']}"
                     for p in problems)


def missing_required(props: dict[str, str], tdef: dict[str, Any]) -> list[str]:
    return [p.get("label", p["key"]) for p in tdef.get("properties") or [] if p.get("required") and not props.get(p["key"])]


# ── 关系: 最多几个 / 不能成环 ─────────────────────────────────────

def relation_vocab() -> dict[str, Any]:
    here = Path(__file__).resolve()
    for cand in (here.parents[3] / "contracts" / "wiki_relation_vocab.json",
                 here.parents[2] / "contracts" / "wiki_relation_vocab.json"):
        try:
            return json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def canonical_edge(src: str, rel: str, dst: str, inverse: dict[str, str]) -> tuple[str, str, str]:
    """一对互为反向的关系统一成一种写法: B 包含 A → A 隶属 B (取词表 inverse 里先出现的那个)。"""
    if rel in inverse:
        first = next(iter(k for k in inverse if k == rel or inverse[k] == rel))
        if rel != first:
            return dst, first, src
    return src, rel, dst


class KB:
    """知识库里的条目和关系 (一次读全, 给关系约束 / 约束检查用)。节点 = rel_path。"""

    def __init__(self) -> None:
        from . import wiki_types  # noqa: PLC0415
        self.schema = wiki_types.load_schema()
        vocab = relation_vocab()
        self.inverse: dict[str, str] = dict(vocab.get("inverse") or {})
        self.acyclic = {canonical_edge("a", r, "b", self.inverse)[1] for r in vocab.get("acyclic") or []}
        self.by_name: dict[str, str] = {}
        self.type: dict[str, str] = {}
        self.title: dict[str, str] = {}
        self.out: dict[str, list[tuple[str, str]]] = {}   # rel_path → [(rel, 对方 rel_path)] 文件头里写的
        raw: dict[str, list[dict[str, Any]]] = {}
        for item in wiki_files.list_wiki_files(limit=100000).get("items", []):
            if item["kind"] not in ("entity", "concept"):
                continue
            if (item.get("ontology_status") or "active").lower() in ("rejected", "deprecated"):
                continue
            rp = item["rel_path"]
            self.type[rp] = wiki_types.type_key_of_subtype(item.get("subtype") or "", self.schema) or ""
            self.title[rp] = item["title"]
            for n in [item["title"], *(item.get("aliases") or [])]:
                self.by_name.setdefault(n.strip().lower(), rp)
            raw[rp] = [r for r in item.get("related") or [] if isinstance(r, dict)]
        for rp, rels in raw.items():
            self.out[rp] = [(str(r.get("rel") or ""), d) for r in rels if (d := self.resolve(str(r.get("name") or "")))]

    def resolve(self, name: str) -> str | None:
        return self.by_name.get((name or "").strip().lower())

    def edges(self, replace: tuple[str, list[tuple[str, str]]] | None = None) -> set[tuple[str, str, str]]:
        """统一写法后的全部关系; replace=(节点, 新的出边) 算「这一页改成这样以后」。"""
        out = set()
        for src, rels in self.out.items():
            if replace and src == replace[0]:
                continue
            out |= {canonical_edge(src, rel, dst, self.inverse) for rel, dst in rels if rel and dst != src}
        if replace:
            out |= {canonical_edge(replace[0], rel, dst, self.inverse) for rel, dst in replace[1] if rel and dst != replace[0]}
        return out

    def label(self, node: str) -> str:
        return self.title.get(node) or node.removeprefix("new:")

    def type_label(self, tk: str) -> str:
        return (self.schema.get(tk) or {}).get("label") or tk or "未分类"

    def max_specs(self, node: str) -> list[dict[str, Any]]:
        return [r for r in (self.schema.get(self.type.get(node, "")) or {}).get("relations") or []
                if r.get("max") and r.get("to")]

    def targets(self, edges: set[tuple[str, str, str]], node: str, spec: dict[str, Any]) -> set[str]:
        rel = canonical_edge("a", spec["rel"], "b", self.inverse)[1]
        flipped = rel != spec["rel"]
        to = set(spec.get("to") or [])
        if flipped:   # 定义写的是「包含 最多 N 个」: 数 node 是谁的上级
            return {s for s, r, d in edges if r == rel and d == node and self.type.get(s) in to}
        return {d for s, r, d in edges if r == rel and s == node and self.type.get(d) in to}

    def path_exists(self, edges: set[tuple[str, str, str]], rel: str, start: str, goal: str) -> bool:
        nxt: dict[str, set[str]] = {}
        for s, r, d in edges:
            if r == rel:
                nxt.setdefault(s, set()).add(d)
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(nxt.get(cur, ()))
        return False


def relation_problems(kb: KB, node: str, subtype_key: str, new_out: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    """这一页的出边改成 new_out ([(rel, 对方 rel_path, 写的名字)]) 以后: 有没有超过「最多几个」、有没有绕成环。
    只报这次改动造成或加重的, 老数据里已有的不挡。返回 [{name, rel, why}]。"""
    if node not in kb.type:
        kb.type[node] = subtype_key
    old = kb.edges()
    new = kb.edges((node, [(r, d) for r, d, _ in new_out]))
    added = new - old
    if not added:
        return []
    names = {d: n for _, d, n in new_out}
    out: list[dict[str, str]] = []
    touched = {node} | {d for _, d, _ in new_out}
    for n in touched:
        for spec in kb.max_specs(n):
            now, before = kb.targets(new, n, spec), kb.targets(old, n, spec)
            if len(now) > int(spec["max"]) and len(now) > len(before):
                culprit = next((d for d in now - before if d != node), None) if n == node else n
                who = "、".join(f"「{kb.label(t)}」" for t in sorted(now))
                to = "/".join(kb.type_label(t) for t in spec["to"] if t in kb.schema)
                out.append({"name": names.get(culprit or "", kb.label(culprit or n)), "rel": spec["rel"],
                            "why": f"{kb.type_label(kb.type.get(n, ''))}「{kb.label(n)}」最多{spec['rel']}{spec['max']}个{to}, "
                                   f"这样写就成了 {who}; 换了就把原来那条改掉"})
    for s, r, d in sorted(added):
        if r in kb.acyclic and (s == d or kb.path_exists(new, r, d, s)):
            other = d if s == node else s
            out.append({"name": names.get(other, kb.label(other)), "rel": r,
                        "why": f"「{kb.label(s)}」{r}「{kb.label(d)}」会绕成环: 「{kb.label(d)}」已经 (直接或间接) {r}「{kb.label(s)}」"})
    return out
