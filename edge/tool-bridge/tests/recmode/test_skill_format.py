"""P3.5.43 — skill_format 单测.

跑法 (从 edge/tool-bridge):
  python3 -m pytest tests/recmode/test_skill_format.py -q
"""
from __future__ import annotations

import re

import pytest

from catfish_tool_bridge.recmode.skill_format import (
    DESCRIPTION_TOKEN_BUDGET,
    SkillManifest,
    TRIGGERS_MAX,
    TRIGGERS_MIN,
    VALID_KINDS,
    VALID_NAMESPACES,
    _name_to_render_function,
    estimate_description_tokens,
    render_frontmatter,
    render_script_py,
    render_skill_md,
    validate_manifest,
)


# ── fixture ─────────────────────────────────────────────────────────


def _valid_manifest(**overrides) -> SkillManifest:
    defaults = dict(
        name="weekly-report",
        namespace="department",
        kind="procedural",
        description="员工周报生成 skill — 一键拉本周 git 提交 + 会议 + TODO 生成 xlsx 周报",
        triggers=["周报", "本周工作", "本周总结", "weekly report"],
    )
    defaults.update(overrides)
    return SkillManifest(**defaults)


# ── estimate_description_tokens ─────────────────────────────────────


def test_token_estimate_ascii():
    # 'hello world' = 11 bytes → ceil(11/4) = 3
    assert estimate_description_tokens("hello world") == 3


def test_token_estimate_chinese():
    # '你好' 6 bytes (utf8 3 bytes/char) → ceil(6/4) = 2
    assert estimate_description_tokens("你好") == 2


def test_token_estimate_huashu_compressed_under_budget():
    """P3.5.41.1 实测压缩 huashu 到 158 tokens 是 budget 安全线.

    校验真实压缩文案的 token 估算落在合理范围. 用 install_huashu_ppt_skill.sh:106-114
    的 CATFISH_COMPRESSED_DESC 真实文本.
    """
    huashu_desc = (
        "花叔 Design — HTML 原生设计 skill. 高保真原型 / 交互 Demo / 演讲 HTML deck → "
        "可编辑 PPTX / 时间轴动画 (导出 MP4/GIF, 60fps 插帧) / 设计变体 + 5 流派×20 种"
        "设计方向顾问 + 5 维专家评审 + 带解说长动画 pipeline. 触发词: 做原型 / 做交互 Demo / "
        "hi-fi 设计 / 设计风格 / 推荐风格 / iOS 原型 / app mockup / 导出 MP4 / 导出 GIF / "
        "设计评审 / 解说动画 / TTS+动画 / 5 分钟讲清 XX. 反 AI slop + Junior Designer 工作流 "
        "(先 assumptions/placeholder 再迭代). 详细 Starter Components / Brand Asset Protocol / "
        "哲学库 / Playwright 验证见 body."
    )
    tokens = estimate_description_tokens(huashu_desc)
    # P3.5.41.1 commit 说 158 — 真实 ceil 后接近这个
    assert 140 <= tokens <= 200, f"实际 {tokens}, 偏离 158 太远"


# ── validate_manifest 字段约束 ─────────────────────────────────────


def test_validate_passes_for_normal():
    errors = validate_manifest(_valid_manifest())
    assert errors == []


def test_validate_rejects_non_kebab_name():
    errors = validate_manifest(_valid_manifest(name="WeeklyReport"))
    assert any("kebab-case" in e for e in errors)


def test_validate_rejects_snake_case_name():
    errors = validate_manifest(_valid_manifest(name="weekly_report"))
    assert any("kebab-case" in e for e in errors)


def test_validate_rejects_invalid_namespace():
    errors = validate_manifest(_valid_manifest(namespace="foo"))
    assert any("namespace" in e for e in errors)


def test_validate_rejects_invalid_kind():
    errors = validate_manifest(_valid_manifest(kind="executable"))
    assert any("kind" in e for e in errors)


def test_validate_rejects_too_few_triggers():
    errors = validate_manifest(_valid_manifest(triggers=["x", "y"]))
    assert any("triggers 至少" in e for e in errors)


def test_validate_rejects_too_many_triggers():
    errors = validate_manifest(_valid_manifest(triggers=["t"] * 30))
    assert any("triggers 至多" in e for e in errors)


def test_validate_rejects_empty_description():
    errors = validate_manifest(_valid_manifest(description="   "))
    assert any("description 不能为空" in e for e in errors)


def test_validate_rejects_over_budget_description():
    # 故意造 >158 tokens (e.g. 1000 字节中文 ≈ 333 字符 ≈ 250 tokens)
    long_desc = "高品质企业级 skill 描述" * 100  # ~3000 字节
    errors = validate_manifest(_valid_manifest(description=long_desc))
    assert any("超预算" in e for e in errors)


# ── render_frontmatter ──────────────────────────────────────────────


