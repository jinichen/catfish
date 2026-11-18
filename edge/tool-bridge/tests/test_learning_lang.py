"""学习第 5 步 (11/18, learning_lang.py): 外语 —— 情景对话 (开场 / 纠错 + 接着说) / 跟读逐词对照 (不调模型) / 复盘做卡。大模型用假的。"""
from __future__ import annotations

import asyncio
import json

import pytest

from catfish_tool_bridge import gateway_llm, learning, learning_lang, learning_store, practice


@pytest.fixture(autouse=True)
def _env():
    learning._RUNNING.clear()


def _llm(monkeypatch, replies: list):
    seen: list = []

    async def fake(messages, **kw):
        seen.append(messages)
        r = replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
    monkeypatch.setattr(gateway_llm, "call_llm", fake)
    return seen


def _p(tmp_path, **kw):
    return {"catfish_home": str(tmp_path), "auth_token": "t", "gateway_url": "http://gw", "model": "m", **kw}


async def _run(tmp_path, method, **kw):
    r = await learning.handle(method, _p(tmp_path, **kw))
    await asyncio.gather(*list(learning._TASKS))
    return r


def _get(tmp_path, sid):
    return asyncio.run(learning.handle("learn/get", _p(tmp_path, id=sid)))


def test_compare_words_and_cjk():
    r = learning_lang.compare("We usually isolate the server first.", "we usually isolated server first", "en")
    assert r["missed"] == ["isolate", "the"] and r["extra"] == ["isolated"] and r["score"] == round(4 / 6, 2)
    assert learning_lang.compare("Hello there", "hello there", "en")["score"] == 1.0
    j = learning_lang.compare("会議の資料", "会議資料", "ja")
    assert j["missed"] == ["の"] and j["score"] == 0.8
    assert learning_lang.tokens("Don't stop, OK?", "en") == ["don't", "stop", "ok"]


def test_language_session_dialogue_flow(tmp_path, monkeypatch):
    v = asyncio.run(_run(tmp_path, "learn/create", goal="商务英语: 跟海外客户开会", mode="language", lang="en"))
    sid = v["session"]["id"]
    assert v["session"]["phase"] == "lang" and v["session"]["lang"] == {"code": "en", "activity": None, "ending": False}
    assert v["running"] is False                                          # 外语先等他选练法, 不自动调模型
    seen = _llm(monkeypatch, [{"role": "海外客户 Tom", "setting": "视频会议确认交付时间", "line": "Hi, can we confirm the delivery date?", "zh": "嗨, 能确认下交付日期吗?"}])
    asyncio.run(_run(tmp_path, "learn/lang_start", id=sid, activity="dialogue", scene="确认交付时间"))
    assert "目标语言: 英语 (en)" in seen[0][0]["content"] and "确认交付时间" in seen[0][1]["content"]
    seen = _llm(monkeypatch, [{"fixes": [{"said": "We will finish at next Friday", "better": "We'll finish by next Friday", "why": "截止用 by"}],
                               "line": "Great. What if there's a delay?", "zh": "好。要是延期呢?"}])
    asyncio.run(_run(tmp_path, "learn/lang_say", id=sid, text="We will finish at next Friday", secs=4))
    assert "员工: We will finish at next Friday" in seen[0][1]["content"]
    v = _get(tmp_path, sid)
    kinds = [e["kind"] for e in v["events"]]
    assert kinds[-4:] == ["lang_line", "lang_line", "lang_fix", "lang_line"] and v["session"]["stalled"] is False
    with pytest.raises(ValueError, match="外语练习时"):
        asyncio.run(learning.handle("learn/say", _p(tmp_path, id=sid, text="hi")))
    # 结束 → 复盘做卡
    _llm(monkeypatch, [{"summary": "能沟通, 介词要注意", "expressions": [{"text": "by next Friday", "zh": "下周五之前"}],
                        "mistakes": [{"said": "at next Friday", "better": "by next Friday", "why": "截止用 by"}],
                        "cards": [{"point": "截止用 by", "question": "想说「下周五之前完成」, 英语怎么说?", "answer": "We'll finish by next Friday."}]}])
    asyncio.run(_run(tmp_path, "learn/lang_end", id=sid))
    v = _get(tmp_path, sid)
    assert v["events"][-1]["kind"] == "lang_recap" and v["events"][-1]["cards"] == 1 and v["session"]["lang"]["activity"] is None
    con = practice.connect(tmp_path / "practice.db")
    assert json.loads(con.execute("SELECT tags FROM questions").fetchone()[0]) == ["英语", "商务英语: 跟海外客户开会"]
    con.close()


def test_shadow_flow_no_llm_for_compare(tmp_path, monkeypatch):
    sid = asyncio.run(_run(tmp_path, "learn/create", goal="日语: 接待日本客户", mode="language", lang="ja"))["session"]["id"]
    _llm(monkeypatch, [{"setting": "接待客户", "sentences": [{"text": "会議の資料を送ります。", "zh": "我把会议资料发过去。", "note": ""}] * 5}])
    asyncio.run(_run(tmp_path, "learn/lang_start", id=sid, activity="shadow"))
    v = _get(tmp_path, sid)
    assert v["events"][-1]["kind"] == "shadow_set" and len(v["events"][-1]["sentences"]) == 5
    _llm(monkeypatch, [])                                                  # 对照不调模型
    v = asyncio.run(learning.handle("learn/lang_shadow", _p(tmp_path, id=sid, idx=0, heard="会議資料を送ります", secs=3)))
    r = v["events"][-1]
    assert r["kind"] == "shadow_result" and r["missed"] == ["の"] and r["score"] == 0.9
    assert v["session"]["stalled"] is False and v["running"] is False
    with pytest.raises(ValueError, match="没有这一句"):
        asyncio.run(learning.handle("learn/lang_shadow", _p(tmp_path, id=sid, idx=9, heard="x")))
    with pytest.raises(ValueError, match="没听到"):
        asyncio.run(learning.handle("learn/lang_shadow", _p(tmp_path, id=sid, idx=0, heard=" ")))


def test_end_needs_practice_and_lang_code_default(tmp_path, monkeypatch):
    meta = learning_store.create(tmp_path, "x", "language", "xx")
    assert meta["lang"] == {"code": "en"} and meta["phase"] == "lang"
    with pytest.raises(ValueError, match="还没开口练"):
        asyncio.run(learning.handle("learn/lang_end", _p(tmp_path, id=meta["id"])))
    with pytest.raises(ValueError, match="情景对话"):
        asyncio.run(learning.handle("learn/lang_start", _p(tmp_path, id=meta["id"], activity="sing")))
