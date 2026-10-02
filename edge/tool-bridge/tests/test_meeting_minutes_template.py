"""会议纪要自定义模版 (10/3)。大模型调用用假的 call_llm 替掉。"""
from __future__ import annotations

import asyncio
import json

import pytest

from catfish_tool_bridge import meeting_minutes as mm
from catfish_tool_bridge import meeting_minutes_template as mt
from tests.test_meeting_minutes import REPLY, SEGS, _fake_llm, _meeting

TEMPLATE = {
    "id": "tpl_1",
    "name": "党委会纪要",
    "body": "# {{会议标题}}\n时间: {{日期}}  参会: {{参会人}} ({{参会人数}} 人, {{时长}})\n"
            "## 一、议题 (列出讨论的议题)\n## 二、决定事项\n| 事项 | 负责人 | 时限 |\n|---|---|---|\n{{未知}}",
}


def test_default_path_unchanged_one_call(tmp_path, monkeypatch):
    d = _meeting(tmp_path, SEGS)
    calls = _fake_llm(monkeypatch, [REPLY])
    doc = asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t"))
    assert len(calls) == 1 and "template" not in doc
    assert "## 摘要" in (d / "minutes.md").read_text(encoding="utf-8")


def test_template_second_call_gets_filled_template_and_structured_result(tmp_path, monkeypatch):
    d = _meeting(tmp_path, SEGS, {"0": "陈鸿波", "1": "林达华"})
    out = "```markdown\n# 资质集采周会\n## 一、议题\n- 资质集采二期\n```"
    calls = _fake_llm(monkeypatch, [REPLY, out])
    doc = asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t", template=TEMPLATE))

    assert len(calls) == 2
    sent = calls[1][1]["content"]
    # 占位符发出去前就换好了; 不认识的占位符原样留着
    assert "# 资质集采周会" in sent and "时间: 2026-10-01" in sent
    assert "参会: 林达华、陈鸿波 (3 人, 1 分钟)" in sent and "{{未知}}" in sent
    assert '"decisions": ["d"]' in sent, "结构化结果要给模型参照 (负责人 / 截止以它为准)"
    assert "我们先过一下资质集采" in sent, "短会议给转写原文"

    md = (d / "minutes.md").read_text(encoding="utf-8")
    assert md.startswith("# 资质集采周会\n## 一、议题"), "代码块外壳要剥掉"
    assert "按纪要模版「党委会纪要」" in md
    saved = json.loads((d / "minutes.json").read_text(encoding="utf-8"))
    assert saved["template"] == {"id": "tpl_1", "name": "党委会纪要"}
    assert saved["decisions"] == ["d"], "结构化结果照常存, 待办「加入任务库」要用"
    assert doc["template"]["name"] == "党委会纪要"


def test_long_meeting_template_uses_partials_not_raw_transcript(tmp_path, monkeypatch):
    long_segs = [{"spk": i % 2, "start": i * 10.0, "end": i * 10.0 + 9, "text": "内容" * 400} for i in range(40)]
    d = _meeting(tmp_path, long_segs)
    chunks = len(mm.chunk_lines(mm.transcript_lines({"segments": long_segs}, {})))
    calls = _fake_llm(monkeypatch, [REPLY] * (chunks + 1) + ["# 纪要"])
    asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t", template=TEMPLATE))
    assert len(calls) == chunks + 2
    assert "第 1 段要点" in calls[-1][1]["content"]
    assert "内容内容内容" not in calls[-1][1]["content"]


def test_empty_template_output_is_an_error(tmp_path, monkeypatch):
    d = _meeting(tmp_path, SEGS)
    _fake_llm(monkeypatch, [REPLY, "  "])
    with pytest.raises(ValueError, match="没写出内容"):
        asyncio.run(mm.summarize(d, gateway_url="http://gw.test", auth_token="t", template=TEMPLATE))


def test_validate_template():
    assert mt.validate_template(None) is None
    assert mt.validate_template({}) is None
    assert mt.validate_template({"id": "tpl_1", "body": " x "})["body"] == "x"
    with pytest.raises(ValueError, match="空"):
        mt.validate_template({"id": "tpl_1", "body": "  "})
    with pytest.raises(ValueError, match="太长"):
        mt.validate_template({"body": "x" * (mt.MAX_TEMPLATE_CHARS + 1)})


def test_handle_summarize_passes_template(tmp_path, monkeypatch):
    _meeting(tmp_path, SEGS)
    calls = _fake_llm(monkeypatch, [REPLY, "# ok"])
    doc = asyncio.run(mm.handle_summarize({
        "catfish_home": str(tmp_path), "gateway_url": "http://gw.test", "auth_token": "t",
        "meeting_id": "mtg_20261001-100000_ab12", "template": TEMPLATE,
    }))
    assert len(calls) == 2 and doc["template"]["id"] == "tpl_1"
