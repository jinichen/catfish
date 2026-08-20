"""BL-LEARN-RECMODE aggregator (5/14 v0 骨架) 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_aggregator.py -q

测纯函数 (不调 LLM): load_recording_inputs / build_messages / parse_llm_output /
render_skill_md / render_main_py / write_skill_files. call_llm 5/26 真做才接.
"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge.recmode import aggregator


# ─── load_recording_inputs ─────────────────────────────────


def test_load_inputs_empty_dir(tmp_path):
    """没 events 没语音, 不挂"""
    sd = tmp_path / "rec_empty"
    sd.mkdir()
    inp = aggregator.load_recording_inputs(sd)
    assert inp.events == []
    assert inp.transcripts == []
    assert inp.screenshots == []


def test_load_inputs_full(tmp_path):
    """events + transcripts + screenshots 全有"""
    sd = tmp_path / "rec_full"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        '{"ts": 0.0, "kind": "page_navigated", "url": "http://eis.ffcs.cn/"}\n'
        '{"ts": 3.2, "kind": "click", "text": "应用"}\n',
        encoding="utf-8",
    )
    (sd / "transcripts.jsonl").write_text(
        '{"ts": 1.5, "text": "现在演示企业资质检查"}\n'
        '{"ts": 4.0, "text": "点应用 tab"}\n',
        encoding="utf-8",
    )
    ss_dir = sd / "screenshots"
    ss_dir.mkdir()
    (ss_dir / "kf_001.png").write_bytes(b"fake_png_bytes_001")
    (ss_dir / "kf_002.png").write_bytes(b"fake_png_bytes_002")

    inp = aggregator.load_recording_inputs(sd)
    assert len(inp.events) == 2
    assert inp.events[0]["kind"] == "page_navigated"
    assert len(inp.transcripts) == 2
    assert "现在演示" in inp.transcripts[0]["text"]
    assert len(inp.screenshots) == 2
    assert inp.screenshots[0][0] == "kf_001"
    assert inp.screenshots[0][1] == b"fake_png_bytes_001"


def test_load_inputs_skips_corrupt_jsonl(tmp_path):
    """坏 jsonl 行跳过, 不挂"""
    sd = tmp_path / "rec_bad"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        '{"ts": 0, "kind": "ok"}\n'
        'not json\n'
        '{"ts": 1, "kind": "ok2"}\n',
        encoding="utf-8",
    )
    inp = aggregator.load_recording_inputs(sd)
    assert len(inp.events) == 2


# ─── build_messages ────────────────────────────────────────


def test_build_messages_structure(tmp_path):
    sd = tmp_path / "rec_msg"
    sd.mkdir()
    (sd / "events.jsonl").write_text('{"ts": 0, "kind": "click", "text": "应用"}\n', encoding="utf-8")
    (sd / "transcripts.jsonl").write_text('{"ts": 0.5, "text": "点应用"}\n', encoding="utf-8")
    (sd / "screenshots").mkdir()
    (sd / "screenshots" / "kf_001.png").write_bytes(b"png_bytes")

    inp = aggregator.load_recording_inputs(sd)
    msgs = aggregator.build_messages(inp)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "selector" in msgs[0]["content"]  # SYSTEM_PROMPT 含纪律
    assert msgs[1]["role"] == "user"
    parts = msgs[1]["content"]
    assert isinstance(parts, list)
    # 第一段 text 含 events + 语音
    assert "events 序列" in parts[0]["text"]
    assert "应用" in parts[0]["text"]
    assert "点应用" in parts[0]["text"]
    # 截图 multipart
    img_parts = [p for p in parts if p["type"] == "image_url"]
    assert len(img_parts) == 1
    assert img_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


# ─── parse_llm_output ──────────────────────────────────────


def test_parse_llm_output_with_markdown_fence():
    raw = """这是 LLM 思考...

```json
{
  "skill_name": "eis-qual-check",
  "namespace": "department",
  "kind": "procedural",
  "description": "EIS 企业资质过期检查",
  "triggers": ["资质过期", "资质检查", "EIS 扫描"],
  "intent_summary": "扫所有页找快过期",
  "params_schema": [{"name": "days", "type": "integer", "default": 90, "description": "阈值"}],
  "steps": [{"step_no": 1, "intent": "登录", "tool": "catfish_browser_navigate", "args_template": {"url": "http://eis.ffcs.cn/"}}],
  "execute_code_segment": "# 抓表",
  "output_schema": {"report_path": "string"},
  "confidence": 0.85,
  "questions_for_user": []
}
```

