"""鲶鱼对话卡片的组件目录 (A2UI v1.0, 11/15)。

从上游基础目录 (data/a2ui/basic_catalog_v1.json, 原样拷的) 派生一份鲶鱼自己的目录:
  · 基础组件里留 BASIC_COMPONENTS (12 个; 不放 Image / Video / AudioPlayer —— 会去外网拉东西, Modal / Tabs / Icon 对话里用不上)
  · 加 4 个鲶鱼自定义组件 (CUSTOM_COMPONENTS): 邮件卡 / 日程卡 / 待办项 / 文件卡 —— 模型只填数据,
    Companion 用自己的界面画 (companion-app/src/tabs/Chat/catfishCardComponents.tsx), 点了能跳到邮件页 / 打开本机文件
  · 函数留 FUNCTIONS (校验 / 逻辑 / 格式化); 去掉 openUrl (卡片不该能打开任意网址)

上游的说法: 一个目录 = 组件 + 函数的 JSON Schema, 智能体按它生成提示词、校验输出, 渲染端按同一个 id 认组件。
生成结果存 data/a2ui/catfish_catalog_v1.json (tests/test_a2ui_catalog.py 钉住跟这里生成的一致), 两边都读它:
tool-bridge 的专员 (a2ui_card.py) 用官方 SDK 加载它; Companion 声明自己能画这个 id (lib/a2uiCard.ts CARD_CATALOG_ID)。

重新生成: python -m catfish_tool_bridge.a2ui_catalog
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data" / "a2ui"
BASIC_PATH = DATA / "basic_catalog_v1.json"
CATALOG_PATH = DATA / "catfish_catalog_v1.json"
CATALOG_ID = "https://catfish.local/a2ui/catalogs/catfish/v1.json"

BASIC_COMPONENTS = ("Text", "Row", "Column", "List", "Card", "Divider",
                    "Button", "TextField", "CheckBox", "ChoicePicker", "Slider", "DateTimeInput")
FUNCTIONS = ("required", "regex", "length", "numeric", "email",
             "formatString", "formatNumber", "formatCurrency", "formatDate", "pluralize", "and", "or", "not")

_DS = {"$ref": "common_types.json#/$defs/DynamicString"}
_DB = {"$ref": "common_types.json#/$defs/DynamicBoolean"}
_DSL = {"$ref": "common_types.json#/$defs/DynamicStringList"}
_DV = {"$ref": "common_types.json#/$defs/DynamicValue"}
_DN = {"$ref": "common_types.json#/$defs/DynamicNumber"}
_W = {"$ref": "#/$defs/Weight"}


def _comp(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Express 按属性的声明顺序认位置参数 —— 必填的放前面, 模型按位置写才不会错位。"""
    return {"type": "object", "description": description,
            "properties": {"component": {"const": name}, **props, "weight": _W},
            "required": ["component", *required]}


