"""skill_freeze v2 unit tests (BL-MM9-FREEZE-v2, 5/12).

测覆盖:
- teach_start / teach_end / freeze_skill 全流程
- freeze 拒 active session (强制 end 后凝固)
- freeze 拒空 trace
- 明文密码拒凝固 (安全)
- script.py syntax 必过
- _call schema 兼容 (browser_* {type:ok} ↔ freeze {ok:True})
- 参数推断 (username / password_ref / max_captcha_retry)
- captcha 数据流依赖识别
- snapshot/find_by_text/locate 标 skip
"""
from __future__ import annotations

import ast
import json
import shutil
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """trace + skills 目录隔离."""
    from catfish_tool_bridge import trace_recorder, skill_freeze
    fake_home = tmp_path / "home"
    fake_traces = fake_home / ".catfish" / "traces"
    fake_skills = tmp_path / "skills"
    fake_skills.mkdir()
    # 不放空 install_to_hermes.sh — 让 freeze 跑时 run_install 跳过
    monkeypatch.setattr(trace_recorder, "TRACE_DIR", fake_traces)
    monkeypatch.setattr(trace_recorder, "TRACE_PATH", fake_traces / "active.jsonl")
    monkeypatch.setattr(trace_recorder, "STATE_PATH", fake_traces / "_state.json")
    monkeypatch.setattr(trace_recorder, "LAST_COMPLETED_PATH", fake_traces / "_last_completed.json")
    monkeypatch.setattr(trace_recorder, "ARCHIVE_DIR", fake_traces)
    trace_recorder._SEQ = 0
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(fake_skills))
    yield {"home": fake_home, "skills": fake_skills, "traces": fake_traces}


def _seed_eis_trace():
    """录一份典型 EIS 登录 7 步教学 trace."""
    from catfish_tool_bridge import trace_recorder, skill_freeze
    skill_freeze.teach_start({"name": "eis-login", "description": "登 EIS"})
    steps = [
        ("catfish_browser_goto", {"url": "http://eis.ffcs.cn", "wait_until": "load"}, {"ok": True}),
        ("catfish_browser_snapshot", {"max_elements": 200}, {"ok": True}),
        ("catfish_browser_fill", {"selector": "#name", "text": "chenhb"}, {"ok": True}),
        ("catfish_browser_fill", {"selector": "#pwd", "secret_ref": "keychain://eis_password"}, {"ok": True}),
        ("catfish_recognize_captcha", {"selector": "#captchaImg", "hint": "alphanumeric_4"}, {"ok": True, "text": "cT92", "confidence": 0.65}),
        ("catfish_browser_fill", {"selector": "#captcha", "text": "cT92"}, {"ok": True}),
        ("catfish_browser_click", {"selector": "div.button-login"}, {"ok": True}),
    ]
    for tool, args, result in steps:
        trace_recorder.record(tool, args, result, True, 100)


# ─── teach_start / teach_end ─────────────────────────────────────────


def test_teach_start_success():
    from catfish_tool_bridge import skill_freeze, trace_recorder
    r = skill_freeze.teach_start({"name": "x", "description": "y"})
    assert r["ok"] is True
    assert r["name"] == "x"
    assert "session_id" in r
    assert trace_recorder.is_session_active()


def test_teach_end_returns_step_count():
    from catfish_tool_bridge import skill_freeze, trace_recorder
    _seed_eis_trace()
    r = skill_freeze.teach_end({"reason": "done"})
    assert r["ok"] is True
    assert r["step_count"] == 7
    assert r["name"] == "eis-login"


def test_teach_end_without_start_fails():
    from catfish_tool_bridge import skill_freeze
    r = skill_freeze.teach_end({})
    assert r["ok"] is False


# ─── freeze_skill 前置条件 ───────────────────────────────────────────


def test_freeze_requires_valid_name():
    from catfish_tool_bridge import skill_freeze
    r = skill_freeze.freeze_skill({"name": ""})
    assert r["ok"] is False and "name 必填" in r["error"]

    r = skill_freeze.freeze_skill({"name": "Invalid Name"})  # 含空格
    assert r["ok"] is False

    r = skill_freeze.freeze_skill({"name": "1bad"})  # 数字开头
    assert r["ok"] is False


def test_freeze_requires_valid_namespace():
    from catfish_tool_bridge import skill_freeze
    r = skill_freeze.freeze_skill({"name": "x", "namespace": "evil"})
    assert r["ok"] is False
    assert "namespace 必须是" in r["error"]


def test_freeze_rejects_active_session():
    """active session 期间 freeze → 拒."""
    from catfish_tool_bridge import skill_freeze
    skill_freeze.teach_start({"name": "midway"})
    r = skill_freeze.freeze_skill({"name": "midway", "run_install": False})
    assert r["ok"] is False
    assert "active teach session" in r["error"]


def test_freeze_rejects_no_completed_session():
    from catfish_tool_bridge import skill_freeze
    r = skill_freeze.freeze_skill({"name": "nothing", "run_install": False})
    assert r["ok"] is False
    assert "last_completed" in r["error"]


# ─── freeze_skill 产出验证 ───────────────────────────────────────────


