"""BL-MM13 (5/8) — propose_skill_revision (改进老 skill) 单测.

覆盖:
  - 校验: skill_path / SemVer / reason 长度 / diff_summary / evidence_summary
  - SemVer: 必须 X.Y.Z, proposed > current
  - 红线字段拒 (跟 MM9 一致, 复用 _is_redline_skill_name)
  - 限流: 24h 内同 skill_path 不重复 propose
  - 限流: 1 小时内最多 3 个 revision propose (比 MM9 严格, 一次别改太多)
  - 落盘 jsonl 格式正确 (proposed event)
  - 多次 propose 返 total_revisions 累加
  - dispatch_native 路由
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools


@pytest.fixture
def revisions_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个 test 独立 ~/.catfish/skill_revisions.jsonl"""
    p = tmp_path / "skill_revisions.jsonl"
    monkeypatch.setattr(catfish_tools, "SKILL_REVISIONS_PATH", p)
    return p


# 共用 valid args 模板, test 改一两个字段试不同断言
def _valid_args() -> dict:
    return {
        "skill_path": "department/weekly-report",
        "current_version": "0.3.2",
        "proposed_version": "0.4.0",
        "reason": "员工 5/8 5/9 5/10 三次 BL-MM11 给 👎, 评论 '太啰嗦'. audit 4 次 timeout 没 catch.",
        "diff_summary": "- SKILL.md: 删第 3 段冗余\n- script.py: 加 try/except 兜 InvalidArgument",
        "evidence_summary": "BL-MM11: 5 个 👎. audit: 12 次调用 4 次失败 (33%). BL-MM12 score: 35.",
    }


# ============================================================
# Validation 必填 + 格式
# ============================================================


def test_empty_skill_path_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["skill_path"] = ""
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "skill_path" in out["error"]


def test_skill_path_must_have_namespace(revisions_file: Path) -> None:
    """skill_path 必须含 / (例 'department/weekly-report'), 不能是单一 token"""
    args = _valid_args()
    args["skill_path"] = "weekly-report"  # 没 namespace
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "kebab-case" in out["error"] or "/" in out["error"]


def test_skill_path_camelcase_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["skill_path"] = "Department/WeeklyReport"
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"


def test_current_version_not_semver_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["current_version"] = "v0.3"
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "SemVer" in out["error"]


def test_proposed_version_not_semver_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["proposed_version"] = "0.4-dev"
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"


def test_proposed_must_be_greater_than_current(revisions_file: Path) -> None:
    args = _valid_args()
    args["current_version"] = "0.4.0"
    args["proposed_version"] = "0.3.5"  # 小于 current
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "必须 >" in out["error"]


def test_proposed_equal_to_current_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["proposed_version"] = "0.3.2"  # 跟 current 一样
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"


def test_reason_too_short_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["reason"] = "员工不喜欢"  # < 30 字
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "reason" in out["error"]


def test_diff_summary_too_short_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["diff_summary"] = "改一改"
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "diff_summary" in out["error"]


def test_evidence_summary_too_short_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["evidence_summary"] = "数据"
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "evidence_summary" in out["error"]


# ============================================================
# 红线 (跟 MM7/MM9 一致 — 健康 / 财务 / 感情 / 政治 / 宗教)
# ============================================================


def test_redline_health_path_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["skill_path"] = "personal/health-tracker"
    args["reason"] = "员工反复让我估算 medical drug 剂量, 改进药品库表"  # 含 medical
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "红线" in out["error"]


def test_redline_finance_path_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    args["skill_path"] = "personal/finance-loan-calc"
    args["reason"] = (
        "员工反复 finance 算 loan 利率, 改 debt structure 计算公式增加准确度"
    )
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "红线" in out["error"]


# ============================================================
# 限流: 同 skill_path 24h 内不重复
# ============================================================


def test_recent_same_skill_path_rejected(revisions_file: Path) -> None:
    args = _valid_args()
    first = catfish_tools.propose_skill_revision(args)
    assert first["type"] == "ok"
    second = catfish_tools.propose_skill_revision(args)
    assert second["type"] == "error"
    assert "24h" in second["error"]


