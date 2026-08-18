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
    """trace + skills 目录隔离 (5/21: 同时隔离 LOCAL_SKILLS_ROOT 防写真用户 home)."""
    from catfish_tool_bridge import trace_recorder, skill_freeze, skill_register
    fake_home = tmp_path / "home"
    fake_traces = fake_home / ".catfish" / "traces"
    fake_skills = tmp_path / "skills"          # workspace path
    fake_local = fake_home / ".catfish" / "skills"  # 5/21 local path (默认 target)
    fake_skills.mkdir()
    monkeypatch.setattr(trace_recorder, "TRACE_DIR", fake_traces)
    monkeypatch.setattr(trace_recorder, "TRACE_PATH", fake_traces / "active.jsonl")
    monkeypatch.setattr(trace_recorder, "STATE_PATH", fake_traces / "_state.json")
    monkeypatch.setattr(trace_recorder, "LAST_COMPLETED_PATH", fake_traces / "_last_completed.json")
    monkeypatch.setattr(trace_recorder, "ARCHIVE_DIR", fake_traces)
    trace_recorder._SEQ = 0
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(fake_skills))
    # 5/21 方案 1: target='local' 默认, mock LOCAL_SKILLS_ROOT 防写真 ~/.catfish/skills/
    monkeypatch.setattr(skill_register, "LOCAL_SKILLS_ROOT", fake_local)
    monkeypatch.setattr(skill_freeze, "LOCAL_SKILLS_ROOT", fake_local)
    # 5/21: hermes config.yaml 也指向 tmp_path (默认没有, 注册器走 skipped_no_config)
    fake_hermes_cfg = fake_home / ".hermes" / "config.yaml"
    monkeypatch.setattr(skill_register, "HERMES_CONFIG_PATH", fake_hermes_cfg)
    yield {
        "home": fake_home,
        "skills": fake_skills,
        "traces": fake_traces,
        "local": fake_local,
        "hermes_cfg": fake_hermes_cfg,
    }


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


# ─── 8/18: secret_for_site —— 凝固时什么都不焊 ──────────────────────
#
# 老路 (secret_ref) 不是不安全, 它不把明文写进 script。问题是它焊死的那个
# **引用串会过期**: _infer_params 把当天那个 ref 变成 password_ref 的默认值,
# 员工改了密码 / 换个地方存, script 还在读老的。
#
# 8/17 实撞: UI 存进 catfish-teaching:http://eis.ffcs.cn, 冻结的 eis-login 读
# 4/28 那条 eis_password —— 登录报"账号或密码错误", 两边谁也不知道谁, UI 上
# 看一切正常。
#
# secret_for_site 没有任何"当天的值"可以焊 —— 站点是运行时从 page.url 取的。


def _freeze_one_fill(name: str, fill_args: dict):
    """录 goto + 一次 fill, 凝固, 返回 (result, script 源码)."""
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": name})
    trace_recorder.record("catfish_browser_goto", {"url": "http://eis.ffcs.cn"},
                          {"ok": True}, True, 100)
    trace_recorder.record("catfish_browser_fill", fill_args, {"ok": True}, True, 100)
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": name, "run_install": False})
    assert r["ok"] is True, r
    return r, Path(r["files"][0]).read_text(encoding="utf-8")


