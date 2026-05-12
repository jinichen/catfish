"""expertise.py 单元测试 (BL-FED2.1, 5/12 鸿波拍板自动从 journal 抽).

mock LLM 调用, 测:
- yaml dump/load 往返
- LLM 抽取 + 边界 (空 / 太长 / 低 confidence / 重复 / 非 JSON)
- merge 规则 (confirmed / rejected 保留, pending 替换, rejected 不被新抽覆盖)
- 3 个 tool entry
- export_for_registry 隐私边界 (只 confirmed tag 字符串, 不传 confidence/evidence)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_yaml(tmp_path, monkeypatch):
    from catfish_tool_bridge import expertise
    fake_path = tmp_path / "home" / ".catfish" / "expertise.yaml"
    monkeypatch.setattr(expertise, "EXPERTISE_PATH", fake_path)
    yield fake_path


def _sample_journal() -> str:
    return """
## 2026-05-01 鸿波本周工作

合规: AI 预判平台 6 到位审核, ISO27001 现场审核准备
资质: 业务连续性五星 + 商品售后服务评价体系 2 项资质
项目立项: 数字政务平台立项申请 (4 段公文)

## 2026-05-08 鸿波本周工作

合规: 续 ISO27001 审核 + CSMM 审核预备
资质: 27000 审核准备, 跟金智达沟通员工资质
立项: 中电信数政蔡旭东总经理调研, 准备项目方案
"""


def _mock_llm_returning(text: str):
    """返回固定文本的 mock LLM."""
    def _call(prompt: str) -> str:
        return text
    return _call


# ─── yaml dump / load 往返 ────────────────────────────────────────────


def test_yaml_dump_load_roundtrip():
    from catfish_tool_bridge import expertise
    data = {
        "extracted_at": "2026-05-12T20:00:00+0800",
        "source": "employee_journal.md",
        "auto_review_pending": True,
        "tags": [
            {"tag": "资质管理", "confidence": 0.92, "evidence_count": 27,
             "aliases": ["资质", "证书"], "status": "pending", "last_reviewed_at": None},
            {"tag": "合规审核", "confidence": 0.85, "evidence_count": 18,
             "aliases": [], "status": "confirmed", "last_reviewed_at": "2026-05-12T20:30:00+0800"},
        ],
    }
    yaml_text = expertise._yaml_dump(data)
    loaded = expertise._yaml_load(yaml_text)
    assert loaded["source"] == data["source"]
    assert loaded["auto_review_pending"] is True
    assert len(loaded["tags"]) == 2
    assert loaded["tags"][0]["tag"] == "资质管理"
    assert loaded["tags"][0]["confidence"] == 0.92
    assert loaded["tags"][0]["aliases"] == ["资质", "证书"]
    assert loaded["tags"][1]["status"] == "confirmed"


def test_load_missing_file_returns_default():
    from catfish_tool_bridge import expertise
    data = expertise.load_expertise()
    assert data["tags"] == []
    assert data["auto_review_pending"] is True


def test_save_load_persistence():
    from catfish_tool_bridge import expertise
    data = {
        "extracted_at": "2026-05-12T20:00:00+0800",
        "source": "test",
        "auto_review_pending": False,
        "tags": [{"tag": "x", "confidence": 0.5, "evidence_count": 1, "aliases": [],
                  "status": "confirmed", "last_reviewed_at": None}],
    }
    assert expertise.save_expertise(data) is True
    reloaded = expertise.load_expertise()
    assert reloaded["tags"][0]["tag"] == "x"
    assert reloaded["auto_review_pending"] is False


# ─── LLM 抽取边界 ────────────────────────────────────────────────────


def test_extract_empty_journal_returns_empty():
    from catfish_tool_bridge import expertise
    assert expertise.extract_from_journal("", _mock_llm_returning("anything")) == []
    assert expertise.extract_from_journal("   ", _mock_llm_returning("anything")) == []


def test_extract_normal_case():
    from catfish_tool_bridge import expertise
    llm_resp = json.dumps({
        "tags": [
            {"tag": "资质管理", "confidence": 0.92, "evidence_count": 27, "aliases": ["资质"]},
            {"tag": "合规审核", "confidence": 0.85, "evidence_count": 18, "aliases": []},
        ]
    })
    tags = expertise.extract_from_journal(_sample_journal(), _mock_llm_returning(llm_resp))
    assert len(tags) == 2
    assert tags[0]["tag"] == "资质管理"
    assert tags[0]["confidence"] == 0.92
    assert tags[0]["status"] == "pending"  # 抽出来默认 pending


def test_extract_strips_markdown_fence():
    """LLM 偶尔会带 ```json wrapper, 必须能解."""
    from catfish_tool_bridge import expertise
    llm_resp = '```json\n{"tags": [{"tag": "xx", "confidence": 0.8, "evidence_count": 5}]}\n```'
    tags = expertise.extract_from_journal("some journal", _mock_llm_returning(llm_resp))
    assert len(tags) == 1
    assert tags[0]["tag"] == "xx"


def test_extract_low_confidence_skipped():
    from catfish_tool_bridge import expertise
    llm_resp = json.dumps({
        "tags": [
            {"tag": "高频", "confidence": 0.9, "evidence_count": 20},
            {"tag": "低频", "confidence": 0.2, "evidence_count": 1},  # < 0.4 跳过
        ]
    })
    tags = expertise.extract_from_journal("j", _mock_llm_returning(llm_resp))
    assert len(tags) == 1
    assert tags[0]["tag"] == "高频"


def test_extract_invalid_tag_length_skipped():
    from catfish_tool_bridge import expertise
    llm_resp = json.dumps({
        "tags": [
            {"tag": "X", "confidence": 0.9, "evidence_count": 5},  # 太短 < 2
            {"tag": "正常 tag", "confidence": 0.9, "evidence_count": 5},
            {"tag": "超长" * 30, "confidence": 0.9, "evidence_count": 5},  # 太长
        ]
    })
    tags = expertise.extract_from_journal("j", _mock_llm_returning(llm_resp))
    assert len(tags) == 1
    assert tags[0]["tag"] == "正常 tag"


def test_extract_dedup_same_tag():
    from catfish_tool_bridge import expertise
    llm_resp = json.dumps({
        "tags": [
            {"tag": "资质", "confidence": 0.9, "evidence_count": 5},
            {"tag": "资质", "confidence": 0.7, "evidence_count": 3},  # 重复, 第二个跳过
        ]
    })
    tags = expertise.extract_from_journal("j", _mock_llm_returning(llm_resp))
    assert len(tags) == 1


def test_extract_invalid_json_returns_empty():
    from catfish_tool_bridge import expertise
    tags = expertise.extract_from_journal("j", _mock_llm_returning("不是 JSON 是文字"))
    assert tags == []


def test_extract_llm_raises_returns_empty():
    from catfish_tool_bridge import expertise
    def _raise(p):
        raise RuntimeError("LLM 端点挂了")
    assert expertise.extract_from_journal("j", _raise) == []


# ─── merge 规则 (核心 — 员工 review 不被覆盖) ────────────────────────


def test_merge_confirmed_status_preserved():
    """已 confirmed 的 tag 即使新抽到, status 也保留, 但 confidence/evidence 更新."""
    from catfish_tool_bridge import expertise
    existing = [
        {"tag": "资质", "confidence": 0.85, "evidence_count": 15,
         "status": "confirmed", "last_reviewed_at": "2026-05-10"},
    ]
    new = [
        {"tag": "资质", "confidence": 0.95, "evidence_count": 30,
         "aliases": [], "status": "pending", "last_reviewed_at": None},
    ]
    merged = expertise.merge_with_existing(new, existing)
    assert len(merged) == 1
    assert merged[0]["status"] == "confirmed"  # 保留
    assert merged[0]["confidence"] == 0.95  # 更新
    assert merged[0]["evidence_count"] == 30
    assert merged[0]["last_reviewed_at"] == "2026-05-10"  # review 时间保留


def test_merge_rejected_persists_even_if_not_reextracted():
    """已 rejected 的 tag 即使新抽没抽到, 也保留 (员工说不要 = 不要)."""
    from catfish_tool_bridge import expertise
    existing = [
        {"tag": "拒绝项", "confidence": 0.8, "evidence_count": 5,
         "status": "rejected", "last_reviewed_at": "2026-05-11"},
    ]
    new = [
        {"tag": "新 tag", "confidence": 0.9, "evidence_count": 10,
         "status": "pending", "aliases": [], "last_reviewed_at": None},
    ]
    merged = expertise.merge_with_existing(new, existing)
    assert len(merged) == 2
    rejected_tags = [t for t in merged if t["status"] == "rejected"]
    assert len(rejected_tags) == 1
    assert rejected_tags[0]["tag"] == "拒绝项"


def test_merge_pending_overwritten():
    """pending 的 tag 直接被新数据替换, 不保留."""
    from catfish_tool_bridge import expertise
    existing = [
        {"tag": "x", "confidence": 0.5, "evidence_count": 3,
         "status": "pending", "last_reviewed_at": None},
    ]
    new = [
        {"tag": "x", "confidence": 0.9, "evidence_count": 20,
         "aliases": [], "status": "pending", "last_reviewed_at": None},
    ]
    merged = expertise.merge_with_existing(new, existing)
    assert len(merged) == 1
    assert merged[0]["confidence"] == 0.9


def test_merge_case_insensitive():
    """tag 名大小写不敏感."""
    from catfish_tool_bridge import expertise
    existing = [{"tag": "RBAC", "confidence": 0.8, "evidence_count": 5,
                 "status": "confirmed", "last_reviewed_at": None}]
    new = [{"tag": "rbac", "confidence": 0.9, "evidence_count": 10,
            "status": "pending", "aliases": [], "last_reviewed_at": None}]
    merged = expertise.merge_with_existing(new, existing)
    assert len(merged) == 1
    assert merged[0]["status"] == "confirmed"


# ─── tool entries ────────────────────────────────────────────────────


def test_tool_extract_expertise_full_flow():
    from catfish_tool_bridge import expertise
    llm = _mock_llm_returning(json.dumps({
        "tags": [
            {"tag": "资质管理", "confidence": 0.92, "evidence_count": 27},
            {"tag": "合规", "confidence": 0.85, "evidence_count": 18},
        ]
    }))
    r = expertise.tool_extract_expertise({}, llm, journal_text=_sample_journal())
    assert r["ok"] is True
    assert r["extracted_count"] == 2
    assert r["pending_review_count"] == 2
    # 文件真写了
    data = expertise.load_expertise()
    assert len(data["tags"]) == 2


def test_tool_extract_empty_journal():
    from catfish_tool_bridge import expertise
    r = expertise.tool_extract_expertise({}, _mock_llm_returning(""), journal_text="")
    assert r["ok"] is False
    assert "journal 为空" in r["error"]


def test_tool_list_expertise_with_filter():
    from catfish_tool_bridge import expertise
    expertise.save_expertise({
        "extracted_at": "x", "source": "y", "auto_review_pending": False,
        "tags": [
            {"tag": "a", "status": "confirmed", "confidence": 0.9, "evidence_count": 5,
             "aliases": [], "last_reviewed_at": None},
            {"tag": "b", "status": "pending", "confidence": 0.7, "evidence_count": 3,
             "aliases": [], "last_reviewed_at": None},
            {"tag": "c", "status": "rejected", "confidence": 0.5, "evidence_count": 2,
             "aliases": [], "last_reviewed_at": None},
        ],
    })
    r = expertise.tool_list_expertise({"status_filter": "confirmed"})
    assert r["count"] == 1
    assert r["tags"][0]["tag"] == "a"
    r2 = expertise.tool_list_expertise({})  # all
    assert r2["count"] == 3


def test_tool_confirm_expertise_update_status():
    from catfish_tool_bridge import expertise
    expertise.save_expertise({
        "extracted_at": "", "source": "", "auto_review_pending": True,
        "tags": [{"tag": "x", "status": "pending", "confidence": 0.8, "evidence_count": 5,
                  "aliases": [], "last_reviewed_at": None}],
    })
    r = expertise.tool_confirm_expertise({"tag": "x", "status": "confirmed"})
    assert r["ok"] is True
    data = expertise.load_expertise()
    assert data["tags"][0]["status"] == "confirmed"
    assert data["tags"][0]["last_reviewed_at"] is not None


def test_tool_confirm_unknown_tag():
    from catfish_tool_bridge import expertise
    r = expertise.tool_confirm_expertise({"tag": "不存在的 tag", "status": "confirmed"})
    assert r["ok"] is False
    assert "找不到" in r["error"]


def test_tool_confirm_invalid_status():
    from catfish_tool_bridge import expertise
    expertise.save_expertise({
        "extracted_at": "", "source": "", "auto_review_pending": True,
        "tags": [{"tag": "x", "status": "pending", "confidence": 0.8, "evidence_count": 5,
                  "aliases": [], "last_reviewed_at": None}],
    })
    r = expertise.tool_confirm_expertise({"tag": "x", "status": "evil"})
    assert r["ok"] is False


def test_tool_confirm_rename_and_add_aliases():
    from catfish_tool_bridge import expertise
    expertise.save_expertise({
        "extracted_at": "", "source": "", "auto_review_pending": True,
        "tags": [{"tag": "资质", "status": "pending", "confidence": 0.8, "evidence_count": 5,
                  "aliases": ["证书"], "last_reviewed_at": None}],
    })
    r = expertise.tool_confirm_expertise({
        "tag": "资质",
        "new_tag": "资质管理",
        "add_aliases": ["认证", "ISO"],
    })
    assert r["ok"] is True
    data = expertise.load_expertise()
    assert data["tags"][0]["tag"] == "资质管理"
    assert "认证" in data["tags"][0]["aliases"]
    assert "ISO" in data["tags"][0]["aliases"]
    assert "证书" in data["tags"][0]["aliases"]  # 老 alias 保留


# ─── export_for_registry — 隐私边界关键 ──────────────────────────────


def test_export_for_registry_only_confirmed():
    """只返 status=confirmed 的 tag, pending / rejected 不导出."""
    from catfish_tool_bridge import expertise
    expertise.save_expertise({
        "extracted_at": "", "source": "", "auto_review_pending": False,
        "tags": [
            {"tag": "资质", "status": "confirmed", "confidence": 0.9,
             "evidence_count": 20, "aliases": [], "last_reviewed_at": None},
            {"tag": "合规", "status": "pending", "confidence": 0.8,
             "evidence_count": 10, "aliases": [], "last_reviewed_at": None},
            {"tag": "拒绝", "status": "rejected", "confidence": 0.7,
             "evidence_count": 5, "aliases": [], "last_reviewed_at": None},
        ],
    })
    out = expertise.export_for_registry()
    assert out == ["资质"]


def test_export_for_registry_no_metadata_leak():
    """返的只是 tag 字符串数组, 不包 confidence / evidence / aliases / source."""
    from catfish_tool_bridge import expertise
    expertise.save_expertise({
        "extracted_at": "敏感", "source": "敏感",
        "auto_review_pending": False,
        "tags": [{"tag": "资质", "status": "confirmed", "confidence": 0.99,
                  "evidence_count": 999, "aliases": ["秘密别名"],
                  "last_reviewed_at": "敏感"}],
    })
    out = expertise.export_for_registry()
    assert out == ["资质"]
    # 类型检查: list[str], 不是 list[dict]
    assert all(isinstance(t, str) for t in out)


def test_export_empty_when_no_confirmed():
    """没 confirmed → 空列表 → 黄页不展示."""
    from catfish_tool_bridge import expertise
    expertise.save_expertise({
        "extracted_at": "", "source": "", "auto_review_pending": True,
        "tags": [{"tag": "x", "status": "pending", "confidence": 0.8,
                  "evidence_count": 5, "aliases": [], "last_reviewed_at": None}],
    })
    assert expertise.export_for_registry() == []