def test_after_24h_can_propose_again(revisions_file: Path) -> None:
    """24h 之前 propose 过的, 现在可以再 propose"""
    args = _valid_args()
    first = catfish_tools.propose_skill_revision(args)
    assert first["type"] == "ok"

    # 把 jsonl 里的 ts 向前推 25 小时
    lines = revisions_file.read_text(encoding="utf-8").splitlines()
    new_lines = []
    for line in lines:
        rec = json.loads(line)
        rec["ts"] = rec["ts"] - 25 * 3600
        new_lines.append(json.dumps(rec, ensure_ascii=False))
    revisions_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    args["proposed_version"] = "0.4.1"  # bump 一下
    second = catfish_tools.propose_skill_revision(args)
    assert second["type"] == "ok"


# ============================================================
# 限流: 单 session 1h 内最多 3 个
# ============================================================


def test_session_limit_3_rejected(revisions_file: Path) -> None:
    """propose 3 个不同 skill 之后, 第 4 个被限流"""
    for i in range(3):
        args = _valid_args()
        args["skill_path"] = f"department/skill-{i}"
        out = catfish_tools.propose_skill_revision(args)
        assert out["type"] == "ok"
    # 第 4 个
    args = _valid_args()
    args["skill_path"] = "department/skill-3"
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "error"
    assert "上限" in out["error"]


# ============================================================
# 落盘 + summary
# ============================================================


def test_successful_propose_writes_jsonl(revisions_file: Path) -> None:
    args = _valid_args()
    out = catfish_tools.propose_skill_revision(args)
    assert out["type"] == "ok"
    assert out["revision_id"].startswith("rev_")
    assert out["skill_path"] == "department/weekly-report"
    assert out["current_version"] == "0.3.2"
    assert out["proposed_version"] == "0.4.0"
    assert "summary" in out

    # jsonl 里能找到对应记录
    lines = revisions_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event_type"] == "proposed"
    assert rec["skill_path"] == "department/weekly-report"
    assert rec["status"] == "proposed"
    assert rec["current_version"] == "0.3.2"
    assert rec["proposed_version"] == "0.4.0"
    assert "ts" in rec
    assert "ts_iso" in rec


def test_total_revisions_increments(revisions_file: Path) -> None:
    """多次成功 propose, total_revisions 计数递增"""
    for i in range(3):
        args = _valid_args()
        args["skill_path"] = f"department/skill-{i}"
        out = catfish_tools.propose_skill_revision(args)
        assert out["type"] == "ok"
        assert out["total_revisions"] == i + 1


# ============================================================
# dispatch_native 路由 (确保工具在 dispatch 表里 + tool def 在 CATFISH_NATIVE_TOOLS)
# ============================================================


def test_propose_revision_in_native_tools(revisions_file: Path) -> None:
    names = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert "catfish_propose_skill_revision" in names


def test_propose_revision_required_fields(revisions_file: Path) -> None:
    """6 个字段都必填"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_propose_skill_revision"
    )
    required = set(tool["input_schema"]["required"])
    assert required == {
        "skill_path",
        "current_version",
        "proposed_version",
        "reason",
        "diff_summary",
        "evidence_summary",
    }


def test_dispatch_routes_to_propose_revision(revisions_file: Path) -> None:
    """dispatch_native('catfish_propose_skill_revision', args) 调到 propose_skill_revision"""
    out = catfish_tools.dispatch_native("catfish_propose_skill_revision", _valid_args())
    assert out["type"] == "ok"
    assert out["revision_id"].startswith("rev_")


def test_dispatch_unknown_tool_raises() -> None:
    """dispatch_native 对未知 tool name 抛 ValueError (现有约定)"""
    with pytest.raises(ValueError, match="unknown native tool"):
        catfish_tools.dispatch_native("catfish_propose_skill_revision_typo", {})
