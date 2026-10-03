"""按员工自定义的模版写会议纪要 (10/3)。

缺省 (不传模版) 跟原来一模一样: meeting_minutes.render_markdown 出固定的
"摘要 / 决议 / 待定问题 / 待办"。员工在「会议」页可以存自己的模版 —— 一份 Markdown,
写好标题层级、表格, 括号里写每节要写什么 (Companion 存在 ~/.catfish/meeting-templates/,
生成时把模版正文随 RPC 传过来)。

做法: 结构化抽取照旧先跑一遍 (待办要用来「加入任务库」, 不能丢), 然后多调一次大模型,
拿转写 (长会议拿分段要点) + 已抽出的结构化结果, 按模版写成 Markdown。

占位符在发给大模型之前就换成真值 —— 别让模型去猜日期、参会人:
    {{会议标题}} {{日期}} {{参会人}} {{参会人数}} {{时长}}

10/3 下午: 模版也可以是单位定死的 Word / Excel 文件 (kind = docx / xlsx)。认空、填写在
meeting_file_template.py; 这里管"让大模型给每个空出值" (build_fill_messages / parse_fill)。
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


def build_fill_messages(head: str, known: dict[str, str], slots: list[dict], material: str, minutes: dict) -> list[dict]:
    sys_prompt = (
        "你是会议记录员, 要把会议内容填进单位固定的纪要表。只输出一个 JSON 对象, 不要任何解释:\n"
        "  键是每个空的 id; kind=text 的值给字符串 (多条内容用 \\n 分行, 按「一、二、」或「1. 2.」编号);\n"
        "  kind=table 的值给二维数组, 每行按 columns 的顺序给出每一格, 序号列填 1、2、3…。\n"
        "规则:\n"
        "1. 会议名称、日期、参会人员这类在「已知信息」里有的, 直接用已知值。\n"
        "2. 待办 / 下一步工作类的表格用「已抽取的结构化结果」里的 action_items, 负责人和时限照抄, 不要改。\n"
        "3. 只写会议材料里有的事实; 材料里没有的空 (主持人、地点、记录人之类没提到的) 填空字符串 \"\", 由员工手填, 不要编。\n"
        "4. 每个空按它的标签写对应内容, 不要把别的空的内容重复写进来。"
    )
    user = (
        f"{head}\n## 已知信息\n{json.dumps(known, ensure_ascii=False)}\n\n"
        f"## 表里要填的空\n{json.dumps(slots, ensure_ascii=False)}\n\n"
        f"## 已抽取的结构化结果\n{json.dumps(minutes, ensure_ascii=False)}\n\n"
        f"## 会议材料\n{material}"
    )
    return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]


def parse_fill(raw: str, slots: list[dict]) -> dict:
    """大模型给的 JSON → {slot id: 值}; 不认识的 id 丢掉, 类型不对的按空处理。"""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    text = m.group(1) if m else raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"大模型没按格式给出要填的内容: {raw[:200]!r}") from e
    out: dict = {}
    for s in slots:
        v = data.get(s["id"])
        if s["kind"] == "table":
            out[s["id"]] = [r if isinstance(r, list) else [r] for r in v] if isinstance(v, list) else []
        else:
            out[s["id"]] = "\n".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v))
    return out


def validate_template(tpl: object) -> dict | None:
    """RPC 里的 template 参数 → {id, name, body}; 没传 / 缺省模版 → None。"""
    if not tpl:
        return None
    if not isinstance(tpl, dict):
        raise ValueError("template 参数格式不对")
    kind = str(tpl.get("kind") or "markdown")
    if kind in ("docx", "xlsx"):
        from pathlib import Path  # noqa: PLC0415
        path = Path(str(tpl.get("path") or ""))
        if path.suffix.lower() != f".{kind}" or not path.is_file():
            raise ValueError(f"纪要模版文件找不到了: {path} (在「管理模版」里重新上传)")
        return {"id": str(tpl.get("id") or ""), "name": str(tpl.get("name") or "自定义模版"), "kind": kind,
                "path": str(path)}
    body = str(tpl.get("body") or "").strip()
    if not body:
        raise ValueError("纪要模版是空的")
    if len(body) > MAX_TEMPLATE_CHARS:
        raise ValueError(f"纪要模版太长 ({len(body)} 字, 上限 {MAX_TEMPLATE_CHARS})")
    return {"id": str(tpl.get("id") or ""), "name": str(tpl.get("name") or "自定义模版"), "kind": "markdown", "body": body}
