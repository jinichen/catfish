"""11/15 对话卡片只给声明过能画的客户端 (上游 rendererCapabilities): Companion 主对话随请求头声明,
微信 / 定时任务 / 旧版 Companion 不声明 → 调 catfish_form card 被拦下, 让模型改用文字问。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("card_channel_guard", Path(__file__).parent.parent / "card_channel_guard.py")
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)

CAPS = json.dumps({"1.0": {"supportedCatalogIds": [g.CATALOG_ID]}})
CARD = {"action": "card", "request": "选时间"}
TOOL = "mcp__catfish_tools__catfish_form"


def test_declared_capability_allows():
    assert g.decide(TOOL, CARD, CAPS) is None


@pytest.mark.parametrize("caps", [None, "", "not json", json.dumps({"1.0": {"supportedCatalogIds": ["https://other/catalog.json"]}}),
                                  json.dumps({"0.9": {"supportedCatalogIds": [g.CATALOG_ID]}})])
def test_no_or_wrong_capability_blocks(caps):
    r = g.decide(TOOL, CARD, caps, platform="weixin")
    assert r["action"] == "block" and "改用文字" in r["message"]


def test_other_actions_and_tools_untouched():
    assert g.decide(TOOL, {"action": "create"}, None) is None
    assert g.decide("todo", CARD, None) is None


def test_eval_switch_only_opens_cli(monkeypatch):
    monkeypatch.setenv("CATFISH_A2UI_ASSUME_CAPABLE", "1")
    assert g.decide(TOOL, CARD, None, platform="tool") is None
    assert g.decide(TOOL, CARD, None, platform="weixin")["action"] == "block"
    assert g.decide(TOOL, CARD, None, platform="cron")["action"] == "block"


def test_session_platform(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'tool')")
    conn.commit()
    conn.close()
    assert g.session_platform("s1", tmp_path) == "tool"
    assert g.session_platform("nope", tmp_path) == ""
    assert g.session_platform("s1", tmp_path / "missing") == ""


def test_registry_roundtrip():
    spec = importlib.util.spec_from_file_location("session_registry", Path(__file__).parent.parent / "session_registry.py")
    reg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reg)
    reg.register_a2ui("s1", CAPS)
    reg.register_a2ui("s1", "")          # 空值不覆盖
    assert reg.lookup_a2ui("s1") == CAPS and reg.lookup_a2ui("s2") is None


def test_header_registered_on_session_then_hook_allows(monkeypatch, tmp_path):
    """请求头 → 中间件放进 CV_A2UI_CAPS → admit 时登记到会话 → 出卡前的钩子按会话放行; 没声明的会话拦下。"""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))   # 没有 state.db: 评估开关那条查不到来源
    import plugin_api_turn as pat
    import plugin_ctx
    token = plugin_ctx.CV_A2UI_CAPS.set(CAPS)
    try:
        pat._register("sess-pc")
    finally:
        plugin_ctx.CV_A2UI_CAPS.reset(token)
    assert pat.card_capability_hook(tool_name=TOOL, args=CARD, session_id="sess-pc") is None
    blocked = pat.card_capability_hook(tool_name=TOOL, args=CARD, session_id="sess-wx")
    assert blocked["action"] == "block"
    assert pat.card_capability_hook(tool_name=TOOL, args={"action": "list"}, session_id="sess-wx") is None
