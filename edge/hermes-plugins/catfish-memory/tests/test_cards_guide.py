"""11/15 对话卡片 (A2UI) 规范: 只给电脑端主对话; 规范里的白名单 / 目录 id / 两句话开头跟 Companion 一致; 例子能画。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import catfish_memory_cards as cards

_COMPANION_TS = Path(__file__).resolve().parents[3] / "companion-app" / "src" / "lib" / "a2uiCard.ts"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CATFISH_CARDS_FORCE", raising=False)
    cards._SOURCE_CACHE.clear()


def _state_db(tmp_path: Path, rows):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    conn.executemany("INSERT INTO sessions VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return tmp_path


def test_only_companion_primary_gets_guide(tmp_path):
    assert cards.render_cards_guide("s", tmp_path, platform="api_server", agent_context="primary") == cards.GUIDE
    assert cards.render_cards_guide("s", tmp_path, platform="weixin", agent_context="primary") == ""
    # 电脑端里跑的定时任务 / 子任务也不给: 没人在对话里看着
    assert cards.render_cards_guide("s", tmp_path, platform="api_server", agent_context="cron") == ""
    assert cards.render_cards_guide("s", tmp_path, platform="api_server", agent_context="subagent") == ""


def test_old_hermes_without_platform_falls_back_to_state_db(tmp_path):
    home = _state_db(tmp_path, [("pc", "api_server"), ("wx", "weixin")])
    assert cards.render_cards_guide("pc", home) == cards.GUIDE
    assert cards.render_cards_guide("wx", home) == ""
    # 查不到 (新会话第一轮还没写库 / 没有库) → 不给, 不往微信发 JSON
    assert cards.render_cards_guide("new", home) == ""
    assert cards.render_cards_guide("pc", tmp_path / "nope") == ""


def test_force_env_only_opens_cli_for_eval(monkeypatch, tmp_path):
    monkeypatch.setenv("CATFISH_CARDS_FORCE", "1")
    assert cards.render_cards_guide("x", tmp_path, platform="cli") == cards.GUIDE
    # 网关带着评估开关跑时, 员工的微信 / 定时任务照样不给
    assert cards.render_cards_guide("x", tmp_path, platform="weixin") == ""
    assert cards.render_cards_guide("x", tmp_path, platform="cli", agent_context="cron") == ""


def test_guide_matches_companion():
    ts = _COMPANION_TS.read_text(encoding="utf-8")
    comps = re.search(r"export const CARD_COMPONENTS = \[(.*?)\] as const;", ts, re.S).group(1)
    assert tuple(re.findall(r'"(\w+)"', comps)) == cards.CARD_COMPONENTS
    assert f'CARD_CATALOG_ID = "{cards.CATALOG_ID}"' in ts
    assert f'SUBMIT_HEAD = "{cards.SUBMIT_HEAD}"' in ts
    assert f'RETRY_HEAD = "{cards.RETRY_HEAD}"' in ts
    for name in cards.CARD_COMPONENTS:
        assert name in cards.GUIDE


def test_guide_example_is_a_complete_card():
    """规范里的例子模型会照着学: 必须完整 (有 root、引用的 id 都在、只用白名单组件、目录 id 对)。"""
    m = re.search(r"```a2ui\n(.*?)\n```", cards.GUIDE, re.S)
    msgs = json.loads(m.group(1))
    assert msgs[0]["createSurface"]["catalogId"] == cards.CATALOG_ID
    comps = msgs[1]["updateComponents"]["components"]
    ids = {c["id"] for c in comps}
    refs = {"root"}
    for c in comps:
        assert c["component"] in cards.CARD_COMPONENTS
        if isinstance(c.get("child"), str):
            refs.add(c["child"])
        refs.update(x for x in c.get("children", []) if isinstance(x, str))
    assert refs <= ids
    assert "updateDataModel" in msgs[2]
