"""客户档案 (销售跟进阶段 1, docs/SALES-FOLLOWUP-PLAN.md)。

一个客户 = 知识库里一个 `entity_type: customer` 的实体页 (~/.catfish/wiki/entities/)。
建页走 wiki_files.create_wiki_entry, 改页走 update_wiki_file —— 文件名、重名检测、
本体状态跟知识库其它条目一套规则, 知识库 TAB 里直接能看。

页面 = frontmatter 里的客户字段 + 正文「## 时间线」。时间线一行一条, 新的在上:

    - 2026-10-12 · 微信 · 客户 · 问题 · 私有化部署要几台服务器?
    - 2026-10-10 · 邮件 · 我方 · 承诺 · 周三前发正式报价 · 截止 2026-10-15
    - 2026-10-09 · 邮件 · 我方 · 报价 · 年费 12 万 (v1) · ✓

格式固定是给跟进雷达 (customer_radar.py) 读的; 员工在知识库里手改也照这个格式。

不随商品写死的部分 (阶段名、各阶段多少天算冷了、报价后几天算没回音) 在
「销售模板」里 (~/.catfish/sales_template.json); 没有就用下面的鲶鱼默认模板。

红线 (计划 §2):
- 新建客户、改阶段都要员工确认 (confirmed=true), 模型只能提议。
- 匹配不上客户的消息不硬塞, 返回候选让员工选。
- 只存在员工本机, 中央端不存。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import wiki_files

SUBTYPE = "customer"
TIMELINE_HEADER = "## 时间线"
CHANNELS = ("邮件", "微信", "会议", "电话", "其他")
WHO = ("客户", "我方")
KINDS = ("问题", "承诺", "报价", "信息")
DONE_MARK = "✓"

DEFAULT_TEMPLATE: dict[str, Any] = {
    "name": "鲶鱼销售",
    # cold_days: 最近一次客户来消息后多少天没动静算冷了; 0 = 这个阶段不追 (成交 / 输单 / 暂停)
    "stages": [
        {"name": "线索", "cold_days": 14},
        {"name": "需求确认", "cold_days": 7},
        {"name": "已报价", "cold_days": 7},
        {"name": "商务谈判", "cold_days": 3},
        {"name": "成交", "cold_days": 0},
        {"name": "输单", "cold_days": 0},
        {"name": "暂停", "cold_days": 0},
    ],
    "quoted_stage": "已报价",
    "reply_wait_days": 1,
    "quote_followup_days": 5,
}

FIELDS = ("stage", "owner", "contacts", "last_contact", "next_action", "next_action_due", "deal_size")


# ── 销售模板 ─────────────────────────────────────────────────────────────

def template_path() -> Path:
    return wiki_files._catfish_home() / "sales_template.json"


def load_template() -> dict[str, Any]:
    """读销售模板; 缺字段用默认补, 坏文件退回默认 (不因为一个手误让雷达整个停掉)。"""
    tpl = json.loads(json.dumps(DEFAULT_TEMPLATE))
    try:
        user = json.loads(template_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return tpl
    if isinstance(user, dict):
        stages = user.get("stages")
        if isinstance(stages, list) and all(isinstance(s, dict) and s.get("name") for s in stages):
            tpl["stages"] = [{"name": str(s["name"]), "cold_days": max(0, int(s.get("cold_days") or 0))} for s in stages]
        for key in ("name", "quoted_stage"):
            if isinstance(user.get(key), str) and user[key].strip():
                tpl[key] = user[key].strip()
        for key in ("reply_wait_days", "quote_followup_days"):
            if isinstance(user.get(key), (int, float)) and user[key] >= 0:
                tpl[key] = int(user[key])
    return tpl


def save_template(tpl: dict[str, Any]) -> dict[str, Any]:
    stages = tpl.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages 至少要一个阶段")
    names = [str(s.get("name", "")).strip() for s in stages]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise ValueError("阶段名不能空, 也不能重复")
    if str(tpl.get("quoted_stage", "")).strip() not in names:
        raise ValueError("quoted_stage 必须是上面阶段之一")
    path = template_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return load_template()


def stage_names(tpl: dict[str, Any] | None = None) -> list[str]:
    return [s["name"] for s in (tpl or load_template())["stages"]]


# ── 页面读写 ─────────────────────────────────────────────────────────────

def _entities_dir() -> Path:
    return wiki_files._catfish_home() / "wiki" / "entities"


def _yaml_value(v: Any) -> str:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    s = str(v).replace("\n", " ").strip()
    if not s or re.search(r"[:#\[\]{}\"']|^[\s\-?|>!&*%@`]", s):
        return json.dumps(s, ensure_ascii=False)
    return s


def _set_fields(content: str, fields: dict[str, Any]) -> str:
    fm, body = wiki_files._split_frontmatter(content)
    lines = fm.splitlines()
    for key, value in fields.items():
        line = f"{key}: {_yaml_value(value)}"
        idx = next((i for i, raw in enumerate(lines) if re.match(rf"^{re.escape(key)}\s*:", raw)), None)
        if idx is None:
            lines.append(line)
        else:
            lines[idx] = line
    return "---\n" + "\n".join(lines).strip("\n") + "\n---\n" + body.lstrip("\n")


def _field(fm: str, key: str) -> Any:
    raw = wiki_files._parse_field(fm, key)
    if raw is None:
        return None
    raw = raw.strip()
    if raw[:1] in "[{\"":
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _is_customer(fm: str) -> bool:
    return (wiki_files._parse_field(fm, "entity_type") or "").strip() == SUBTYPE


def _read(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    fm, body = wiki_files._split_frontmatter(content)
    rec: dict[str, Any] = {
        "name": (wiki_files._parse_field(fm, "title") or path.stem).strip(),
        "rel_path": f"wiki/entities/{path.name}",
        "content": content,
    }
    for key in FIELDS:
        rec[key] = _field(fm, key)
    rec["contacts"] = rec["contacts"] if isinstance(rec["contacts"], list) else []
    rec["timeline"] = parse_timeline(body)
    return rec


def all_customers() -> list[dict[str, Any]]:
    d = _entities_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_customer(wiki_files._split_frontmatter(text)[0]):
            out.append(_read(p))
    return out


def find(name: str = "", email: str = "", wechat: str = "") -> tuple[dict[str, Any] | None, list[str]]:
    """按名字 / 联系人邮箱 (含同域名) / 微信备注名找客户。返回 (命中, 候选名)。"""
    name, email, wechat = name.strip(), email.strip().lower(), wechat.strip()
    customers = all_customers()
    if name:
        exact = [c for c in customers if c["name"] == name]
        if exact:
            return exact[0], []
        fuzzy = [c for c in customers if name in c["name"] or c["name"] in name]
        if len(fuzzy) == 1:
            return fuzzy[0], []
        return None, [c["name"] for c in fuzzy]
    if email:
        domain = email.split("@")[-1]
        hits = [c for c in customers if any(str(x.get("email", "")).lower() == email for x in c["contacts"])]
        if not hits and domain and domain not in _PUBLIC_MAIL:
            hits = [c for c in customers if any(str(x.get("email", "")).lower().endswith("@" + domain) for x in c["contacts"])]
        return (hits[0], []) if len(hits) == 1 else (None, [c["name"] for c in hits])
    if wechat:
        hits = [c for c in customers if any(str(x.get("wechat", "")) == wechat for x in c["contacts"])]
        return (hits[0], []) if len(hits) == 1 else (None, [c["name"] for c in hits])
    return None, []


_PUBLIC_MAIL = {"qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "hotmail.com", "foxmail.com", "sina.com", "yeah.net", "aliyun.com", "139.com"}


# ── 时间线 ───────────────────────────────────────────────────────────────

_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) · (\S+) · (\S+) · (\S+) · (.+)$")


def parse_timeline(body: str) -> list[dict[str, Any]]:
    """时间线条目, 新的在前 (页面里就是这个顺序)。格式不对的行跳过。"""
    lines = body.splitlines()
    try:
        start = lines.index(TIMELINE_HEADER) + 1
    except ValueError:
        return []
    out = []
    for raw in lines[start:]:
        if raw.startswith("## "):
            break
        m = _LINE.match(raw.strip())
        if not m:
            continue
        rest = m.group(5)
        done = rest.endswith(f" · {DONE_MARK}")
        if done:
            rest = rest[: -len(f" · {DONE_MARK}")]
        due = None
        dm = re.search(r" · 截止 (\d{4}-\d{2}-\d{2})$", rest)
        if dm:
            due, rest = dm.group(1), rest[: dm.start()]
        out.append({"date": m.group(1), "channel": m.group(2), "who": m.group(3), "kind": m.group(4),
                    "text": rest, "due": due, "done": done})
    return out


def format_entry(e: dict[str, Any]) -> str:
    line = f"- {e['date']} · {e['channel']} · {e['who']} · {e['kind']} · {e['text']}"
    if e.get("due"):
        line += f" · 截止 {e['due']}"
    if e.get("done"):
        line += f" · {DONE_MARK}"
    return line


def _replace_timeline(content: str, entries: list[dict[str, Any]]) -> str:
    fm, body = wiki_files._split_frontmatter(content)
    lines = body.splitlines()
    block = [TIMELINE_HEADER, *[format_entry(e) for e in entries]]
    if TIMELINE_HEADER in lines:
        start = lines.index(TIMELINE_HEADER)
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        lines[start:end] = block + ([""] if end < len(lines) else [])
    else:
        lines += ["", *block]
    return "---\n" + fm.strip("\n") + "\n---\n" + "\n".join(lines).rstrip() + "\n"


def _iso_day(value: Any, default: str | None = None) -> str | None:
    s = str(value or "").strip()
    if not s:
        return default
    try:
        return datetime.fromisoformat(s[:10]).date().isoformat()
    except ValueError:
        raise ValueError(f"日期格式不对 (要 YYYY-MM-DD): {s}") from None


# ── 工具动作 (catfish_customer, action=...) ───────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def _write(rec: dict[str, Any], content: str) -> dict[str, Any]:
    r = wiki_files.update_wiki_file(rec["rel_path"], content)
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "写客户页失败")
    return r


def _summary(c: dict[str, Any], timeline: int = 0) -> dict[str, Any]:
    out = {k: c.get(k) for k in ("name", "rel_path", *FIELDS)}
    if timeline:
        out["timeline"] = c["timeline"][:timeline]
    return out


def action_upsert(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ValueError("name (客户公司名) 不能空")
    tpl = load_template()
    confirmed = bool(args.get("confirmed"))
    stage = str(args.get("stage") or "").strip()
    if stage and stage not in stage_names(tpl):
        raise ValueError(f"阶段 {stage!r} 不在销售模板里, 可选: {stage_names(tpl)}")
    fields: dict[str, Any] = {}
    for key in ("owner", "next_action", "deal_size"):
        if args.get(key) not in (None, ""):
            fields[key] = str(args[key]).strip()
    if args.get("next_action_due"):
        fields["next_action_due"] = _iso_day(args["next_action_due"])
    if isinstance(args.get("contacts"), list):
        fields["contacts"] = [{k: str(v) for k, v in x.items() if v} for x in args["contacts"] if isinstance(x, dict)]

    rec, candidates = find(name=name)
    if rec is None:
        if candidates:
            return {"ok": False, "ambiguous": candidates, "error": f"「{name}」对上了多个客户, 用准确全名再调"}
        proposal = {"name": name, "stage": stage or stage_names(tpl)[0], **fields}
        if not confirmed:
            return {"ok": False, "needs_confirmation": True, "proposal": proposal,
                    "message": "新建客户要员工确认: 把 proposal 念给员工, 同意后带 confirmed=true 再调"}
        body = f"# {name}\n\n{TIMELINE_HEADER}\n"
        r = wiki_files.create_wiki_entry("entity", name, body, subtype=SUBTYPE, tags=["客户"])
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "建客户页失败")
        rec = _read(_entities_dir() / Path(r["rel_path"]).name)
        _write(rec, _set_fields(rec["content"], {"stage": proposal["stage"], **fields}))
        return {"ok": True, "created": True, "customer": _summary(_read(_entities_dir() / Path(rec["rel_path"]).name))}

    if stage and stage != rec.get("stage") and not confirmed:
        return {"ok": False, "needs_confirmation": True,
                "proposal": {"name": rec["name"], "stage_from": rec.get("stage"), "stage_to": stage},
                "message": "改阶段要员工确认 (阶段是业务判断): 说明依据, 员工同意后带 confirmed=true 再调"}
    if stage:
        fields["stage"] = stage
    if not fields:
        return {"ok": True, "unchanged": True, "customer": _summary(rec)}
    _write(rec, _set_fields(rec["content"], fields))
    return {"ok": True, "customer": _summary(_read(_entities_dir() / Path(rec["rel_path"]).name))}


def _resolve(args: dict[str, Any]) -> dict[str, Any]:
    rec, candidates = find(name=str(args.get("customer") or args.get("name") or ""),
                           email=str(args.get("email") or ""), wechat=str(args.get("wechat") or ""))
    if rec is None:
        hint = f"候选: {candidates}" if candidates else "没有这个客户 —— 先用 action=upsert 提议新建 (员工确认后才建)"
        raise LookupError(f"找不到对应客户。{hint}。不要把消息记到别的客户上。")
    return rec


def action_log(args: dict[str, Any]) -> dict[str, Any]:
    rec = _resolve(args)
    entry = {
        "date": _iso_day(args.get("date"), _today()),
        "channel": str(args.get("channel") or "其他"),
        "who": str(args.get("who") or ""),
        "kind": str(args.get("kind") or "信息"),
        "text": re.sub(r"\s+", " ", str(args.get("text") or "")).replace(" · ", " ").strip()[:160],
        "due": _iso_day(args.get("due")) if args.get("due") else None,
        "done": False,
    }
    if entry["channel"] not in CHANNELS or entry["who"] not in WHO or entry["kind"] not in KINDS:
        raise ValueError(f"channel ∈ {CHANNELS}, who ∈ {WHO}, kind ∈ {KINDS}")
    if not entry["text"]:
        raise ValueError("text 不能空 —— 写客户 / 我方的原话要点")
    if entry["kind"] == "承诺" and (entry["who"] != "我方" or not entry["due"]):
        raise ValueError("承诺 = 我方答应客户的事, 必须带 due (截止日)")
    if any(format_entry(e) == format_entry(entry) for e in rec["timeline"]):
        return {"ok": True, "duplicate": True, "customer": rec["name"]}
    # 新的在上; 同一天里后记的排前面
    timeline = [entry, *rec["timeline"]]
    timeline.sort(key=lambda e: e["date"], reverse=True)
    content = _replace_timeline(rec["content"], timeline)
    if entry["who"] == "客户" and entry["date"] > str(rec.get("last_contact") or ""):
        content = _set_fields(content, {"last_contact": entry["date"]})
    _write(rec, content)
    return {"ok": True, "customer": rec["name"], "logged": format_entry(entry)}


def action_done(args: dict[str, Any]) -> dict[str, Any]:
    """把我方承诺 / 报价跟进标成已办 (按原文片段匹配)。"""
    rec = _resolve(args)
    needle = str(args.get("text") or "").strip()
    if not needle:
        raise ValueError("text: 要标完成的那条承诺的原文片段")
    hits = [e for e in rec["timeline"] if not e["done"] and needle in e["text"]]
    if len(hits) != 1:
        return {"ok": False, "matches": [format_entry(e) for e in hits],
                "error": "没找到唯一一条未完成的匹配, 换更准的片段"}
    hits[0]["done"] = True
    _write(rec, _replace_timeline(rec["content"], rec["timeline"]))
    return {"ok": True, "customer": rec["name"], "done": format_entry(hits[0])}


def action_get(args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "customer": _summary(_resolve(args), timeline=int(args.get("limit") or 30))}


def action_list(args: dict[str, Any]) -> dict[str, Any]:
    stage = str(args.get("stage") or "").strip()
    rows = [_summary(c) for c in all_customers() if not stage or c.get("stage") == stage]
    rows.sort(key=lambda r: str(r.get("last_contact") or ""), reverse=True)
    return {"ok": True, "count": len(rows), "customers": rows, "stages": stage_names()}


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """catfish_customer 的入口 (catfish_tools 按前缀路由过来)。"""
    action = str(args.get("action") or "").strip()
    try:
        if action == "radar":
            from . import customer_radar  # noqa: PLC0415
            return {"ok": True, **customer_radar.scan()}
        if action == "template":
            return {"ok": True, "template": load_template()}
        handler = {"upsert": action_upsert, "log": action_log, "done": action_done,
                   "get": action_get, "list": action_list}.get(action)
        if handler is None:
            raise ValueError("action 只能是 upsert / log / done / get / list / radar / template")
        return handler(args)
    except (ValueError, LookupError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}
