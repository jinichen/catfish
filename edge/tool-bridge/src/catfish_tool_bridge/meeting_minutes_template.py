"""按员工自定义的模版写会议纪要 (10/3)。

缺省 (不传模版) 跟原来一模一样: meeting_minutes.render_markdown 出固定的
"摘要 / 决议 / 待定问题 / 待办"。员工在「会议」页可以存自己的模版 —— 一份 Markdown,
写好标题层级、表格, 括号里写每节要写什么 (Companion 存在 ~/.catfish/meeting-templates/,
生成时把模版正文随 RPC 传过来)。

做法: 结构化抽取照旧先跑一遍 (待办要用来「加入任务库」, 不能丢), 然后多调一次大模型,
拿转写 (长会议拿分段要点) + 已抽出的结构化结果, 按模版写成 Markdown。

占位符在发给大模型之前就换成真值 —— 别让模型去猜日期、参会人:
    {{会议标题}} {{日期}} {{参会人}} {{参会人数}} {{时长}}
"""
from __future__ import annotations

import json
import re

MAX_TEMPLATE_CHARS = 8000

PLACEHOLDERS = ("会议标题", "日期", "参会人", "参会人数", "时长")


def _duration(meta: dict, transcript: dict) -> str:
    secs = float(meta.get("duration_secs") or 0) or max(
        (float(s.get("end", 0)) for s in transcript.get("segments") or []), default=0.0,
    )
    m = int(secs // 60)
    return f"{m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"{m} 分钟"


def fill_placeholders(template: str, meta: dict, transcript: dict, names: dict[str, str], date: str) -> str:
    values = {
        "会议标题": str(meta.get("title") or ""),
        "日期": date,
        "参会人": "、".join(sorted(set(names.values()))) or "(未标注)",
        "参会人数": str(meta.get("attendees") or ""),
        "时长": _duration(meta, transcript),
    }
    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", lambda m: values.get(m.group(1), m.group(0)), template)


def build_template_messages(head: str, template: str, material: str, minutes: dict) -> list[dict]:
    sys_prompt = (
        "你是会议记录员, 按员工给的模版写会议纪要, 输出 Markdown 正文, 不要代码块、不要任何解释。\n"
        "规则:\n"
        "1. 保留模版的标题、层级、顺序和表格表头; 模版里的固定文字原样保留。\n"
        "2. 括号里 (全角或半角) 是写作说明, 照说明填写内容, 说明本身不要出现在结果里。\n"
        "3. 只写会议材料里有的事实; 某一节材料里没有就写「(无)」, 不要编。\n"
        "4. 人名、负责人、截止日期以「已抽取的结构化结果」为准, 不要自己改。"
    )
    user = (
        f"{head}\n"
        f"## 模版\n{template}\n\n"
        f"## 已抽取的结构化结果\n{json.dumps(minutes, ensure_ascii=False)}\n\n"
        f"## 会议材料\n{material}"
    )
    return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]


def clean_markdown(raw: str) -> str:
    """模型偶尔还是会包一层 ```markdown ... ```, 剥掉。"""
    text = raw.strip()
    m = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n?```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def validate_template(tpl: object) -> dict | None:
    """RPC 里的 template 参数 → {id, name, body}; 没传 / 缺省模版 → None。"""
    if not tpl:
        return None
    if not isinstance(tpl, dict):
        raise ValueError("template 参数格式不对")
    body = str(tpl.get("body") or "").strip()
    if not body:
        raise ValueError("纪要模版是空的")
    if len(body) > MAX_TEMPLATE_CHARS:
        raise ValueError(f"纪要模版太长 ({len(body)} 字, 上限 {MAX_TEMPLATE_CHARS})")
    return {"id": str(tpl.get("id") or ""), "name": str(tpl.get("name") or "自定义模版"), "body": body}
