"""11/15 对话卡片 (a2ui_card.py): 照上游 subagent_tool —— 专员写 Express、SDK 校验、不过带着报错重写、存本机只回编号。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("a2ui")

from catfish_tool_bridge import a2ui_card as ac  # noqa: E402

GOOD = """<a2ui>
root = Card(body)
body = Column([Text("**约周例会**"), ChoicePicker("时间", _, [{label: "周二 10:00", value: "2026-11-17T10:00"}], $/f/time, "chips"), Button(Text("就这样定"), "primary", Event("book_meeting", {meeting: $/f}))])
$/f/time = []
</a2ui>"""
#: 引用了没写的子组件 —— 11/15 直接写 JSON 时模型就这么漏过整行
MISSING_CHILD = """<a2ui>
root = Card(body)
body = Column([title, row3])
title = Text("x")
</a2ui>"""
#: 白名单外的组件 (会去外网拉图)
IMAGE = """<a2ui>
root = Card(Image("https://tracker.example.com/p.gif"))
</a2ui>"""

COMPANION_TS = Path(__file__).resolve().parents[2] / "companion-app" / "src" / "lib" / "a2uiCard.ts"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))


def fake_llm(*replies):
    seen: list[list[dict]] = []

    async def call(messages):
        seen.append(messages)
        return replies[len(seen) - 1]
    call.seen = seen  # type: ignore[attr-defined]
    return call


def test_good_card_saved_out_of_band(tmp_path):
    llm = fake_llm("好的\n" + GOOD)
    r = ac.make_card({"request": "让员工从两个时段里选一个会议时间"}, llm=llm)
    assert r["ok"] is True and r["card_id"].startswith("c_")
    # 主小鲶只拿到编号和一句话, 不拿卡片本身 (上游 saved out-of-band; 也碰不到 4KB 归档)
    assert "createSurface" not in json.dumps(r, ensure_ascii=False)
    saved = json.loads((tmp_path / "cards" / f"{r['card_id']}.json").read_text(encoding="utf-8"))
    assert saved["request"] == "让员工从两个时段里选一个会议时间"
    surface = saved["messages"][0]["createSurface"]
    assert surface["catalogId"] == "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
    assert {c["component"] for c in surface["components"]} <= set(ac.CARD_COMPONENTS)


@pytest.mark.parametrize("bad", [MISSING_CHILD, IMAGE, "没有卡片, 只是一句话"])
def test_rejected_then_fixed(bad):
    llm = fake_llm(bad, GOOD)
    r = ac.make_card({"request": "让员工从两个时段里选一个会议时间"}, llm=llm)
    assert r["ok"] is True
    # 第二次带着上一次的回复和报错 (上游 _retry_message)
    second = llm.seen[1]
    assert second[-2] == {"role": "assistant", "content": bad}
    assert second[-1]["content"].startswith("你上一次的回复被拒了")


def test_gives_up_after_two_attempts_and_says_use_text():
    llm = fake_llm(MISSING_CHILD, MISSING_CHILD)
    r = ac.make_card({"request": "让员工从两个时段里选一个会议时间"}, llm=llm)
    assert r["ok"] is False and "改用文字问员工" in r["error"]
    assert len(llm.seen) == ac.MAX_ATTEMPTS


def test_request_checks():
    assert ac.make_card({}, llm=fake_llm())["ok"] is False
    assert "太短" in ac.make_card({"request": "占位"}, llm=fake_llm())["error"]
    assert "太长" in ac.make_card({"request": "x" * (ac.MAX_REQUEST_CHARS + 1)}, llm=fake_llm())["error"]


def test_prompt_comes_from_sdk_and_only_lists_whitelist():
    system = ac.specialist_messages("x")[0]["content"]
    assert "<a2ui>" in system and "Positional Component Signatures" in system
    sigs = set(re.findall(r"^• (\w+)\(", system, re.M))
    assert set(ac.CARD_COMPONENTS) <= sigs
    assert not ({"Image", "Video", "Modal", "List", "AudioPlayer"} & sigs)
    assert "openUrl(" not in system


def test_whitelist_matches_companion():
    ts = COMPANION_TS.read_text(encoding="utf-8")
    comps = re.search(r"export const CARD_COMPONENTS = \[(.*?)\] as const;", ts, re.S).group(1)
    assert tuple(re.findall(r'"(\w+)"', comps)) == ac.CARD_COMPONENTS


def test_old_cards_pruned(tmp_path):
    import os
    old = ac.save_card([{"x": 1}], "旧的", now=1_000_000_000)
    p = tmp_path / "cards" / f"{old}.json"
    os.utime(p, (1_000_000_000, 1_000_000_000))
    ac.save_card([{"x": 2}], "新的")
    assert not p.exists()


def test_routed_as_catfish_form_card(monkeypatch):
    """不单独开工具 (网关常驻工具已 49 个, Qwen 50+ 返空): 走 catfish_form 的 card 动作。"""
    from catfish_tool_bridge import forms_tool
    from catfish_tool_bridge.catfish_tool_schemas import CATFISH_NATIVE_TOOLS
    seen = {}
    monkeypatch.setattr(ac, "make_card", lambda args, **kw: seen.setdefault("args", args) and {"ok": True})
    forms_tool.dispatch("catfish_form", {"action": "card", "request": "选时间"})
    assert seen["args"]["request"] == "选时间"
    form = next(t for t in CATFISH_NATIVE_TOOLS if t["name"] == "catfish_form")
    assert "card" in form["input_schema"]["properties"]["action"]["enum"]
    assert form["input_schema"]["properties"]["request"]["description"].startswith("card:")
    assert "catfish_card" not in {t["name"] for t in CATFISH_NATIVE_TOOLS}


def test_generation_rules_cover_seen_rejections():
    """11/15 真模型评估里被拒的四类原因都写进了专员的规则 (上游 generation_rules)。"""
    system = ac.specialist_messages("x")[0]["content"]
    for rule in ("只用英文字母", "Card 只放一个子组件", "\"checkbox\" 或 \"chips\"", "DateTimeInput 不写 min / max", "一定带中文提示"):
        assert rule in system