收尾..."""
    skill = aggregator.parse_llm_output(raw)
    # P3.5.43: kebab-case 标准化
    assert skill.skill_name == "eis-qual-check"
    assert skill.namespace == "department"
    assert skill.kind == "procedural"
    assert skill.triggers == ["资质过期", "资质检查", "EIS 扫描"]
    assert skill.confidence == 0.85
    assert len(skill.steps) == 1


def test_parse_llm_output_normalizes_snake_to_kebab():
    """P3.5.43: LLM 偶尔输出 snake_case, 自动转 kebab."""
    raw = '{"skill_name": "weekly_report", "namespace": "personal", "kind": "procedural", "description": "y", "triggers": ["a", "b", "c"], "params_schema": [], "steps": [], "execute_code_segment": "", "output_schema": {}, "confidence": 0.5, "questions_for_user": []}'
    skill = aggregator.parse_llm_output(raw)
    assert skill.skill_name == "weekly-report"


def test_parse_llm_output_fills_defaults_for_missing_triggers_kind():
    """P3.5.43: LLM 漏给 triggers/kind 不挂 — 走 default 让 validate 后续 warn."""
    raw = '{"skill_name": "x", "namespace": "personal", "description": "y", "params_schema": [], "steps": [], "execute_code_segment": "", "output_schema": {}, "confidence": 0.5, "questions_for_user": []}'
    skill = aggregator.parse_llm_output(raw)
    assert skill.skill_name == "x"
    assert skill.triggers == []  # fallback
    assert skill.kind == "procedural"  # default


def test_parse_llm_output_no_fence():
    """LLM 直接吐 JSON 不带围栏也能 parse."""
    raw = '{"skill_name": "x", "namespace": "personal", "kind": "procedural", "description": "y", "triggers": ["a","b","c"], "params_schema": [], "steps": [], "execute_code_segment": "", "output_schema": {}, "confidence": 0.5, "questions_for_user": []}'
    skill = aggregator.parse_llm_output(raw)
    assert skill.skill_name == "x"


def test_parse_llm_output_no_json_raises():
    with pytest.raises(ValueError, match="找不到 JSON"):
        aggregator.parse_llm_output("纯 prose, 没 JSON.")


def test_parse_llm_output_bad_json_raises():
    """有 { 有 } 但内容不是 JSON → parse 失败"""
    with pytest.raises(ValueError, match="parse 失败"):
        aggregator.parse_llm_output("前 { not valid json content } 后")


# ─── render_skill_md / render_main_py ──────────────────────


def _mock_skill():
    # P3.5.43: skill_name kebab-case; 新增 kind + triggers 必填
    return aggregator.SkillOutput(
        skill_name="eis-qual-check",
        namespace="department",
        kind="procedural",
        description="EIS 企业资质过期检查",
        triggers=["资质过期", "资质检查", "EIS 扫描"],
        intent_summary="扫所有页找证书有效期 < N 天的",
        params_schema=[
            {"name": "days_threshold", "type": "integer", "default": 90, "description": "阈值天数"},
        ],
        steps=[
            {
                "step_no": 1,
                "intent": "进资质管理",
                "tool": "catfish_browser_find_by_text",
                "args_template": {"text": "应用"},
                "selector_hint": {"text": "应用", "near_text": "通讯录", "role": "tab"},
                "expected_after": "应用网格出现",
            },
        ],
        execute_code_segment="rows = []\nfor p in range(1, pages+1):\n    rows.extend(parse_page())",
        output_schema={"expiring_count": "number"},
        confidence=0.82,
        questions_for_user=["是否需要按部门筛选?"],
        raw_json={},
    )


def test_render_skill_md():
    md = aggregator.render_skill_md(_mock_skill())
    # P3.5.43: 开头必须是 frontmatter, 不能直接 # title
    assert md.startswith("---\n")
    assert "name: eis-qual-check" in md
    assert "kind: procedural" in md
    assert "triggers:" in md
    assert "  - 资质过期" in md
    assert "# eis-qual-check" in md  # 标题在 frontmatter 后
    assert "EIS 企业资质过期检查" in md
    assert "**days_threshold**" in md
    assert "selector_hint" in md
    assert "是否需要按部门筛选" in md  # questions_for_user


def test_render_skill_md_with_meta():
    md = aggregator.render_skill_md(
        _mock_skill(),
        recording_meta={"session_id": "rec_1", "duration_s": 320, "events_count": 25, "keyframes_count": 8},
    )
    assert "rec_1" in md
    assert "320" in md
    assert "25" in md


def test_render_script_py():
    """P3.5.43: render_main_py 改名 render_script_py, 生成 def render_<name>()."""
    py = aggregator.render_script_py(_mock_skill())
    assert 'SKILL_NAME = "eis-qual-check"' in py
    # P3.5.43 BLOCKER 2: 函数名 render_ 前缀, 不是 main
    assert "def render_eis_qual_check(params)" in py
    assert "def main(" not in py  # 不再有 main
    assert "Step 1: 进资质管理" in py
    assert "parse_page" in py
    # 8/20: 步骤只 emit 成注释, body 没实现 → 必须返 ok=False。
    # 原来这里断言 '"ok": True', 把「什么都不做却报告成功」钉成了契约。
    # 详见 test_skill_format.test_script_py_returns_dict_with_ok 的说明。
    assert '"ok": False' in py


def test_render_main_py_deprecated_alias_still_works():
    """老 caller 调 render_main_py — deprecation warn 但仍 work."""
    import warnings
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        py = aggregator.render_main_py(_mock_skill())
        assert any("deprecated" in str(w.message).lower() for w in ws)
    # 内容跟 render_script_py 一样
    assert "def render_eis_qual_check(params)" in py


# ─── write_skill_files ─────────────────────────────────────


def test_write_skill_files(tmp_path):
    skill = _mock_skill()
    out_dir = aggregator.write_skill_files(
        skill,
        skills_root=tmp_path,
        recording_meta={"session_id": "rec_1", "duration_s": 100, "events_count": 5, "keyframes_count": 2},
    )
    assert out_dir == tmp_path / "department" / "eis-qual-check"
    assert (out_dir / "SKILL.md").exists()
    # P3.5.43: 文件名 script.py 不是 main.py
    assert (out_dir / "script.py").exists()
    assert not (out_dir / "main.py").exists()
    assert (out_dir / "recmode_meta.json").exists()
    meta = json.loads((out_dir / "recmode_meta.json").read_text())
    assert meta["session_id"] == "rec_1"
    # P3.5.43: meta 含 validate_errors 字段
    assert "validate_errors" in meta


# ─── aggregate_session 端到端 (LLM 占位) ─────────────────


@pytest.mark.asyncio
async def test_aggregate_session_e2e_with_mock_llm(tmp_path, monkeypatch):
    """端到端 mock call_llm 返一段 JSON → 真 parse → 真落 skill 文件."""
    sd = tmp_path / "rec_e2e"
    sd.mkdir()
    (sd / "events.jsonl").write_text('{"ts": 0, "kind": "click"}\n', encoding="utf-8")
    (sd / "meta.json").write_text('{"session_id": "rec_e2e", "duration_s": 10}', encoding="utf-8")

    fake_response = '{"skill_name": "test-skill", "namespace": "personal", "kind": "procedural", "description": "测", "triggers": ["a","b","c"], "params_schema": [], "steps": [], "execute_code_segment": "print(1)", "output_schema": {}, "confidence": 0.5, "questions_for_user": []}'

    async def fake_call_llm(messages, **kw):
        return fake_response

    monkeypatch.setattr(aggregator, "call_llm", fake_call_llm)
    # P3.5.43: 阻止真 sync 到 ~/.hermes/skills/ (test 隔离). monkeypatch skill_sync.
    from catfish_tool_bridge.recmode import skill_sync
    monkeypatch.setattr(skill_sync, "HERMES_SKILLS_ROOT", tmp_path / "fake-hermes")
    # 测 draft_only=False 老行为 (直接落正式 skills)
    out = await aggregator.aggregate_session(
        sd, skills_root=tmp_path / "skills", draft_only=False,
    )
    assert out["skill_name"] == "test-skill"
    assert out["namespace"] == "personal"
    assert out["is_draft"] is False
    assert (tmp_path / "skills" / "personal" / "test-skill" / "SKILL.md").exists()
    # P3.5.43: draft_only=False 自动 sync 到 hermes
    assert (tmp_path / "fake-hermes" / "test-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_aggregate_session_draft_default(tmp_path, monkeypatch):
    """5/14 RecMode C: 默认 draft_only=True 落 session_dir/skill_draft/"""
    sd = tmp_path / "rec_draft"
    sd.mkdir()
    (sd / "events.jsonl").write_text('{"ts": 0, "kind": "click"}\n', encoding="utf-8")
    (sd / "meta.json").write_text('{"session_id": "rec_draft"}', encoding="utf-8")
    fake_response = '{"skill_name": "draft-x", "namespace": "personal", "kind": "procedural", "description": "d", "triggers": ["a","b","c"], "params_schema": [], "steps": [], "execute_code_segment": "", "output_schema": {}, "confidence": 0.5, "questions_for_user": []}'

    async def fake_call_llm(messages, **kw):
        return fake_response

    monkeypatch.setattr(aggregator, "call_llm", fake_call_llm)
    from catfish_tool_bridge.recmode import skill_sync
    fake_hermes = tmp_path / "fake-hermes"
    monkeypatch.setattr(skill_sync, "HERMES_SKILLS_ROOT", fake_hermes)
    out = await aggregator.aggregate_session(sd)  # 默认 draft_only=True
    assert out["is_draft"] is True
    # 落到 session_dir/skill_draft/personal/draft-x/
    assert (sd / "skill_draft" / "personal" / "draft-x" / "SKILL.md").exists()
    # P3.5.43: draft 不 sync 到 hermes
    assert not fake_hermes.exists() or not (fake_hermes / "draft-x").exists()


@pytest.mark.asyncio
async def test_call_llm_no_token_raises(monkeypatch):
    """没 CATFISH_DEV_TOKEN 也没传 auth_token → friendly RuntimeError"""
    monkeypatch.delenv("CATFISH_DEV_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="auth token"):
        await aggregator.call_llm([{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_call_llm_gateway_unreachable(monkeypatch):
    """gateway 不可达 → friendly RuntimeError"""
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "fake")
    with pytest.raises(RuntimeError, match="不可达"):
        await aggregator.call_llm(
            [{"role": "user", "content": "test"}],
            gateway_url="http://127.0.0.1:1",  # 几乎不可能在用的端口
            timeout_s=2.0,
        )
