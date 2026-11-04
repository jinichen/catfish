"""11/4: 「整理记忆」精简建议 —— 只留安全的改法, 存成待确认, 员工批了才改。"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge import memory_compact as mc

LONG_A = "偏好: 对外文书一律写正式口径, 不用口语, 不用网络用语, 也不要用感叹号, 数字都用阿拉伯数字, 单位写全称, 日期写成某年某月某日的格式。外发之前先给我过目, 涉及客户名称和金额的地方要脱敏处理。"
LONG_B = "偏好: 对外的文书要正式, 不要口语化的表达, 数字统一阿拉伯数字。" * 2
SHORT = "叫我鸿波"


def test_rewrite_must_really_shorten():
    ops = mc.validate_ops([
        {"type": "rewrite", "index": 0, "content": LONG_A[:-5]},            # 只省 5 字 → 不提
        {"type": "rewrite", "index": 1, "content": "对外文书写正式, 数字用阿拉伯数字"},
    ], [LONG_A, LONG_B])
    assert [o["originals"] for o in ops] == [[LONG_B]]
    assert ops[0]["saved"] == len(LONG_B) - len("对外文书写正式, 数字用阿拉伯数字")


def test_bad_ops_are_dropped():
    entries = [LONG_A, LONG_B, SHORT]
    ops = mc.validate_ops([
        {"type": "rewrite", "index": 9, "content": "x"},                     # 编号越界
        {"type": "rewrite", "index": True, "content": "x"},                  # bool 不算编号
        {"type": "merge", "indices": [0], "content": "x"},                   # 合并要两条以上
        {"type": "rewrite", "index": 0, "content": "a § b"},                 # 含分隔符会拆坏文件
        {"type": "rewrite", "index": 0, "content": "  "},                    # 空
        {"type": "delete", "index": 0},                                      # 不认的改法 (不许模型直接删)
        {"type": "merge", "indices": [0, 1], "content": "对外文书写正式口径, 数字用阿拉伯数字"},
        {"type": "rewrite", "index": 1, "content": "已经合并过的那条"},       # 跟上一条重叠
    ], entries)
    assert len(ops) == 1 and ops[0]["type"] == "merge" and ops[0]["originals"] == [LONG_A, LONG_B]


def test_parse_tolerates_fences_and_chatter():
    text = '好的:\n```json\n{"ops":[{"type":"rewrite","index":0,"content":"短"}]}\n```'
    assert mc._parse(text) == [{"type": "rewrite", "index": 0, "content": "短"}]
    assert mc._parse("不是 JSON") == []


def test_merge_payload_pins_every_original():
    p = mc._payload("memory", {"originals": [LONG_A, LONG_B], "content": "合并"})
    assert p["action"] == "batch" and p["target"] == "memory"
    assert [(o["action"], o["matched_entry"]) for o in p["operations"]] == [("replace", LONG_A), ("remove", LONG_B)]
    single = mc._payload("user", {"originals": [LONG_A], "content": "短"})
    assert single == {"target": "user", "action": "replace", "old_text": LONG_A, "matched_entry": LONG_A, "content": "短"}


# ── 跟 hermes 真函数走一遍 (没装 hermes-agent 就跳过) ──────────────


@pytest.fixture
def hhome(tmp_path, monkeypatch):
    pytest.importorskip("tools.write_approval")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir()
    (tmp_path / "config.yaml").write_text("memory:\n  memory_char_limit: 4000\n  user_char_limit: 2000\n", encoding="utf-8")
    (tmp_path / "memories" / "MEMORY.md").write_text("\n§\n".join([LONG_A, LONG_B, SHORT]), encoding="utf-8")
    return tmp_path


def _entries(home):
    return [e.strip() for e in (home / "memories" / "MEMORY.md").read_text(encoding="utf-8").split("\n§\n")]


def test_propose_stages_then_approve_applies(hhome, monkeypatch):
    from catfish_tool_bridge import memory_pending

    reply = {"ops": [{"type": "merge", "indices": [0, 1], "content": "对外文书写正式口径, 数字用阿拉伯数字"}]}
    monkeypatch.setattr(mc, "_llm", lambda _p: json.dumps(reply, ensure_ascii=False))
    r = mc.propose({"target": "memory"})
    assert r["ok"] and r["staged"] == 1 and r["char_limit"] == 4000
    assert _entries(hhome) == [LONG_A, LONG_B, SHORT]  # 只提建议, 没改

    (item,) = memory_pending.list_pending()["items"]
    assert item["origin"] == mc.ORIGIN and not item["stale"]
    assert memory_pending.resolve({"id": item["id"], "approve": True})["applied"] is True
    assert _entries(hhome) == ["对外文书写正式口径, 数字用阿拉伯数字", SHORT]


def test_second_run_replaces_unapproved_batch(hhome, monkeypatch):
    from catfish_tool_bridge import memory_pending

    reply = {"ops": [{"type": "rewrite", "index": 0, "content": "对外文书写正式"}]}
    monkeypatch.setattr(mc, "_llm", lambda _p: json.dumps(reply, ensure_ascii=False))
    mc.propose({"target": "memory"})
    mc.propose({"target": "memory"})
    assert len(memory_pending.list_pending()["items"]) == 1


def test_model_unreachable_is_reported(hhome, monkeypatch):
    monkeypatch.setattr(mc, "_llm", lambda _p: "")
    r = mc.propose({"target": "memory"})
    assert r["ok"] is False and "连不上" in r["error"]


def test_bad_target_rejected():
    with pytest.raises(ValueError):
        mc.propose({"target": "../x"})