def test_freeze_eis_login_produces_valid_script():
    """完整 EIS 登录 trace → 产出干净 script.py + SKILL.md."""
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({"reason": "done"})
    r = skill_freeze.freeze_skill({
        "name": "eis-login",
        "namespace": "department",
        "description": "登 EIS",
        "run_install": False,
    })
    assert r["ok"] is True, f"freeze failed: {r}"
    assert r["trace_steps_used"] == 7
    # 检查产出文件存在
    script_path = Path(r["files"][0])
    md_path = Path(r["files"][1])
    assert script_path.exists()
    assert md_path.exists()
    # script.py syntax 必过
    src = script_path.read_text(encoding="utf-8")
    ast.parse(src)
    # 关键字段
    assert "def render_eis_login" in src
    assert "_call(" in src
    assert "catfish_browser_goto" in src
    assert "captcha_text" in src  # captcha 数据流变量


def test_freeze_overwrite_false_blocks_existing(tmp_path):
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r1 = skill_freeze.freeze_skill({"name": "dup", "run_install": False})
    assert r1["ok"] is True
    # 第二次同名 + overwrite=false → 拒
    # (需要重新 teach + end, 因为 freeze 后 last_completed 还在但 skill 目录已存在)
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r2 = skill_freeze.freeze_skill({"name": "dup", "run_install": False, "overwrite": False})
    assert r2["ok"] is False
    assert "已存在" in r2["error"]


def test_freeze_overwrite_true_replaces():
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r1 = skill_freeze.freeze_skill({"name": "dup", "run_install": False})
    assert r1["ok"]
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r2 = skill_freeze.freeze_skill({"name": "dup", "run_install": False, "overwrite": True})
    assert r2["ok"] is True


# ─── 安全 — 明文密码拒凝固 ───────────────────────────────────────────


def test_freeze_refuses_plaintext_password():
    """trace 含 #pwd selector + 明文密码 → 拒凝固."""
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": "leak"})
    trace_recorder.record("catfish_browser_goto", {"url": "x"}, {"ok": True}, True, 100)
    # 明文密码 (像密码: 8-32 字 + 数字字母混)
    trace_recorder.record(
        "catfish_browser_fill",
        {"selector": "#pwd", "text": "MyP@ss12345"},  # 明文!
        {"ok": True}, True, 100,
    )
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "leak", "run_install": False})
    assert r["ok"] is False
    assert "明文密码" in r["error"]


def test_freeze_accepts_secret_ref():
    """secret_ref → 凝固成功, script.py 不含明文."""
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": "safe"})
    trace_recorder.record("catfish_browser_goto", {"url": "x"}, {"ok": True}, True, 100)
    trace_recorder.record(
        "catfish_browser_fill",
        {"selector": "#pwd", "secret_ref": "keychain://my_pw"},
        {"ok": True}, True, 100,
    )
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "safe", "run_install": False})
    assert r["ok"] is True
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    assert "MyP@ss" not in src and "明文" not in src
    assert "password_ref" in src or "keychain://my_pw" in src


# ─── 参数推断 ────────────────────────────────────────────────────────


def test_param_inference_extracts_username_pwd_captcha():
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "params-test", "run_install": False})
    assert r["ok"]
    param_names = [p["name"] for p in r["params"]]
    assert "username" in param_names
    assert "password_ref" in param_names
    assert "max_captcha_retry" in param_names
    # username 默认值是 'chenhb'
    username_p = next(p for p in r["params"] if p["name"] == "username")
    assert "chenhb" in username_p["default"]


def test_no_captcha_no_max_captcha_retry_param():
    """trace 没 recognize_captcha → 不推 max_captcha_retry 参数."""
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": "no-captcha"})
    trace_recorder.record("catfish_browser_goto", {"url": "x"}, {"ok": True}, True, 100)
    trace_recorder.record(
        "catfish_browser_fill",
        {"selector": "#name", "text": "u"}, {"ok": True}, True, 100,
    )
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "no-captcha", "run_install": False})
    assert r["ok"]
    param_names = [p["name"] for p in r["params"]]
    assert "max_captcha_retry" not in param_names


# ─── _call schema 兼容 (核心 — 5/12 鸿波撞坑根因) ─────────────────────


def test_generated_call_function_handles_old_schema():
    """生成的 script.py 里 _call 必须认 browser_* 老 schema {type: ok/error}."""
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "schema-test", "run_install": False})
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    # 关键: _call 必须检测 type=='error' 当失败
    assert 'r.get("type") == "error"' in src
    # 不能只看 ok=True 默认 (那是早上的 bug 版本)
    assert 'r.get("ok", True)' not in src


def test_generated_call_has_retry_loop():
    """cold-start 兜底 — _call 必须有 retry."""
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "retry-test", "run_install": False})
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    assert "retries: int = 2" in src
    assert "retry_delay" in src


# ─── captcha 数据流依赖 ──────────────────────────────────────────────


def test_captcha_text_uses_variable_not_hardcode():
    """fill #captcha 的 text 跟上一步 recognize_captcha result.text 一致 →
    生成 script.py 用 captcha_text 变量, 不 hard-code 'cT92'."""
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "captcha-flow", "run_install": False})
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    # 不能 hard-code 'cT92' 当 captcha 值
    # (cT92 在 trace 里, 但 script.py 应该改用 captcha_text 变量)
    assert "'cT92'" not in src and '"cT92"' not in src
    # 必须用 captcha_text 变量
    assert "captcha_text" in src
    # captcha retry loop 自动插入
    assert "max_captcha_retry" in src


# ─── snapshot / find_by_text / locate 标 skip ────────────────────────


def test_snapshot_marked_as_skip():
    """LLM-only 工具不进 script.py 可执行流程, 只留注释."""
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "skip-test", "run_install": False})
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    assert "skip (LLM-only): catfish_browser_snapshot" in src