def test_secret_for_site_不产生任何参数():
    """★★★ 这条就是整个改动的要点。

    只要 params 里冒出 password_ref (或任何带默认值的 ref / 站点),
    就说明又把"当天那个值"焊进去了 —— 员工改密码就会失联。
    """
    r, src = _freeze_one_fill("site-pw", {"selector": "#pwd", "secret_for_site": True})
    names = [p["name"] for p in r["params"]]
    assert "password_ref" not in names, f"又焊了个 ref 参数: {r['params']}"
    assert not any("secret" in n or "site" in n for n in names), (
        f"不该为这条 fill 造任何参数 (站点必须是运行时的当前页): {names}"
    )
    # 站点也不许焊进这一步 —— 焊进去换个入口 (eis→neis) 就错。
    #
    # ⚠ 不能写 `"eis.ffcs.cn" not in src`: goto 那一步本来就该带 URL, 那条会
    #   永远红。第一版我写成了 `... or "goto" in src` 来绕开 —— 而 goto 必然
    #   在 src 里, 整条断言永远为真, 是个假测试。改成只看 fill 那一行。
    #
    # ⚠⚠ 第二版我筛的是"含 secret_for_site 的行", 也不对 —— 那个词还出现在
    #    last_step / raise 里, 以及 SKILL.md 头部的 trace 路径 (路径里含**测试
    #    函数名**, 而这个测试就叫 test_secret_for_site_…)。判据得钉到那一行本身。
    fill_lines = [ln for ln in src.splitlines() if '_call("catfish_browser_fill"' in ln]
    assert len(fill_lines) == 1, f"应该只有一次 fill: {fill_lines}"
    assert '"secret_for_site": True' in fill_lines[0], fill_lines[0]
    assert "ffcs" not in fill_lines[0], f"站点被焊进 fill 了: {fill_lines[0]}"


def test_secret_for_site_原样透传给_browser_fill():
    r, src = _freeze_one_fill("site-pw2", {"selector": "#pwd", "secret_for_site": True})
    assert '"secret_for_site": True' in src, src[-1500:]
    assert "keychain://" not in src, "不该出现任何引用串"


def test_secret_for_site_的_fill_不会被当成_username():
    """★★ 密码框那次 fill 不能被 username 启发吃掉。

    _infer_params 的 username 启发是"第一个非密码 fill 的 text"。
    secret_for_site 这条没有 text, 要是不显式摘出去, 它会掉进 elif 分支,
    拿一个空 text 造出 `username: str = ''` —— 参数表里多一个假参数,
    而真正的用户名那一步反而不再当参数了。

    ⚠ selector 用 `#passwd` 不是随手挑的。第一版写的 `#pwd`, 变异 (把
      `elif args.get("secret_for_site")` 改成 `elif False`) **抓不到** ——
      因为 _infer_params 里原有的启发已经按 selector 含 "pwd"/"password"
      把它挡掉了, 我加的这条分支根本没被走到。测试通过是因为别的原因。

      `#passwd` 的字面里既没有 "pwd" 也没有 "password" (p-a-s-s-w-d),
      老启发漏得掉, 才真正走到新分支。实盘也确实有这种 id ——
      `#pass` / `#j_pass` / `#loginPass` 全漏。
    """
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": "site-order"})
    trace_recorder.record("catfish_browser_goto", {"url": "http://eis.ffcs.cn"},
                          {"ok": True}, True, 100)
    # 密码框在前, 用户名在后 —— 顺序反过来才测得到"会不会被吃掉"
    trace_recorder.record("catfish_browser_fill",
                          {"selector": "#passwd", "secret_for_site": True},
                          {"ok": True}, True, 100)
    trace_recorder.record("catfish_browser_fill",
                          {"selector": "#name", "text": "chenhb"},
                          {"ok": True}, True, 100)
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "site-order", "run_install": False})
    assert r["ok"] is True, r
    names = [p["name"] for p in r["params"]]
    assert names.count("username") == 1, f"username 参数不对: {r['params']}"
    username_p = next(p for p in r["params"] if p["name"] == "username")
    assert "chenhb" in username_p["default"], (
        f"username 默认值被密码框那次 fill 吃掉了: {username_p}"
    )


def test_secret_ref_优先于_secret_for_site():
    """★★ 顺序必须跟运行时一致。

    _browser_fill_impl 是 secret_ref 优先。凝固时反过来的话, 冻结出来的行为
    跟教学时不一样 —— 那是最难查的一类 bug。
    """
    r, src = _freeze_one_fill(
        "both", {"selector": "#pwd", "secret_ref": "keychain://x", "secret_for_site": True}
    )
    assert "password_ref" in [p["name"] for p in r["params"]]
    assert '"secret_ref": password_ref' in src
    assert "secret_for_site" not in src