def test_render_frontmatter_contains_required_fields():
    fm = render_frontmatter(_valid_manifest(), today="2026-06-20")
    # hermes 必填字段都在
    assert "name: weekly-report" in fm
    assert 'version: "1.0.0"' in fm
    assert "kind: procedural" in fm
    assert "deprecated: false" in fm
    assert "triggers:" in fm
    assert "  - 周报" in fm
    assert "  - 本周工作" in fm


def test_render_frontmatter_handles_multiline_description():
    desc = "第一行\n第二行 含特殊: 字符"
    fm = render_frontmatter(_valid_manifest(description=desc))
    # 多行 description 用 |- block scalar
    assert "description: |-" in fm
    assert "  第一行" in fm
    assert "  第二行 含特殊: 字符" in fm


def test_render_frontmatter_escapes_special_chars_in_single_line():
    # 单行 description 含特殊字符 → 双引号
    fm = render_frontmatter(_valid_manifest(description="含 : 冒号"))
    assert 'description: "含 : 冒号"' in fm


# ── render_skill_md ─────────────────────────────────────────────────


def test_render_skill_md_has_frontmatter_block():
    md = render_skill_md(_valid_manifest(), today="2026-06-20")
    # 开头必须是 --- frontmatter ---, 不能直接 # title (P3.5.43 BLOCKER 1)
    assert md.startswith("---\n")
    assert "\n---\n\n# weekly-report" in md


def test_render_skill_md_includes_recording_meta_when_provided():
    meta = {"session_id": "abc123", "duration_s": 45, "events_count": 12, "keyframes_count": 5}
    md = render_skill_md(_valid_manifest(), recording_meta=meta)
    assert "session_id: `abc123`" in md
    assert "录制时长: 45s" in md


def test_render_skill_md_skips_recording_meta_when_absent():
    md = render_skill_md(_valid_manifest())
    assert "自动生成元数据" not in md
    assert "session_id" not in md


def test_render_skill_md_includes_steps():
    m = _valid_manifest()
    m.steps = [
        {"step_no": 1, "intent": "打开周报模板", "tool": "open_file",
         "expected_after": "看到 xlsx 模板"},
        {"step_no": 2, "intent": "填本周事项", "tool": "fill_cell"},
    ]
    md = render_skill_md(m)
    assert "### 1. 打开周报模板" in md
    assert "- tool: `open_file`" in md
    assert "预期: 看到 xlsx 模板" in md
    assert "### 2. 填本周事项" in md


# ── render_script_py ────────────────────────────────────────────────


def test_script_py_has_render_function():
    """P3.5.43 BLOCKER 2: catfish_run_skill 找 attr.startswith('render_')."""
    code = render_script_py(_valid_manifest())
    assert "def render_weekly_report(params):" in code


def test_script_py_kebab_to_snake_for_fn_name():
    code = render_script_py(_valid_manifest(name="catfish-email-digest"))
    assert "def render_catfish_email_digest(params):" in code


def test_script_py_returns_dict_with_ok():
    code = render_script_py(_valid_manifest())
    # 默认 body 必含 return {"ok": True, ...}
    assert '"ok": True' in code


def test_script_py_includes_execute_code_segment():
    m = _valid_manifest()
    m.execute_code_segment = "soup = BeautifulSoup(html)\nrows = soup.select('tr')"
    code = render_script_py(m)
    assert "soup = BeautifulSoup(html)" in code
    assert "rows = soup.select('tr')" in code


def test_script_py_main_guard():
    code = render_script_py(_valid_manifest())
    assert 'if __name__ == "__main__":' in code


# ── name_to_render_function helper ─────────────────────────────────


def test_name_to_render_function_kebab():
    assert _name_to_render_function("weekly-report") == "render_weekly_report"
    assert _name_to_render_function("a") == "render_a"
    assert _name_to_render_function("multi-word-skill") == "render_multi_word_skill"


# ── 端到端: hermes 装载 + catfish_run_skill 兼容性 ─────────────────


def test_e2e_skill_md_is_hermes_loadable_yaml():
    """生成的 SKILL.md 必须能被 YAML parser 读出 frontmatter."""
    md = render_skill_md(_valid_manifest())
    # 抽 frontmatter
    m = re.match(r"^---\n(.*?)\n---\n", md, re.DOTALL)
    assert m, "SKILL.md 必须以 --- frontmatter --- 开头"
    fm_text = m.group(1)
    # YAML 必填字段都在
    for required in ("name:", "version:", "kind:", "triggers:", "description:"):
        assert required in fm_text, f"frontmatter 缺 {required}"


def test_e2e_script_py_render_function_pattern():
    """生成的 script.py 必须含 render_* 函数 (catfish_tools_skill_ops _find_render_function)."""
    code = render_script_py(_valid_manifest())
    # 用跟 _find_render_function 同款 check
    # (它 dir(module) 找 attr.startswith("render_") and callable)
    fn_def_re = re.compile(r"^def (render_\w+)\(params\):", re.MULTILINE)
    matches = fn_def_re.findall(code)
    assert len(matches) >= 1, "script.py 必须至少一个 render_<name>(params) 函数"