#: 鲶鱼自定义组件 (+ 上游官方扩展的 VegaChart)。描述会进专员的提示词 (SDK 生成签名时带上), 写清「数据从哪来、不许编」。
CUSTOM_COMPONENTS: dict[str, dict[str, Any]] = {
    "EmailCard": _comp(
        "EmailCard",
        "One email from the employee's mailbox, shown as Catfish's email card with an 'open in Mail' link. "
        "Only use emails the request gives (their id, sender, subject); never invent one.",
        {"emailId": {"type": "string", "description": "The email id exactly as given in the request (static)."},
         "subject": {**_DS, "description": "Email subject."},
         "sender": {**_DS, "description": "Sender name or address."},
         "date": {**_DS, "description": "When it was received, e.g. 11/15 09:30."},
         "snippet": {**_DS, "description": "One-line summary or first line of the body."}},
        ["emailId", "subject", "sender"]),
    "EventCard": _comp(
        "EventCard",
        "One calendar event, shown as Catfish's schedule card. Only use events the request gives; never invent one.",
        {"title": {**_DS, "description": "Event title."},
         "start": {**_DS, "description": "Start time, e.g. 2026-11-17 10:00."},
         "end": {**_DS, "description": "End time."},
         "location": {**_DS, "description": "Room or meeting link text."},
         "attendees": {**_DSL, "description": "Attendee names."}},
        ["title", "start"]),
    "TodoItem": _comp(
        "TodoItem",
        "One to-do item the employee can tick off, shown as Catfish's to-do row (checkbox + title + due date). "
        "Bind 'done' to the data model so the ticked state is sent back with the button's action.",
        {"title": {**_DS, "description": "What to do."},
         "done": {**_DB, "description": "Whether it is done; bind to a data path."},
         "due": {**_DS, "description": "Due date, e.g. 11/17."}},
        ["title", "done"]),
    "FileCard": _comp(
        "FileCard",
        "One local file on the employee's computer, shown as Catfish's file chip; clicking opens it. "
        "Only use absolute paths the request gives; never a URL.",
        {"path": {"type": "string", "description": "Absolute local path exactly as given (static)."},
         "name": {**_DS, "description": "Display name; defaults to the file name."}},
        ["path"]),
    # 11/16 数据图表: 照上游官方扩展目录 catalogs/vegachart/v1 (data/a2ui/vegachart_catalog_v1.json, 原样拷的) 的 VegaChart,
    # 属性一致 (spec / height); 描述多写了鲶鱼的限制 —— 只认 Vega-Lite、数据直接写进 data.values (a2ui_chart.py 校验, Companion 再查一遍)
    "VegaChart": _comp(
        "VegaChart",
        "Displays a Vega-Lite chart (bar / line / area / pie / point). Data must be inline in data.values, using only numbers "
        "the request gives; never a URL, never invented numbers. Put a one-line Text conclusion next to it.",
        {"spec": {**_DV, "description": "The Vega-Lite spec to render as a JSON object, or a data binding to one."},
         "height": {**_DN, "description": "Optional height of the chart in pixels. Defaults to 290."}},
        ["spec"]),
}


#: 上游目录说明里第二个例子 (List 模板) 用的是 Image —— 我们目录里没有, 换成 Text, 免得专员照抄
_IMAGE_TEMPLATE = (('"component": "Image",\n          "url": {"@path": "url"}',
                    '"component": "Text",\n          "text": {"@path": "name"}'),
                   ('"url": "https://example.com/poodle.jpg"', '"name": "Poodle"'),
                   ('"url": "https://example.com/lab.jpg"', '"name": "Labrador"'))


def _instructions(basic: dict[str, Any]) -> str:
    """上游说明 (布局 / formatString / 校验写法 / 两个例子) 原样留, 例子里的目录 id 换成我们的, Image 例子换成 Text。"""
    text = str(basic.get("instructions") or "").replace(str(basic["$id"]), CATALOG_ID)
    for old, new in _IMAGE_TEMPLATE:
        if old not in text:
            raise ValueError(f"上游目录说明变了, 改 a2ui_catalog._IMAGE_TEMPLATE: {old[:40]}")
        text = text.replace(old, new)
    return text


def build() -> dict[str, Any]:
    basic = json.loads(BASIC_PATH.read_text(encoding="utf-8"))
    cat = copy.deepcopy(basic)
    cat["$id"] = CATALOG_ID
    cat["catalogId"] = CATALOG_ID
    cat["title"] = "Catfish chat cards"
    cat["instructions"] = _instructions(basic)
    cat["description"] = ("Cards shown inside Catfish Companion chat: the A2UI v1.0 basic components minus media / modal / tabs, "
                          "plus Catfish email, calendar, to-do and file components.")
    cat["components"] = {n: basic["components"][n] for n in BASIC_COMPONENTS} | copy.deepcopy(CUSTOM_COMPONENTS)
    cat["functions"] = {n: basic["functions"][n] for n in FUNCTIONS}
    names = [*BASIC_COMPONENTS, *CUSTOM_COMPONENTS]
    cat["$defs"]["anyComponent"] = {"oneOf": [{"$ref": f"#/components/{n}"} for n in names],
                                    "discriminator": {"propertyName": "component"}}
    any_fn = cat["$defs"].get("anyFunction")
    if isinstance(any_fn, dict) and "oneOf" in any_fn:
        any_fn["oneOf"] = [r for r in any_fn["oneOf"] if r.get("$ref", "").rsplit("/", 1)[-1] in FUNCTIONS]
    return cat


def component_names() -> tuple[str, ...]:
    return (*BASIC_COMPONENTS, *CUSTOM_COMPONENTS)


if __name__ == "__main__":
    CATALOG_PATH.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {CATALOG_PATH} ({len(component_names())} components)")