def test_老的_secret_ref_路还能凝固():
    """三个已冻结的 EIS skill 全走这条, 不能顺手拆掉。"""
    r, src = _freeze_one_fill("legacy", {"selector": "#pwd", "secret_ref": "keychain://old"})
    assert "password_ref" in [p["name"] for p in r["params"]]
    assert '"secret_ref": password_ref' in src


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


# ─── v2.1: chrome 状态预检 (5/12 鸿波 13:38 撞坑修) ────────────────────


def test_first_goto_has_state_check():
    """第一步 goto 模板化时插入 actual_url 校验, 防 chrome 已登录撞 fill 失败."""
    from catfish_tool_bridge import skill_freeze
    _seed_eis_trace()
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "state-check", "run_install": False})
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    # 第一步 goto 必须含 actual_url 校验
    assert "_actual_url" in src
    assert "goto_state_check" in src
    # 错误信息引导员工 (重启 chrome)
    assert "chrome 状态不符" in src


def test_only_first_goto_has_state_check():
    """只有第一步 goto 加状态预检, 后续 goto (如果有) 不加, 避免误触."""
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": "multi-goto"})
    trace_recorder.record("catfish_browser_goto", {"url": "http://a"}, {"ok": True, "actual_url": "http://a"}, True, 100)
    trace_recorder.record("catfish_browser_click", {"selector": "#btn"}, {"ok": True}, True, 100)
    trace_recorder.record("catfish_browser_goto", {"url": "http://b"}, {"ok": True}, True, 100)
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "multi-goto", "run_install": False})
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    # 只有 1 个状态预检 (第一步)
    assert src.count("goto_state_check") == 1


# ─── v2.2: 嵌套调 skill (eis-checkin 直接调 eis-login 不重教 7 步) ─────


def test_nested_skill_call_emitted():
    """trace 含 catfish_run_skill → script.py 模板化成嵌套 _call."""
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": "nested"})
    # 教学: 第一步调凝固好的 eis-login skill, 第二步点打卡按钮
    trace_recorder.record(
        "catfish_run_skill",
        {"skill_path": "department/eis-login", "params": {"username": "chenhb"}},
        {"ok": True, "captcha_attempts": 1, "duration_ms": 12000},
        True, 12000,
    )
    trace_recorder.record(
        "catfish_browser_click",
        {"selector": "div.checkin-btn"},
        {"ok": True}, True, 200,
    )
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({
        "name": "eis-checkin-nested",
        "namespace": "department",
        "description": "嵌套调 eis-login + 点打卡",
        "run_install": False,
    })
    assert r["ok"] is True
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    # 必须有 catfish_run_skill 嵌套调用
    assert '_call("catfish_run_skill"' in src
    assert "department/eis-login" in src
    # last_step 标记
    assert "run_skill:department/eis-login" in src
    # syntax 必过
    import ast
    ast.parse(src)


def test_nested_skill_failure_propagates():
    """嵌套 skill 失败时, 外层 skill 也按铁律 raise _SkillStepFailure 报错."""
    from catfish_tool_bridge import skill_freeze, trace_recorder
    skill_freeze.teach_start({"name": "nested-fail"})
    trace_recorder.record(
        "catfish_run_skill",
        {"skill_path": "department/eis-login", "params": {}},
        {"ok": True}, True, 100,
    )
    skill_freeze.teach_end({})
    r = skill_freeze.freeze_skill({"name": "nested-fail", "run_install": False})
    src = Path(r["files"][0]).read_text(encoding="utf-8")
    # 模板必须 raise _SkillStepFailure (跟普通 step 一致)
    assert 'raise _SkillStepFailure("run_skill:department/eis-login"' in src
    assert "嵌套 skill 失败" in src
