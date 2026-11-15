"""11/15 鲶鱼卡片目录 (a2ui_catalog.py): 从上游基础目录派生 + 4 个自定义组件; 用官方 SDK 编得过。"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("a2ui")

from catfish_tool_bridge import a2ui_card as ac  # noqa: E402
from catfish_tool_bridge import a2ui_catalog as cat  # noqa: E402

SAMPLE = """<a2ui>
root = Card(Column([Text("**今天要处理的**"), EmailCard("apple_mail|Google|2610", "报价确认", "王总", "11/15 09:30"), TodoItem("交发票", $/f/invoice, "11/17"), FileCard("/Users/x/报价.xlsx"), EventCard("周例会", "2026-11-17 10:00"), Slider("满意度", 1, 5, $/f/score), List(_template($/items, row)), Row([Button(Text("同意"), "primary", Event("approve", {f: $/f})), Button(Text("驳回"), _, Event("reject", {f: $/f}))])]))
row = TodoItem($title, $done)
$/items = [{title: "写周报", done: false}]
$/f/invoice = false
$/f/score = 3
</a2ui>"""


def test_generated_file_is_up_to_date():
    """改了 a2ui_catalog.py 要重新生成: python -m catfish_tool_bridge.a2ui_catalog"""
    assert json.loads(cat.CATALOG_PATH.read_text(encoding="utf-8")) == json.loads(json.dumps(cat.build()))


def test_catalog_contents():
    c = cat.build()
    assert c["catalogId"] == cat.CATALOG_ID
    assert list(c["components"]) == list(cat.component_names())
    assert not ({"Image", "Video", "AudioPlayer", "Modal", "Tabs", "Icon"} & set(c["components"]))
    assert "openUrl" not in c["functions"]


def test_custom_components_list_slider_and_buttons_compile():
    msgs = ac.compile_reply(SAMPLE)
    comps = msgs[0]["createSurface"]["components"]
    assert msgs[0]["createSurface"]["catalogId"] == cat.CATALOG_ID
    names = {x["component"] for x in comps}
    assert {"EmailCard", "TodoItem", "FileCard", "EventCard", "Slider", "List"} <= names
    assert sum(1 for x in comps if x["component"] == "Button") == 2
    todo = next(x for x in comps if x["component"] == "TodoItem" and x.get("title") == "交发票")
    assert todo["done"] == {"@path": "/f/invoice"} and todo["due"] == "11/17"


def test_specialist_prompt_lists_custom_components_and_rules():
    system = ac.specialist_messages("x")[0]["content"]
    for name in ("EmailCard(", "EventCard(", "TodoItem(", "FileCard(", "List(", "Slider("):
        assert name in system
    assert "每个选项一个按钮" in system and "_template($/items" in system


def test_instructions_point_at_our_catalog_without_image():
    """上游说明里的例子换成我们的目录 id、List 模板用 Text (目录里没有 Image, 专员别照抄)"""
    from catfish_tool_bridge.a2ui_catalog import CATALOG_ID, build
    ins = build()["instructions"]
    assert CATALOG_ID in ins and "a2ui.org" not in ins
    assert '"component": "Image"' not in ins and "example.com" not in ins
