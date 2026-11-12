"""会议纪要: 改 / 导出 Word / 准备发到群 (meeting_minutes_edit.py, 11/12)。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_tool_bridge import meeting_minutes_edit as me

MID = "mtg_20261112-174100_ab12"


def _meeting(tmp_path: Path, *, template: dict | None = None, output_file: str | None = None) -> Path:
    d = tmp_path / "meetings" / MID
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": MID, "title": "CC RC末次", "created_at": "2026-11-12T17:41:00+08:00",
                                             "attendees": 12}), encoding="utf-8")
    (d / "transcript.json").write_text(json.dumps({"segments": [{"spk": 0, "start": 0, "end": 1, "text": "开始"}]}), encoding="utf-8")
    (d / "speakers.json").write_text(json.dumps({"0": "陈鸿波"}), encoding="utf-8")
    doc = {"version": 1, "meeting_id": MID, "summary": "审核方指出工具测试记录不符合项。", "decisions": ["确认存在严重不符合项"],
           "action_items": [{"owner": "", "task": "补充工具测试记录", "due": ""}], "open_questions": [], "carried_over": [],
           "references": {"wiki": [{"title": "CCRC", "rel_path": "wiki/x.md"}], "previous_meeting": None}}
    if template:
        doc["template"] = template
    if output_file:
        doc["output_file"] = output_file
    (d / "minutes.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (d / "minutes.md").write_text("# CC RC末次\n\n原来的纪要\n", encoding="utf-8")
    return d


def _p(tmp_path: Path, **kw) -> dict:
    return {"meeting_id": MID, "catfish_home": str(tmp_path), **kw}


def test_save_fields_rerenders_markdown_and_marks_edited(tmp_path):
    d = _meeting(tmp_path)
    r = me.handle_save(_p(tmp_path, fields={
        "summary": "审核方指出工具测试记录、运维监控数据真实性存在严重不符合项。",
        "decisions": ["确认存在严重不符合项", "", "暂停通知前一个月提交恢复申请"],
        "action_items": [{"task": "补充完整工具测试记录", "owner": "张三", "due": "2026-11-30"},
                         {"task": "  ", "owner": "x"}, {"task": "整理监控数据", "due": "下周"}],
    }))
    assert r["ok"] and r["edited_at"]
    doc = json.loads((d / "minutes.json").read_text(encoding="utf-8"))
    assert doc["decisions"] == ["确认存在严重不符合项", "暂停通知前一个月提交恢复申请"]
    assert doc["action_items"] == [{"owner": "张三", "task": "补充完整工具测试记录", "due": "2026-11-30"},
                                   {"owner": "", "task": "整理监控数据", "due": ""}]
    md = (d / "minutes.md").read_text(encoding="utf-8")
    assert "运维监控数据真实性" in md and "- [ ] 补充完整工具测试记录 (张三 · 截止 2026-11-30)" in md
    assert "已人工核对修改" in md and "请核对" not in md
    assert "参会: 陈鸿波" in md and "知识库 「CCRC」" in md


def test_save_markdown_for_text_template(tmp_path):
    d = _meeting(tmp_path, template={"id": "t1", "name": "公司模版"})
    me.handle_save(_p(tmp_path, markdown="# 会议纪要\n\n改过的全文\n", fields={"action_items": [{"task": "新待办"}]}))
    assert (d / "minutes.md").read_text(encoding="utf-8") == "# 会议纪要\n\n改过的全文\n"
    doc = json.loads((d / "minutes.json").read_text(encoding="utf-8"))
    assert doc["action_items"][0]["task"] == "新待办" and doc["edited_at"]


def test_save_template_only_items_keeps_markdown(tmp_path):
    d = _meeting(tmp_path, template={"id": "t1", "name": "公司模版"})
    me.handle_save(_p(tmp_path, fields={"action_items": []}))
    assert (d / "minutes.md").read_text(encoding="utf-8") == "# CC RC末次\n\n原来的纪要\n"


def test_save_rejects_empty_and_nothing(tmp_path):
    _meeting(tmp_path)
    with pytest.raises(ValueError):
        me.handle_save(_p(tmp_path))
    with pytest.raises(ValueError):
        me.handle_save(_p(tmp_path, markdown="   "))


def test_save_rejects_bad_meeting_id(tmp_path):
    with pytest.raises(ValueError):
        me.handle_save({"meeting_id": "../../etc", "catfish_home": str(tmp_path), "markdown": "x"})


def test_docx_from_markdown(tmp_path):
    pytest.importorskip("docx")
    d = _meeting(tmp_path)
    (d / "minutes.md").write_text("# CC RC末次\n\n- 日期: 2026-11-12\n\n## 摘要\n\n审核方指出问题。\n\n## 待办\n\n- [ ] 补充记录\n", encoding="utf-8")
    r = me.handle_docx(_p(tmp_path))
    p = Path(r["path"])
    assert p.parent == d and p.name.startswith("CC RC末次 会议纪要") and p.suffix == ".docx" and r["size"] > 0
    from docx import Document
    text = "\n".join(par.text for par in Document(str(p)).paragraphs)
    assert "CC RC末次 会议纪要" in text and "审核方指出问题。" in text
    assert text.count("CC RC末次") == 1          # 「# 标题」不重复


def test_docx_uses_filled_template_file(tmp_path):
    d = _meeting(tmp_path)
    f = d / "CC RC末次 会议纪要.xlsx"
    f.write_bytes(b"xlsx")
    doc = json.loads((d / "minutes.json").read_text(encoding="utf-8"))
    doc["output_file"] = str(f)
    (d / "minutes.json").write_text(json.dumps(doc), encoding="utf-8")
    assert me.handle_docx(_p(tmp_path))["path"] == str(f)


def test_stage_copies_into_room_staging(tmp_path, monkeypatch):
    pytest.importorskip("docx")
    _meeting(tmp_path)
    from catfish_tool_bridge import colleague_rooms_bigfiles as big
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(big, "_dir", lambda name: staging)
    r = me.handle_stage(_p(tmp_path))
    assert r["name"].endswith(".docx") and (staging / r["stage_id"]).stat().st_size > 0
    assert big._STAGE_ID.fullmatch(r["stage_id"])
