"""文书风格 = 员工指定的范文 + 写法特点 (style_samples.py, 11/12)。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

BODY = (
    "关于三季度资质维护工作的汇报\n\n"
    "本季度资质维护工作总体平稳，三项到期资质全部按期完成换证。现将有关情况汇报如下。\n\n"
    "一、完成情况。信息安全服务资质于九月通过监督审核；软件能力成熟度评估完成材料初审；"
    "履约能力认证进入现场审查准备阶段。\n\n"
    "| 资质 | 状态 | 到期 |\n| A | 已换证 | 2027 |\n\n"
    "- [ ] 打印计划表\n\n"
    "联系人：张三 13800001111 zhangsan@example.com 负责材料汇总。\n\n"
    "二、存在问题。部分支撑材料归档不及时，个别项目合同原件调取周期偏长，影响了申报进度。\n\n"
    "三、下一步安排。十月底前完成全部材料归档；十一月组织一次内部预审，确保现场审查一次通过。\n"
) * 2


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch):
    from catfish_tool_bridge import colleague_rooms_bigfiles as big
    from catfish_tool_bridge import style_samples as ss
    monkeypatch.setattr(ss, "STYLE_DIR", tmp_path / "style_samples")
    monkeypatch.setattr(ss, "LEGACY_FINGERPRINT", tmp_path / "style_fingerprint.json")
    stage = tmp_path / "staging"
    stage.mkdir()
    monkeypatch.setattr(big, "staged", lambda sid: stage / sid)
    return tmp_path


@pytest.fixture
def ss():
    from catfish_tool_bridge import style_samples
    return style_samples


def _stage(home: Path, sid: str, text: str) -> None:
    (home / "staging" / sid).write_text(text, encoding="utf-8")


def _add(ss, home, sid="s_aaaaaaaaaaaaaaaa", name="三季度汇报.txt", text=BODY):
    _stage(home, sid, text)
    return ss.add({"stage_id": sid, "name": name})


def test_empty_state_and_model_view(ss):
    st = ss.state()
    assert st["samples"] == [] and st["traits"] == [] and st["stale"] is False
    assert ss.for_model()["exists"] is False


def test_add_extracts_text_and_removes_staged_file(ss, home):
    st = _add(ss, home)
    assert [s["name"] for s in st["samples"]] == ["三季度汇报.txt"]
    assert st["samples"][0]["chars"] >= ss.MIN_SAMPLE_CHARS
    assert st["stale"] is True                       # 有范文, 还没总结特点
    assert not (home / "staging" / "s_aaaaaaaaaaaaaaaa").exists()


def test_add_rejects_short_unsupported_duplicate_and_too_many(ss, home):
    with pytest.raises(ValueError, match="太短"):
        _add(ss, home, text="太短了。")
    with pytest.raises(ValueError, match="只收"):
        _add(ss, home, name="表.xlsx")
    _add(ss, home)
    with pytest.raises(ValueError, match="已经在范文里"):
        _add(ss, home, name="同一篇.txt")
    for i in range(ss.MAX_SAMPLES - 1):
        _add(ss, home, name=f"第{i}篇.txt", text=BODY + f"\n补充说明第 {i} 条，情况属实。")
    with pytest.raises(ValueError, match="最多放"):
        _add(ss, home, name="多的.txt", text=BODY + "\n又一篇，内容不同。")


def test_excerpt_keeps_prose_drops_tables_checklists_contacts(ss):
    ex = ss.excerpt(BODY)
    assert "总体平稳" in ex
    assert "|" not in ex and "- [ ]" not in ex and "13800001111" not in ex
    assert len(ex) <= ss.EXCERPT_CHARS + 2


def test_set_traits_normalizes_and_clears_stale(ss, home):
    _add(ss, home)
    st = ss.set_traits({"traits": ["1. 开头一句话先说结论", "- 分点用一、二、三", "", "开头一句话先说结论"]})
    assert st["traits"] == ["开头一句话先说结论", "分点用一、二、三"]
    assert st["stale"] is False
    _add(ss, home, sid="s_bbbbbbbbbbbbbbbb", name="另一篇.txt", text=BODY + "\n新增一段，情况有变化。")
    assert ss.state()["stale"] is True               # 范文换了, 特点还是旧的


def test_for_model_returns_traits_and_excerpts(ss, home):
    _add(ss, home)
    ss.set_traits({"traits": ["开头一句话先说结论"]})
    m = ss.style_fingerprint_get({})["result"]
    assert m["exists"] is True and m["traits"] == ["开头一句话先说结论"]
    assert m["excerpts"][0]["from"] == "三季度汇报.txt" and "总体平稳" in m["excerpts"][0]["text"]


def test_remove_last_sample_drops_traits(ss, home):
    sid = _add(ss, home)["samples"][0]["id"]
    ss.set_traits({"traits": ["短句为主"]})
    st = ss.remove({"id": sid})
    assert st["samples"] == [] and st["traits"] == []
    with pytest.raises(ValueError):
        ss.remove({"id": "../../etc"})


def test_summarize_sends_samples_and_stores_traits(ss, home, monkeypatch):
    _add(ss, home)
    seen = {}

    async def fake(messages, **kw):
        seen["user"], seen["kw"] = messages[1]["content"], kw
        return '<think>想一下</think>["开头一句话先说结论", "分点用一、二、三", "结尾写下一步安排"]'

    monkeypatch.setattr("catfish_tool_bridge.gateway_llm.call_llm", fake)
    st = asyncio.run(ss.summarize({"auth_token": "t", "gateway_url": "http://gw/"}))
    assert st["traits"] == ["开头一句话先说结论", "分点用一、二、三", "结尾写下一步安排"] and st["stale"] is False
    assert "总体平稳" in seen["user"] and seen["kw"]["source"] == "plugin:toolbridge-style-samples"


def test_summarize_needs_login_and_samples_and_usable_output(ss, home, monkeypatch):
    with pytest.raises(ValueError, match="没登录"):
        asyncio.run(ss.summarize({}))
    with pytest.raises(ValueError, match="先放"):
        asyncio.run(ss.summarize({"auth_token": "t", "gateway_url": "http://gw"}))
    _add(ss, home)

    async def junk(messages, **kw):
        return ""

    monkeypatch.setattr("catfish_tool_bridge.gateway_llm.call_llm", junk)
    with pytest.raises(RuntimeError):
        asyncio.run(ss.summarize({"auth_token": "t", "gateway_url": "http://gw"}))
    assert ss.state()["traits"] == []


def test_clear_tool_removes_samples_and_legacy_file(ss, home):
    _add(ss, home)
    (home / "style_fingerprint.json").write_text("{}", encoding="utf-8")
    ss.style_fingerprint_clear({})
    assert ss.state()["samples"] == [] and not (home / "style_fingerprint.json").exists()
    assert ss.style_fingerprint_refresh({})["result"]["ok"] is False


def test_rpc_dispatch(ss, home):
    from catfish_tool_bridge import server
    _stage(home, "s_cccccccccccccccc", BODY)

    async def run():
        r = await server._handle_request({"jsonrpc": "2.0", "id": 1, "method": "style/add",
                                         "params": {"stage_id": "s_cccccccccccccccc", "name": "汇报.md"}})
        bad = await server._handle_request({"jsonrpc": "2.0", "id": 2, "method": "style/remove", "params": {"id": "x"}})
        return r, bad

    r, bad = asyncio.run(run())
    assert json.dumps(r, ensure_ascii=False).count("汇报.md") == 1
    assert "error" in bad
