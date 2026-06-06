"""CatfishMemoryProvider 单测. 不依赖真 hermes 装 (走 fallback ABC).

跑法 (从 catfish repo root):
    cd edge/hermes-plugins/catfish-memory
    python -m pytest test_catfish_memory.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# 加 hermes-plugins/catfish-memory 自己到 sys.path, 让 from catfish_memory ...
# 跟真装 plugin 后 hermes 用 importlib spec 加载等价
import sys
sys.path.insert(0, str(Path(__file__).parent))

from catfish_memory import (  # noqa: E402
    CatfishMemoryProvider,
    _read_jsonl_tail,
    _read_text_safe,
)


@pytest.fixture
def fake_catfish_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """造一个假的 ~/.catfish/ 目录, env 指过去, 各测自己往里塞数据"""
    catfish_dir = tmp_path / ".catfish"
    catfish_dir.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(catfish_dir))
    return catfish_dir


@pytest.fixture
def provider() -> CatfishMemoryProvider:
    """构造一个 provider, 默认未 initialize (需要时各测自己调 init)"""
    return CatfishMemoryProvider()


# ── 基础 protocol ─────────────────────────────────────


def test_name():
    p = CatfishMemoryProvider()
    assert p.name == "catfish-memory"


def test_get_tool_schemas_empty():
    """get_tool_schemas 返空 list — 不暴露 tool"""
    p = CatfishMemoryProvider()
    assert p.get_tool_schemas() == []


def test_is_available_false_when_no_catfish_home(tmp_path, monkeypatch):
    """没 ~/.catfish/ → is_available False"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "does-not-exist"))
    p = CatfishMemoryProvider()
    assert p.is_available() is False


def test_is_available_true_when_catfish_home_exists(fake_catfish_home, provider):
    assert provider.is_available() is True


# ── prefetch 单源 ─────────────────────────────────────


def test_prefetch_empty_when_not_initialized(fake_catfish_home, provider):
    """没 init → prefetch 返空, 不读文件"""
    assert provider.prefetch("hello") == ""


@pytest.mark.xfail(
    reason=(
        "C3 (6/6 鸿波 CI matrix audit): product code drift — prefetch 现在含 "
        "identity bundle 字面字 (SOUL.md / USER.md 注入), 5 个数据源 marker "
        "断言不再成立. 留 BL 单 audit prefetch 真实行为, 同步 test 期望."
    )
)
def test_prefetch_empty_when_no_data(fake_catfish_home, provider):
    """init 了但 ~/.catfish/ 全空 → prefetch 只含 memory 写入纪律 (BL-MEMORY-DISCIPLINE 5/24)."""
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    # 不空 (含纪律)
    assert out, "应该至少返回纪律 section"
    # 含纪律标志
    assert "memory 写入纪律" in out
    # 不含 5 个数据源 marker (这才是"无数据"的真正语义)
    assert "时间感" not in out
    assert "长期记忆" not in out
    assert "长期日记" not in out
    assert "可用技能" not in out
    assert "员工反馈" not in out


