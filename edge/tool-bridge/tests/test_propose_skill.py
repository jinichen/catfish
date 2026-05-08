"""BL-MM9 (5/8) — agent 自动抽 skill propose_skill 单测.

覆盖:
  - 校验: name kebab-case / reason 长度 / action_steps 长度 / evidence_count ≥ 3
  - 红线字段拒绝 (健康 / 财务 / 感情 / 政治 / 宗教)
  - 限流: 24h 内同 name 不重复 propose
  - 限流: 1 小时内最多 5 个 propose
  - 落盘 jsonl 格式正确 (proposed event)
  - 多次 propose 返 total_proposals 累加
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools


@pytest.fixture
def proposals_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个 test 独立 ~/.catfish/skill_proposals.jsonl"""
    p = tmp_path / "skill_proposals.jsonl"
    monkeypatch.setattr(catfish_tools, "SKILL_PROPOSALS_PATH", p)
    return p


# ============================================================
# Validation
# ============================================================


def test_empty_name_rejected(proposals_file: Path) -> None:
    out = catfish_tools.propose_skill({
        "name": "",
        "reason": "员工本周 3 次让我做这件事, 模式相同 (背景/目标/团队/预算)",
        "action_steps": "1. 读员工背景\n2. 拉历史样本\n3. 拼模板\n4. 输出 docx",
        "evidence_count": 3,
    })
    assert out["type"] == "error"
    assert "name" in out["error"]


def test_bad_kebab_name_rejected(proposals_file: Path) -> None:
    """name 必须 kebab-case (小写 + 数字 + 连字符), 不能 CamelCase / 含空格 / 含非 ASCII"""
    bad_names = ["MyProject", "my project", "my_project", "MY-PROJECT", "-leading-dash", "中文-skill"]
    for n in bad_names:
        out = catfish_tools.propose_skill({
            "name": n,
            "reason": "员工 3 次重复, 已观察确认 pattern",
            "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
            "evidence_count": 3,
        })
        assert out["type"] == "error", f"name {n!r} 没被拒"
        assert "kebab" in out["error"]


def test_good_kebab_name_accepted(proposals_file: Path) -> None:
    out = catfish_tools.propose_skill({
        "name": "project-proposal",
        "reason": "员工本周 3 次写立项材料, 结构相似 (背景目标团队预算)",
        "action_steps": "1. 读员工背景\n2. 拉历史样本\n3. 拼模板\n4. 输出 docx 到 ~/.catfish/output/",
        "evidence_count": 3,
    })
    assert out["type"] == "ok"
    assert out["name"] == "project-proposal"


def test_short_reason_rejected(proposals_file: Path) -> None:
    out = catfish_tools.propose_skill({
        "name": "x",
        "reason": "短",  # < 10 字
        "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
        "evidence_count": 3,
    })
    assert out["type"] == "error"


def test_short_action_steps_rejected(proposals_file: Path) -> None:
    out = catfish_tools.propose_skill({
        "name": "good-name",
        "reason": "员工 3 次重复同 pattern, 我观察到了",
        "action_steps": "短",  # < 20 字
        "evidence_count": 3,
    })
    assert out["type"] == "error"
    assert "action_steps" in out["error"]


def test_evidence_count_below_3_rejected(proposals_file: Path) -> None:
    out = catfish_tools.propose_skill({
        "name": "good-name",
        "reason": "员工 1 次让我做, 我冲动想 propose",
        "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
        "evidence_count": 1,
    })
    assert out["type"] == "error"
    assert "3" in out["error"]


# ============================================================
# 红线字段拒绝
# ============================================================


@pytest.mark.parametrize("redline_kw", [
    "health-tracking",
    "salary-calc",
    "love-letter-writer",
    "political-stance",
    "religion-prayer-time",
    "finance-planner",
])
def test_redline_skill_name_rejected(redline_kw: str, proposals_file: Path) -> None:
    out = catfish_tools.propose_skill({
        "name": redline_kw,
        "reason": "员工 3 次让我做相关事, 我观察到了 pattern",
        "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
        "evidence_count": 3,
    })
    assert out["type"] == "error"
    assert "红线" in out["error"]


def test_redline_in_reason_rejected(proposals_file: Path) -> None:
    """name 看起来无害, 但 reason 里有红线词也要拒"""
    out = catfish_tools.propose_skill({
        "name": "personal-helper",
        "reason": "员工 3 次让我帮算贷款利息, 看来是个 pattern",
        "action_steps": "1. 读金额\n2. 套公式\n3. 算利息\n4. 输出表格",
        "evidence_count": 3,
    })
    assert out["type"] == "error"
    assert "红线" in out["error"]


