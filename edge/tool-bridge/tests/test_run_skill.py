"""tool-bridge run_skill 测试 — LLM 通过这个工具调 catfish/skills/ 下的 skill.

覆盖:
  - _help 模式拿 schema
  - 正常调用生成 .docx
  - 路径遍历攻击拦截
  - 不存在的 skill / 不存在的 script.py
  - 参数不匹配的友好错误
  - sys.modules 注册 (dataclass + future annotations 兼容性)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让测试可以 import tool-bridge
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_tool_bridge.catfish_tools import (  # noqa: E402
    NATIVE_TOOL_NAMES,
    is_native,
    run_skill,
)


# ── 注册检查 ────────────────────────────────────────────────────


def test_run_skill_in_native_tools():
    assert "catfish_run_skill" in NATIVE_TOOL_NAMES
    assert is_native("catfish_run_skill")


# ── _help 模式 ──────────────────────────────────────────────────


def test_help_returns_schema():
    r = run_skill(
        {"skill_path": "department/leadership-briefing", "params": {"_help": True}}
    )
    assert r["ok"] is True
    assert "help" in r
    assert "render_briefing" in r["summary"]
    required = r["help"]["required"]
    # 5 段 + 周边参数
    for k in ("title", "background", "problems", "solutions", "next_steps"):
        assert k in required, f"required 缺 {k}"


# ── 正常调用 ────────────────────────────────────────────────────


def _sample_params(out_path: str) -> dict:
    return dict(
        title="测试请示件",
        background="背景一段.",
        problems=[{"desc": "p", "impact": "i", "urgency": "高"}],
        solutions=[
            {
                "name": "A",
                "recommended": True,
                "path": "p",
                "resources": "r",
                "effect": "e",
                "risk": "k",
            }
        ],
        recommendation_reason="理由",
        requests=[{"content": "请批准", "owner": "X 部", "deadline": "下周"}],
        next_steps=[
            {"action": "落地", "owner": "张三", "deadline": "5 月底", "note": ""}
        ],
        department="测试部",
        date_str="2026 年 4 月 30 日",
        output_path=out_path,
    )


def test_render_real_docx(tmp_path):
    out = tmp_path / "rsk.docx"
    r = run_skill(
        {
            "skill_path": "department/leadership-briefing",
            "params": _sample_params(str(out)),
        }
    )
    assert r["ok"] is True
    assert out.exists()
    assert str(out) in r["files"], f"files 字段应含输出路径, 实际 {r['files']}"


# ── 安全 ────────────────────────────────────────────────────────


def test_traversal_blocked():
    r = run_skill({"skill_path": "../../etc/passwd", "params": {}})
    assert r["ok"] is False
    assert "越界" in r["error"]


def test_missing_skill_path():
    r = run_skill({"skill_path": "", "params": {}})
    assert r["ok"] is False
    assert "必填" in r["error"]


def test_nonexistent_skill():
    r = run_skill({"skill_path": "nonexistent/foo", "params": {}})
    assert r["ok"] is False
    assert "不存在" in r["error"]


# ── 参数错误友好提示 ────────────────────────────────────────────


def test_bad_params_friendly_error():
    r = run_skill(
        {
            "skill_path": "department/leadership-briefing",
            "params": {"wrong_param": 1},
        }
    )
    assert r["ok"] is False
    # 错误里要提示用 _help 模式
    assert "_help" in r["error"]


# ── 多次调用 sys.modules 不污染 ─────────────────────────────────


def test_multiple_calls_no_module_leak(tmp_path):
    """同一 skill 连调 3 次, 不应抛异常 (sys.modules 注册要幂等).

    踩过坑: 第一版没注册到 sys.modules, dataclass __module__ lookup 报
    'NoneType' has no __dict__. 修复后这里就是 regression test.
    """
    for i in range(3):
        out = tmp_path / f"multi-{i}.docx"
        r = run_skill(
            {
                "skill_path": "department/leadership-briefing",
                "params": _sample_params(str(out)),
            }
        )
        assert r["ok"] is True, f"第 {i+1} 次调用失败: {r}"
