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


# 8/15: 这条从 6/6 起挂着 xfail, 理由是「product code drift ... 留 BL 单 audit
# prefetch 真实行为, 同步 test 期望」—— 挂了 10 周没人回来处理。现在补上。
#
# 原断言错在两处:
#
#   1. **判据用裸子串**。断言 `"长期记忆" not in out` 会被静态段里出现的
#      "长期记忆" 这四个字命中 —— 那是纪律说明里的一句话, 不是数据段。
#      实测: 空数据时 "长期记忆" ✓ 但它**不在任何段标题里**。
#      改成按 `## ` 段标题判, 精确到段。
#
#   2. **"无数据"的语义变了**。时间感现在是无条件渲染的 (当前日期, 不依赖
#      session_meta.json), 所以它出现在空数据输出里是对的, 不是 drift。
#
# 实测空数据时 prefetch = 5114 字, 六个静态段 + 时间感; 三个真数据段不出现。
# 下面按这个真实契约重写。


def test_prefetch_empty_when_no_data(fake_catfish_home, provider):
    """init 了但 ~/.catfish/ 全空 → 只出静态段 + 时间感, 不出任何数据段。"""
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert out, "应该至少返回静态 section"

    headers = [ln for ln in out.splitlines() if ln.startswith("## ")]

    # 静态段: 不依赖 ~/.catfish 里有没有东西, 永远该在
    for must in ["员工身份与目的", "catfish memory schema", "memory 写入纪律",
                 "安全红线", "事实为准", "时间感"]:
        assert any(must in h for h in headers), f"静态段 {must!r} 不见了; 现有: {headers}"

    # 数据段: 没数据就不该出现。**按段标题判**, 不按裸子串 ——
    # 这几个词在静态段的正文里也出现, 用 `in out` 会假阳。
    for must_not in ["员工长期日记", "可用技能", "员工最近反馈",
                     "战略 / 设计 doc", "员工 wiki 已有", "近期流水"]:
        assert not any(must_not in h for h in headers), (
            f"没数据却渲染了 {must_not!r} 段; 现有: {headers}"
        )


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


# 548cd95 (8/6) 把 _render_employee_journal 从**二选一**改成**两段都注入**, 修的是
# 一个真事故:「早安里说的项目进度, 到工作台就不知道了」—— distilled_facts.md 24h
# 才蒸馏一次, 老实现读到它就 return, 于是最近一个蒸馏周期内写进 journal 的东西
# 主聊天一个字都看不到。
#
# 那个 commit **只改了 catfish_memory.py 一个文件** (git show --stat: 1 file changed),
# 没动这个测试文件 (最后一次改是 6/6 的 28b59cc)。所以下面这条从 8/6 起就是红的,
# 红了 9 天 —— 它断言的 "raw 日志 not in out" 正是被有意废掉的那个行为。
#
# 更要紧的是: 花了一次真事故换来的修复, 一条回归测试都没有。
# test_prefetch_both_layers_present 就是补它 —— 谁把逻辑改回二选一, 它立刻红。
# (2026-08-15 修)


def test_prefetch_employee_journal_distilled(fake_catfish_home, provider):
    """distilled_facts.md 与 journal 尾部**两段都注入**, 不是二选一。"""
    (fake_catfish_home / "distilled_facts.md").write_text(
        "员工偏好简洁回答 + 不喜欢长链思考", encoding="utf-8",
    )
    (fake_catfish_home / "employee_journal.md").write_text(
        "## 2026-08-06 15:11 近期流水\n\nISO 招投标进度", encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")
    assert "distilled" in out
    assert "员工偏好简洁回答" in out
    assert "ISO 招投标进度" in out, "近期流水被吞了 —— 又变回二选一了"


def test_prefetch_both_layers_present(fake_catfish_home, provider):
    """**8/6 事故的最小复现**: distilled 停在昨晚, 今天记的东西必须还能看到。

    两段各自带自己的小标题, 让 LLM 知道哪段更新。谁改回"读到 distilled 就
    return", 这条立刻红。
    """
    (fake_catfish_home / "distilled_facts.md").write_text(
        "员工长期画像: 做资质咨询", encoding="utf-8",
    )
    (fake_catfish_home / "employee_journal.md").write_text(
        "## 2026-08-06 15:11\n\nISO9001+45001 复审已排期\n", encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("hi")

    assert "员工长期记忆" in out, "缺长期段标题"
    assert "近期流水" in out, "缺近期段标题 —— 说明走了二选一那条路"
    assert "员工长期画像" in out and "ISO9001+45001" in out
    # 顺序: 长期在前, 近期在后 (近期标注"比上面的长期记忆更新")
    assert out.index("员工长期画像") < out.index("ISO9001+45001")


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


# ⚠ 下面两条 8/17 改过 query, 断言一个字没动。
#
# 它们钉的是**目录扫描** (找得到 SKILL.md / 跳过没有的), 原来用
# `prefetch("hi")` 只是图省事。8/17 给 skills_catalog 加了相关性闸门之后
# "hi" 会被正确折叠 (|q|=3, |∩|=1) —— 测试意图没问题, 是 query 选得不对:
# 拿一句跟被测 skill 毫无关系的话, 去验"这个 skill 渲染出来了"。
# 换成真能命中的 query, 扫描逻辑照测, 还顺带不跟闸门打架。


def test_prefetch_skills_catalog(fake_catfish_home, provider):
    """skills/<name>/SKILL.md 被发现 + 渲染"""
    skill_dir = fake_catfish_home / "skills" / "ppt-magazine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# ppt-magazine\n\n生成杂志式 PowerPoint", encoding="utf-8",
    )
    provider.initialize(session_id="s1")
    out = provider.prefetch("生成杂志式 PowerPoint")
    assert "可用技能" in out
    assert "ppt-magazine" in out


def test_prefetch_skills_catalog_skips_dirs_without_skill_md(fake_catfish_home, provider):
    """skills/<name>/ 没 SKILL.md → 跳过"""
    (fake_catfish_home / "skills" / "no-manifest").mkdir(parents=True)
    valid = fake_catfish_home / "skills" / "valid"
    valid.mkdir()
    (valid / "SKILL.md").write_text("valid skill", encoding="utf-8")
    provider.initialize(session_id="s1")
    out = provider.prefetch("valid skill")
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