# ============================================================
# 限流: 同 name 24h 内不重复
# ============================================================


def test_same_name_within_24h_rejected(proposals_file: Path) -> None:
    args = {
        "name": "weekly-report",
        "reason": "员工 3 次让我写周报, 拉 audit log + 项目进展拼草稿",
        "action_steps": "1. 拉 audit log\n2. 拉 jira\n3. 拼草稿\n4. 输出 md",
        "evidence_count": 3,
    }
    first = catfish_tools.propose_skill(args)
    assert first["type"] == "ok"

    # 立刻再 propose 同 name
    second = catfish_tools.propose_skill(args)
    assert second["type"] == "error"
    assert "24h" in second["error"]


def test_old_proposal_does_not_block(proposals_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """24h 之前的 proposal 不阻塞新的"""
    # 先伪造一个 25h 前的 proposal
    old_ts = time.time() - 25 * 3600
    proposals_file.parent.mkdir(parents=True, exist_ok=True)
    proposals_file.write_text(
        json.dumps({
            "event_type": "proposed",
            "proposal_id": "prop_old",
            "name": "weekly-report",
            "status": "proposed",
            "ts": old_ts,
        }) + "\n",
        encoding="utf-8",
    )

    args = {
        "name": "weekly-report",
        "reason": "员工 3 次让我写周报, 这次是新的 pattern 出现",
        "action_steps": "1. 拉 audit log\n2. 拉 jira\n3. 拼草稿\n4. 输出 md",
        "evidence_count": 3,
    }
    out = catfish_tools.propose_skill(args)
    assert out["type"] == "ok", f"老 proposal 不该阻塞新, 但 err: {out.get('error')}"


# ============================================================
# 限流: 单 session ≥ 5 拒绝
# ============================================================


def test_session_limit_5(proposals_file: Path) -> None:
    """1 小时内最多 5 个 propose, 第 6 个拒"""
    for i in range(5):
        out = catfish_tools.propose_skill({
            "name": f"skill-{i}",
            "reason": f"员工 3 次重复 pattern {i}, 这是观察到的",
            "action_steps": f"1. step a {i}\n2. step b\n3. step c\n4. step d",
            "evidence_count": 3,
        })
        assert out["type"] == "ok", f"第 {i+1} 个该过, err: {out.get('error')}"

    # 第 6 个拒
    out6 = catfish_tools.propose_skill({
        "name": "skill-6",
        "reason": "员工 3 次重复 pattern 6, 想 propose",
        "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
        "evidence_count": 3,
    })
    assert out6["type"] == "error"
    assert "5" in out6["error"] or "上限" in out6["error"]


# ============================================================
# 落盘格式
# ============================================================


def test_proposal_jsonl_format(proposals_file: Path) -> None:
    out = catfish_tools.propose_skill({
        "name": "travel-expense-calc",
        "reason": "员工 4 次粘贴差旅报销单让我算, 同 pattern 重复",
        "action_steps": "1. 读金额列表\n2. 求和\n3. 应用部门标准\n4. 输出 Excel",
        "evidence_count": 4,
    })
    assert out["type"] == "ok"
    proposal_id = out["proposal_id"]

    # jsonl 文件该有一行
    raw = proposals_file.read_text(encoding="utf-8").strip()
    assert raw.count("\n") == 0  # 单行
    event = json.loads(raw)
    assert event["event_type"] == "proposed"
    assert event["proposal_id"] == proposal_id
    assert event["name"] == "travel-expense-calc"
    assert event["evidence_count"] == 4
    assert event["status"] == "proposed"
    assert "ts" in event
    assert "ts_iso" in event


def test_total_proposals_increments(proposals_file: Path) -> None:
    for i, name in enumerate(["weekly-report", "travel-calc", "project-prop"]):
        out = catfish_tools.propose_skill({
            "name": name,
            "reason": f"员工 3 次重复 pattern {i}, 这是 evidence",
            "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
            "evidence_count": 3,
        })
        assert out["type"] == "ok"
        assert out["total_proposals"] == i + 1


# ============================================================
# Tool 注册到 CATFISH_NATIVE_TOOLS
# ============================================================


def test_propose_skill_in_native_tools_list() -> None:
    names = {t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS}
    assert "catfish_propose_skill" in names


def test_dispatch_native_routes_propose_skill(proposals_file: Path) -> None:
    """通过 dispatch_native 的整链路 — 验证注册到 router"""
    out = catfish_tools.dispatch_native("catfish_propose_skill", {
        "name": "router-test-skill",
        "reason": "通过 dispatch_native 调用, 测试整链路",
        "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
        "evidence_count": 3,
    })
    assert out["type"] == "ok"
    assert out["name"] == "router-test-skill"
