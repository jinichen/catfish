"""知识体系「类型定义」页的后端 (11/07): 看每一类的属性 / 行动 / 提醒天数, 一次改一处。

鸿波 11/07「属性是硬编码吗? 做类型定义页」: 类型定义原来只能改随包发的 json 或手写本机补充文件。

改的只是本机补充文件 ~/.catfish/wiki-type-schema.json, 内置定义 (edge/contracts/wiki_type_schema.json) 不动:
- 内置的属性 / 行动: 可以改名字、说明、要做的事 (本机写一份同 key / id 的覆盖), 可以隐藏 / 恢复, 不能删;
- 自己加的: 随便改、删;
- 新类型: 从知识库里已有的细分类型挑一个 (类型 key = 条目的 entity_type / concept_type)。
每次改动先校验, 旧文件备份成 wiki-type-schema.json.bak, 再整份写回。
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any

from . import wiki_files, wiki_types

_KEY_RE = re.compile(r"^[A-Za-z0-9_一-鿿-]{1,40}$")      # 类型 key: 跟条目的细分类型一致, 中文也行
_PROP_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_PROP_TYPES = ("text", "date")
_EDITABLE_PROP = ("label", "type", "hint", "keywords", "max_len")
_EDITABLE_ACTION = ("label", "description", "instruction", "skill_path")


def _origin(key: str, builtin: dict, local: dict, field: str, ident: str) -> str:
    in_b = any(x.get(field) == ident for x in (builtin.get(key) or {}).get("properties" if field == "key" else "actions") or [])
    in_l = any(x.get(field) == ident for x in (local.get(key) or {}).get("properties" if field == "key" else "actions") or [])
    return "builtin+local" if in_b and in_l else "builtin" if in_b else "local"


def view() -> dict[str, Any]:
    """[{key, label, origin, status, properties: [...+origin, hidden], actions: [...+origin, hidden]}] + 知识库里有、还没定义的细分类型。"""
    builtin, local = wiki_types.load_builtin(), wiki_types.load_local()
    merged = wiki_types.merge_types(builtin, local, keep_hidden=True)
    out = []
    for key, tdef in merged.items():
        out.append({
            "key": key,
            "label": tdef.get("label", key),
            "origin": "builtin" if key in builtin else "local",
            "status": tdef.get("status") if isinstance(tdef.get("status"), dict) else None,
            "properties": [{**p, "origin": _origin(key, builtin, local, "key", p["key"])} for p in tdef.get("properties") or [] if p.get("key")],
            "actions": [{**a, "origin": _origin(key, builtin, local, "id", a["id"])} for a in tdef.get("actions") or [] if a.get("id")],
        })
    return {"ok": True, "types": out, "subtypes": _known_subtypes(set(merged))}


def _known_subtypes(defined: set[str]) -> list[dict[str, Any]]:
    """知识库里出现过、还没有类型定义的细分类型 (新建类型从这里挑), 按条数从多到少。"""
    home = wiki_files._catfish_home()
    counts: dict[str, int] = {}
    for sub in ("wiki/entities", "wiki/concepts"):
        d = home / sub
        if not d.is_dir():
            continue
        for path in d.glob("*.md"):
            try:
                fm, _ = wiki_files._split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            for field in ("entity_type", "concept_type"):
                raw = wiki_types._unquote(wiki_files._parse_field(fm, field) or "")
                if raw:
                    kind = "entity" if field == "entity_type" else "concept"
                    canon = wiki_files._canonical_subtype(kind, raw)
                    if raw not in defined and canon not in defined:
                        counts[canon] = counts.get(canon, 0) + 1
    return [{"key": k, "count": n} for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]


def _save_local(local: dict[str, dict[str, Any]]) -> None:
    path = wiki_files._catfish_home() / wiki_types.LOCAL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.with_suffix(".json.bak").write_bytes(path.read_bytes())
    body = {"_why": "知识体系「类型定义」页写的本机补充 (内置定义随安装包发, 这里只记改动); 说明见 docs/WIKI-TYPE-SCHEMA.md",
            "version": 1, "types": local}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _clean_prop(raw: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _EDITABLE_PROP:
        if field not in raw:
            continue
        v = raw[field]
        if field == "label":
            v = str(v or "").strip()
            if not v or len(v) > 20:
                raise ValueError("属性名要填, 最多 20 个字")
        elif field == "type":
            if v not in _PROP_TYPES:
                raise ValueError("属性类型只能是 文本 / 日期")
        elif field == "hint":
            v = str(v or "").strip()[:80]
        elif field == "keywords":
            v = [str(x).strip() for x in (v if isinstance(v, list) else str(v or "").replace("，", ",").split(",")) if str(x).strip()][:10]
        elif field == "max_len":
            v = max(10, min(int(v or 60), 400))
        out[field] = v
    if not partial and "label" not in out:
        raise ValueError("属性名要填")
    return out


def _clean_action(raw: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _EDITABLE_ACTION:
        if field not in raw:
            continue
        v = str(raw[field] or "").strip()
        if field == "label" and (not v or len(v) > 20):
            raise ValueError("行动名要填, 最多 20 个字")
        if field == "instruction" and len(v) > 1000:
            raise ValueError("要做的事最多 1000 字")
        if field == "skill_path" and v and (".." in v.split("/") or not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", v)):
            raise ValueError("技能路径不对, 例 personal/renzheng-gongshi-website-verify")
        if field == "description":
            v = v[:120]
        out[field] = v
    if not partial and not out.get("label"):
        raise ValueError("行动名要填")
    if not partial and not out.get("instruction"):
        raise ValueError("要做的事要填: 点了这个行动, 小鲶要按这句话做")
    return out


def _new_prop_key(existing: set[str]) -> str:
    while True:
        k = f"custom_{secrets.token_hex(3)}"
        if k not in existing:
            return k


def _upsert(items: list[dict[str, Any]], field: str, ident: str, patch: dict[str, Any]) -> list[dict[str, Any]]:
    out = [dict(x) for x in items]
    hit = next((x for x in out if x.get(field) == ident), None)
    if hit is None:
        out.append({field: ident, **patch})
    else:
        hit.update(patch)
    return out


def apply(params: dict[str, Any]) -> dict[str, Any]:
    """一次改一处: op = add_type / delete_type / rename_type / set_warn_days /
    add_property / update_property / hide_property / unhide_property / delete_property /
    add_action / update_action / hide_action / unhide_action / delete_action。返回改完的 view()。"""
    op = str(params.get("op") or "")
    type_key = str(params.get("type") or "").strip()
    builtin, local = wiki_types.load_builtin(), wiki_types.load_local()
    merged = wiki_types.merge_types(builtin, local, keep_hidden=True)
    try:
        if op == "add_type":
            if not _KEY_RE.match(type_key):
                raise ValueError("类型要从知识库已有的细分类型里选")
            if type_key in merged:
                raise ValueError(f"「{type_key}」已经有定义了")
            label = str(params.get("label") or type_key).strip()[:20]
            local[type_key] = {"label": label, "kind": str(params.get("kind") or "entity"), "properties": [], "actions": []}
        else:
            if type_key not in merged:
                raise ValueError(f"没有这个类型: {type_key or '(空)'}")
            t = dict(local.get(type_key) or {})
            is_builtin_type = type_key in builtin
            if op == "delete_type":
                if is_builtin_type:
                    raise ValueError("内置类型不能删, 可以隐藏它的属性和行动")
                local.pop(type_key, None)
                t = None
            elif op == "rename_type":
                label = str(params.get("label") or "").strip()
                if not label or len(label) > 20:
                    raise ValueError("类型名要填, 最多 20 个字")
                t["label"] = label
            elif op == "set_warn_days":
                if not isinstance(merged[type_key].get("status"), dict):
                    raise ValueError("这一类没有按日期算的状态")
                days = int(params.get("warn_days") or 0)
                if not 1 <= days <= 1000:
                    raise ValueError("提前天数要在 1 到 1000 之间")
                t["status"] = {**(t.get("status") or {}), "warn_days": days}
            elif op in ("add_property", "update_property"):
                props = merged[type_key].get("properties") or []
                if op == "add_property":
                    patch = _clean_prop(params.get("property") or {})
                    patch.setdefault("type", "text")
                    if any(p.get("label") == patch["label"] for p in props):
                        raise ValueError(f"已经有叫「{patch['label']}」的属性了")
                    key = _new_prop_key({p["key"] for p in props})
                else:
                    key = str(params.get("key") or "")
                    if not any(p.get("key") == key for p in props):
                        raise ValueError(f"没有这个属性: {key}")
                    patch = _clean_prop(params.get("property") or {}, partial=True)
                    if "label" in patch and any(p.get("label") == patch["label"] and p.get("key") != key for p in props):
                        raise ValueError(f"已经有叫「{patch['label']}」的属性了")
                if not _PROP_KEY_RE.match(key):
                    raise ValueError(f"属性 key 不合法: {key}")
                t["properties"] = _upsert(t.get("properties") or [], "key", key, patch)
            elif op in ("hide_property", "unhide_property", "delete_property"):
                key = str(params.get("key") or "")
                if not any(p.get("key") == key for p in merged[type_key].get("properties") or []):
                    raise ValueError(f"没有这个属性: {key}")
                in_builtin = any(p.get("key") == key for p in (builtin.get(type_key) or {}).get("properties") or [])
                if op == "delete_property":
                    if in_builtin:
                        raise ValueError("内置属性不能删, 可以隐藏")
                    t["properties"] = [p for p in t.get("properties") or [] if p.get("key") != key]
                else:
                    hidden = [k for k in t.get("hidden_properties") or [] if k != key]
                    t["hidden_properties"] = hidden + ([key] if op == "hide_property" else [])
            elif op in ("add_action", "update_action"):
                actions = merged[type_key].get("actions") or []
                if op == "add_action":
                    patch = _clean_action(params.get("action") or {})
                    ident = f"type.{type_key}.custom-{secrets.token_hex(3)}"
                    if not _ACTION_ID_RE.match(ident):
                        ident = f"type.custom.{secrets.token_hex(4)}"
                else:
                    ident = str(params.get("id") or "")
                    if not any(a.get("id") == ident for a in actions):
                        raise ValueError(f"没有这个行动: {ident}")
                    patch = _clean_action(params.get("action") or {}, partial=True)
                t["actions"] = _upsert(t.get("actions") or [], "id", ident, patch)
            elif op in ("hide_action", "unhide_action", "delete_action"):
                ident = str(params.get("id") or "")
                if not any(a.get("id") == ident for a in merged[type_key].get("actions") or []):
                    raise ValueError(f"没有这个行动: {ident}")
                in_builtin = any(a.get("id") == ident for a in (builtin.get(type_key) or {}).get("actions") or [])
                if op == "delete_action":
                    if in_builtin:
                        raise ValueError("内置行动不能删, 可以隐藏")
                    t["actions"] = [a for a in t.get("actions") or [] if a.get("id") != ident]
                else:
                    hidden = [i for i in t.get("hidden_actions") or [] if i != ident]
                    t["hidden_actions"] = hidden + ([ident] if op == "hide_action" else [])
            else:
                raise ValueError(f"不认识的操作: {op or '(空)'}")
            if t is not None:
                local[type_key] = {k: v for k, v in t.items() if v not in ([], None) or k in ("properties", "actions")}
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    _save_local(local)
    return view()
