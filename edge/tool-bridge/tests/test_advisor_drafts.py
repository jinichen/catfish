"""P3.5.40 (6/18 鸿波 audit huashu-design 后催 'Junior Designer 早 show'):
draft_email_reply phase 化单测.

跑法: cd edge/tool-bridge && PYTHONPATH=src python3 -m pytest tests/test_advisor_drafts.py -q

覆盖:
- 默认 phase=final (老 caller 兼容)
- phase=final 时 content 必填
- phase=assumptions 时 content 可空, questions 必填
- phase=assumptions 写 reply-{rec}-{tone}-questions.md 含 questions/assumptions/outline
- phase 非法值拒
- 返字段含 phase
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_tool_bridge import advisor_drafts as ad  # noqa: E402


@pytest.fixture
def tmp_catfish(tmp_path: Path, monkeypatch):
    """临时 ~/.catfish/outputs/ 目录, env CATFISH_HOME 重定向."""
    catfish = tmp_path / ".catfish"
    catfish.mkdir()
    (catfish / "outputs").mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(catfish))
    return catfish


# ── 默认 phase=final (向后兼容老 caller) ─────────────────────────────


def test_default_phase_final_works_like_before(tmp_catfish):
    """不传 phase, 默认 final, 跟老行为一致."""
    result = ad.draft_email_reply({
        "tone": "balanced",
        "thread_id": "thread_1",
        "recipient": "boss@example.com",
        "subject": "项目进度",
        "content": "您好, 项目按计划推进, 9 月底交付.",
    })
    assert "error" not in result
    assert result["phase"] == "final"
    assert result["tone"] == "balanced"
    assert result["filename"].endswith("balanced.md")
    assert "questions" not in result["filename"]


def test_phase_final_content_required(tmp_catfish):
    """phase=final 时 content 必填."""
    result = ad.draft_email_reply({
        "tone": "balanced",
        "thread_id": "t1",
        "recipient": "x",
        "subject": "y",
        "phase": "final",
        "content": "",
    })
    assert "error" in result
    assert "content 不能为空" in result["error"]


# ── phase=assumptions (Junior Designer 早 show) ────────────────────


def test_phase_assumptions_questions_required(tmp_catfish):
    """phase=assumptions 时 questions 必填 (没 questions 等于直接 final, 拒)."""
    result = ad.draft_email_reply({
        "tone": "balanced",
        "thread_id": "t1",
        "recipient": "x",
        "subject": "y",
        "phase": "assumptions",
        "content": "draft",
    })
    assert "error" in result
    assert "questions 必填" in result["error"]


def test_phase_assumptions_with_questions(tmp_catfish):
    """phase=assumptions content 可空, 写 questions 文件."""
    result = ad.draft_email_reply({
        "tone": "balanced",
        "thread_id": "t_q1",
        "recipient": "boss@example.com",
        "subject": "客户合同",
        "phase": "assumptions",
        "questions": [
            "上次电话提的预算具体数字",
            "是否要 cc 张主任",
            "客户公司用全称还是简称",
        ],
    })
    assert "error" not in result, result.get("error")
    assert result["phase"] == "assumptions"
    assert result["questions_count"] == 3
    assert "questions" in result["filename"]
    # 文件内容含 questions
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "需要员工先答" in text
    assert "上次电话提的预算具体数字" in text
    assert "是否要 cc 张主任" in text


def test_phase_assumptions_with_assumptions_and_outline(tmp_catfish):
    """assumptions + outline 字段一起写入."""
    result = ad.draft_email_reply({
        "tone": "strict",
        "thread_id": "t_q2",
        "recipient": "client@example.com",
        "subject": "回函",
        "phase": "assumptions",
        "questions": ["确认报价是否含税"],
        "assumptions": ["默认假设员工要 hold 这单", "默认项目截止 9 月底"],
        "outline": ["1. 致谢", "2. 确认 3 点", "3. 提下次会议"],
    })
    assert result["phase"] == "assumptions"
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "已假设" in text
    assert "默认假设员工要 hold 这单" in text
    assert "计划回信 outline" in text
    assert "1. 致谢" in text


def test_phase_assumptions_with_draft_content(tmp_catfish):
    """phase=assumptions 时 content 可放草稿初稿, 跟 questions 一起写."""
    result = ad.draft_email_reply({
        "tone": "friendly",
        "thread_id": "t_q3",
        "recipient": "alice@example.com",
        "subject": "周会",
        "phase": "assumptions",
        "questions": ["周三还是周四"],
        "content": "您好, [TODO 时间 placeholder] 开周会, 议题如下…",
    })
    assert result["phase"] == "assumptions"
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "LLM 给的草稿初步" in text
    assert "[TODO 时间 placeholder]" in text


# ── 边界 ──────────────────────────────────────────────────────────


def test_invalid_phase_value_rejected(tmp_catfish):
    """phase 非 assumptions/final → 拒."""
    result = ad.draft_email_reply({
        "tone": "balanced",
        "thread_id": "t1",
        "recipient": "x",
        "subject": "y",
        "phase": "draft",  # 非法
        "content": "hi",
    })
    assert "error" in result
    assert "phase 只能是" in result["error"]


def test_final_phase_filename_unchanged_from_old(tmp_catfish):
    """phase=final filename 保持老格式 reply-{rec}-{tone}.md (无 questions 后缀)."""
    result = ad.draft_email_reply({
        "tone": "urgent",
        "thread_id": "t1",
        "recipient": "ceo@example.com",
        "subject": "x",
        "phase": "final",
        "content": "ok",
    })
    assert result["filename"] == "reply-ceoexamplecom-urgent.md"