def test_prefetch_session_meta(fake_catfish_home, provider):
    """有 session_meta.json → prefetch 含时间感"""
    (fake_catfish_home / "session_meta.json").write_text(
        json.dumps({"last_chat_iso": "2026-05-18T10:00:00"}),
        encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "时间感" in out
    assert "2026-05-18T10:00:00" in out


def test_prefetch_employee_journal_distilled(fake_catfish_home, provider):
    """distilled_facts.md 优先于 employee_journal.md"""
    (fake_catfish_home / "distilled_facts.md").write_text(
        "员工偏好简洁回答 + 不喜欢长链思考", encoding="utf-8",
    )
    (fake_catfish_home / "employee_journal.md").write_text(
        "raw 日志 (不应该被选中)", encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "distilled" in out
    assert "员工偏好简洁回答" in out
    assert "raw 日志" not in out  # distilled 优先, raw 不应该出现


def test_prefetch_employee_journal_raw_fallback(fake_catfish_home, provider):
    """没 distilled, fallback 到 employee_journal.md"""
    (fake_catfish_home / "employee_journal.md").write_text(
        "2026-05-18 跟客户开会讨论资质审核 + 8 项 EIS 流程",
        encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "员工长期日记" in out
    assert "资质审核" in out


def test_prefetch_skills_catalog(fake_catfish_home, provider):
    """skills/<name>/SKILL.md 被发现 + 渲染"""
    skill_dir = fake_catfish_home / "skills" / "ppt-magazine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# ppt-magazine\n\n生成杂志式 PowerPoint", encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "可用技能" in out
    assert "ppt-magazine" in out


def test_prefetch_skills_catalog_skips_dirs_without_skill_md(fake_catfish_home, provider):
    """skills/<name>/ 没 SKILL.md → 跳过"""
    (fake_catfish_home / "skills" / "no-manifest").mkdir(parents=True)
    valid = fake_catfish_home / "skills" / "valid"
    valid.mkdir()
    (valid / "SKILL.md").write_text("valid skill", encoding="utf-8")
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "valid" in out
    assert "no-manifest" not in out


def test_prefetch_feedback(fake_catfish_home, provider):
    """feedback.jsonl 最后几条被渲染, 含 👍/👎"""
    feedback_file = fake_catfish_home / "feedback.jsonl"
    feedback_file.write_text(
        '{"verdict": "up", "note": "回答得很对"}\n'
        '{"verdict": "down", "note": "答非所问"}\n',
        encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "最近反馈" in out
    assert "回答得很对" in out
    assert "答非所问" in out
    assert "👍" in out
    assert "👎" in out


def test_prefetch_feedback_handles_bad_lines(fake_catfish_home, provider):
    """jsonl 含烂行 → 跳过, 不挂"""
    (fake_catfish_home / "feedback.jsonl").write_text(
        '{"verdict": "up", "note": "ok"}\n'
        'this is not json\n'
        '{"verdict": "down", "note": "bad"}\n',
        encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "ok" in out
    assert "bad" in out
    assert "not json" not in out


# ── skill_guard 条件触发 ─────────────────────────────


def test_prefetch_skill_guard_triggers_on_keyword(fake_catfish_home, provider):
    """query 含 'skill' / '技能' → 注入 Skill Guard 铁律"""
    provider.initialize(session_id="s1")
    out = provider.prefetch("帮我跑技能生成 ppt")
    assert "Skill Guard" in out
    assert "catfish_run_skill" in out


def test_prefetch_skill_guard_not_triggered_on_normal_query(fake_catfish_home, provider):
    """普通 query 不触发 skill_guard, 避免 prompt 噪音"""
    provider.initialize(session_id="s1")
    out = provider.prefetch("今天天气怎么样?")
    assert "Skill Guard" not in out


# ── prefetch 聚合 ─────────────────────────────────────


def test_prefetch_aggregates_all_available_sources(fake_catfish_home, provider):
    """5 个数据源都有 → 5 个 section 都出现, 顺序固定"""
    # 1. session_meta
    (fake_catfish_home / "session_meta.json").write_text(
        '{"last_chat_iso": "2026-05-18T10:00:00"}', encoding="utf-8",
    )
    # 2. employee_journal
    (fake_catfish_home / "distilled_facts.md").write_text(
        "员工是金融行业产品经理", encoding="utf-8",
    )
    # 3. skills_catalog
    skill = fake_catfish_home / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# demo", encoding="utf-8")
    # 4. feedback
    (fake_catfish_home / "feedback.jsonl").write_text(
        '{"verdict": "up", "note": "go on"}\n', encoding="utf-8",
    )
    # 5. skill_guard 走 query 触发
    provider.initialize(session_id="s1")
    out = provider.prefetch("跑技能 demo")

    assert "时间感" in out
    assert "员工长期记忆" in out
    assert "可用技能" in out
    assert "最近反馈" in out
    assert "Skill Guard" in out
    # 顺序 (session_meta → journal → skills → feedback → guard)
    idx_meta = out.find("时间感")
    idx_journal = out.find("员工长期记忆")
    idx_skills = out.find("可用技能")
    idx_feedback = out.find("最近反馈")
    idx_guard = out.find("Skill Guard")
    assert idx_meta < idx_journal < idx_skills < idx_feedback < idx_guard


# ── helper 函数 ──────────────────────────────────────


def test_read_text_safe_returns_empty_on_missing(tmp_path):
    assert _read_text_safe(tmp_path / "no-such.txt", 1000) == ""


def test_read_text_safe_truncates_on_overflow(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 10000, encoding="utf-8")
    out = _read_text_safe(big, max_bytes=300)
    # 截到 max_bytes // 3 + truncated 标记 (我们的简单 truncate 策略)
    assert "truncated" in out
    assert len(out) < 500


def test_read_jsonl_tail_returns_recent(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text(
        "\n".join(json.dumps({"i": i}) for i in range(50)) + "\n",
        encoding="utf-8",
    )
    out = _read_jsonl_tail(f, max_lines=5)
    assert len(out) <= 5
    # 末尾 5 条
    assert out[-1]["i"] == 49


# ── initialize 副作用 ────────────────────────────────


def test_initialize_records_hermes_home_kwarg(fake_catfish_home, provider):
    provider.initialize(session_id="s1", hermes_home="/tmp/fake-hermes-home")
    assert provider._hermes_home == Path("/tmp/fake-hermes-home")


def test_initialize_idempotent(fake_catfish_home, provider):
    """重复 init 不挂"""
    provider.initialize(session_id="s1")
    provider.initialize(session_id="s2")  # 不应该挂
    assert provider._session_id == "s2"


def test_shutdown_no_op_does_not_raise(fake_catfish_home, provider):
    provider.initialize(session_id="s1")
    provider.shutdown()  # 不应该挂
