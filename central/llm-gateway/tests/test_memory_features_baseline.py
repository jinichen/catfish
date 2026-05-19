"""BL-GATEWAY-CLEANUP-POST-HERMES Week 2 — baseline integration test.

# 目的 (鸿波 5/19 prep)

这份测试是**当前 gateway memory 写路径行为的 baseline 锁**. Week 2 把
session_summarizer / memory_distill / employee_journal 这 3 个模块搬到
catfish-memory hermes plugin 后, **同一份测试跑 plugin 应该仍然 pass** —
不 pass 说明迁移**不等价**, 需要 debug 差距.

# 覆盖范围 (跟 Week 2 迁移设计 1-for-1)

1. session summary → `append_to_journal()` 真写到 employee_journal.md
   - 格式: `## YYYY-MM-DD HH:MM · session …xxxxxx\n\n<summary 正文>\n`
2. `inject_employee_journal()` (当前 gateway 注入路径) 把 distilled + journal 真注入
3. 空 journal + 空 distilled → noop (跟 BL-EMPLOYEE-JOURNAL-EMPTY-NOOP 配套)
4. `maybe_run_llm_distillation()` mock LLM 后真写 distilled_facts.md
5. `append_to_journal()` 追加格式 (分隔 + 段头 + bytes)
6. `summarize_one_session()` 端到端 (mock LLM + mock state.db read_session_messages)
   → 写 journal + mark journaled

# 设计原则

- **全 mock LLM** (httpx.AsyncClient.post), 不实盘, 不依赖网络
- **全用 tmp_path / monkeypatch**, 不动 ~/.catfish/ 真文件
- **不依赖 hermes** (跟 gateway 当前实现一致, 不预设 catfish-memory plugin)
- **assert 行为 not 实现** — 测的是"journal 文件有这段内容", 不是"内部调了某函数"

# Week 2 迁移后该怎么用

Step C 完成后 (gateway env gate 关掉, plugin on_session_end 接管):
1. 重新跑这 6 个 test
2. 如果直接跑 `pytest tests/test_memory_features_baseline.py -v` 全 pass → 迁移等价 ✓
3. 如果有 case 挂 → 看 assert 哪里, 排查 plugin 行为是不是漏了哪一步

# 跑法 (从 gateway repo)

    cd central/llm-gateway
    python -m pytest tests/test_memory_features_baseline.py -v

# 不在覆盖范围 (留 Week 3 / 其它 sprint)

- a2a_journal_hook 的 [a2a-help] entry 格式 — 这是跨员工协作, 单独 sprint 设计
  hermes 写入通道, 不在 Week 2 范围
- session title 写回 state.db (`_update_session_title`) — open question 待鸿波拍
- distillation 多 chunk 跨 chunk dedup 细节 — 已有 test_memory_distill_live 覆盖
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from catfish_gateway import memory_distill, session_summarizer
from catfish_gateway.employee_journal import (
    append_to_journal,
    inject_employee_journal,
    read_journal,
)


# ─────────────────────────────────────────────────────────
# 公共桩 / fixture
# ─────────────────────────────────────────────────────────


@pytest.fixture
def fake_catfish_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """造一个假 ~/.catfish/, monkeypatch employee_journal / memory_distill 全部
    指过去. 测试之间互不污染.

    返 catfish_home Path, 各测可以直接 (home / "employee_journal.md").write_text(...)
    塞数据.
    """
    catfish_dir = tmp_path / ".catfish"
    catfish_dir.mkdir()
    journal_path = catfish_dir / "employee_journal.md"
    distilled_path = catfish_dir / "distilled_facts.md"
    state_path = catfish_dir / "memory_distill_state.json"
    journaled_marker = catfish_dir / "journaled_sessions.txt"

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)
    monkeypatch.setattr(memory_distill, "DISTILL_STATE_PATH", state_path)
    monkeypatch.setattr(session_summarizer, "JOURNALED_MARKER", journaled_marker)

    return catfish_dir


def _mock_httpx_response(content: str, status: int = 200) -> MagicMock:
    """造一个假 httpx.Response, 返一个 OpenAI 兼容 choices[0].message.content."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = ""
    resp.json = MagicMock(return_value={
        "choices": [{"message": {"content": content}}],
    })
    return resp


def _patch_user_model_resolver(monkeypatch: pytest.MonkeyPatch, *, model_name: str = "test-model"):
    """BL-INTERNAL-MODEL-FOLLOW-USER-DISTILL — distill 严格走 user_model_resolver.
    测试桩返一个 fake model 让 distill 不 skip.
    """
    fake_model = SimpleNamespace(
        name=model_name,
        mode="chat",
        upstream=SimpleNamespace(is_available=True),
    )
    monkeypatch.setattr(
        "catfish_gateway.user_model_resolver.get_user_last_session_model",
        lambda email: model_name,
    )
    monkeypatch.setattr(
        "catfish_gateway.user_model_resolver.resolve_model_obj",
        lambda name, config: fake_model if name == model_name else None,
    )
    monkeypatch.setattr(
        "catfish_gateway.config.load_config",
        lambda: SimpleNamespace(models=[fake_model]),
    )
    return fake_model


# ─────────────────────────────────────────────────────────
# Test 1: session summary 真写到 employee_journal
# ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_baseline_session_summary_writes_journal(
    fake_catfish_home: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """端到端: summarize_one_session(sid, started_at, msg_count) →
    LLM (mock) 返"### 主题 + 正文" → append_to_journal → 文件真有这段.

    迁移后等价行为:
      hermes on_session_end(messages) → catfish-memory plugin 内部 LLM 调用 →
      _append_to_journal() → 同一个 employee_journal.md 真有这段.

    assert 文件内容 (not 实现细节), 这样测试跨实现可移植.
    """
    journal_path = fake_catfish_home / "employee_journal.md"
    # Mock state.db: _read_session_messages 返几条假对话
    monkeypatch.setattr(
        session_summarizer,
        "_read_session_messages",
        lambda sid: [
            ("user", "帮我写一份资质周报"),
            ("assistant", "好的, 我看下上次格式"),
            ("user", "用 4 段, 合规/资质/安全/其它"),
        ],
    )
    # Mock _read_session_model 返一个 origin_model
    monkeypatch.setattr(
        session_summarizer, "_read_session_model", lambda sid: "test-model",
    )
    # Mock _update_session_title (state.db 写, 测试不该真写)
    monkeypatch.setattr(
        session_summarizer, "_update_session_title", lambda sid, title: True,
    )
    # Patch catalog 让 _summarize_with_llm 找得到 model
    fake_model = SimpleNamespace(
        name="test-model",
        mode="chat",
        upstream=SimpleNamespace(is_available=True),
    )
    monkeypatch.setattr(
        "catfish_gateway.config.load_config",
        lambda: SimpleNamespace(models=[fake_model]),
    )
    # Mock dev token (避免真 mint)
    monkeypatch.setattr(
        "catfish_gateway.auth.dev_token.ensure_internal_dev_token",
        lambda: "fake-internal-token",
    )
    # Mock httpx LLM 调用 → 返一段 markdown summary
    fake_summary = "### 资质周报第 19 版\n员工要写资质周报, 用 4 段格式, 合规/资质/安全/其它."
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_httpx_response(fake_summary))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # 清掉 session 锁 / cooldown 防干扰
    session_summarizer._SESSION_COOL_DOWN.clear()
    session_summarizer._CURRENTLY_SUMMARIZING.clear()

    started_at = time.time() - 3600  # 1 小时前
    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await session_summarizer.summarize_one_session(
            "sess_baseline_001", started_at, 3,
        )

    assert ok is True, "summarize_one_session 应该返 True"
    assert journal_path.exists(), "journal 文件应该被创建"
    content = journal_path.read_text(encoding="utf-8")
    # gateway 加的日期前缀
    assert "## " in content, "应该有 ## 二级段头 (日期分隔)"
    assert "session" in content, "应该带 session id 后缀"
    # LLM 返的主题正文
    assert "资质周报第 19 版" in content
    assert "员工要写资质周报" in content
    # journaled marker 也写了 (防重复 summarize)
    assert (fake_catfish_home / "journaled_sessions.txt").exists()


# ─────────────────────────────────────────────────────────
# Test 2: inject_employee_journal 把 distilled + journal 真注入
# ─────────────────────────────────────────────────────────


def test_baseline_inject_employee_journal_loads_distilled_and_journal(
    fake_catfish_home: Path,
):
    """gateway 当前注入路径 — inject_employee_journal() 看到 distilled +
    journal 都存在时, 应该把内容注入到 system message 末尾.

    迁移后等价行为:
      hermes 调 catfish-memory.prefetch(query) → 返同样内容 → hermes
      把它注入 system. 内容应该包含"长期事实" + journal 段.

    本测试 assert 内容能找到 (能 substring 命中), 不 assert 具体 markdown
    格式 — Week 2 迁移到 plugin 后渲染 wrapper 可能微调 (e.g.
    "员工长期日记 (gateway 自动注入, 两段式)" 改成 "员工长期记忆 (catfish)").
    跨实现可读, 不挂.
    """
    journal_path = fake_catfish_home / "employee_journal.md"
    journal_path.write_text(
        "## 2026-05-15 14:00 · session …abc123\n\n"
        "### 资质周报\n员工要写本周资质周报, 4 段格式.\n",
        encoding="utf-8",
    )
    distilled_path = fake_catfish_home / "distilled_facts.md"
    distilled_path.write_text(
        "- 老板: 张总, 部门长, 偏好简短数字明确报告\n"
        "- 项目: EIS 资质管理 (4 段周报)\n",
        encoding="utf-8",
    )

    msgs = [{"role": "system", "content": "原 system prompt"}]
    out = inject_employee_journal(msgs)
    assert len(out) == 1
    content = out[0]["content"]
    # 原 system 还在
    assert "原 system prompt" in content
    # distilled 内容到了
    assert "老板: 张总" in content
    assert "EIS 资质管理" in content
    # journal 内容也到了
    assert "资质周报" in content
    assert "4 段格式" in content


# ─────────────────────────────────────────────────────────
# Test 3: 空 journal + 空 distilled → noop
# ─────────────────────────────────────────────────────────


def test_baseline_inject_employee_journal_empty_noop(
    fake_catfish_home: Path,
):
    """空 journal + 空 distilled → inject 不动 messages (原样返).

    BL-EMPLOYEE-JOURNAL-EMPTY-NOOP (task #15): 别空 journal 还塞个 header
    占 system prompt. 这个测试 lock 当前 noop 行为.

    迁移后 plugin.prefetch() 应该返空字符串 → hermes 自然不注入.
    """
    # journal / distilled 都不创建
    msgs = [{"role": "system", "content": "原"}]
    out = inject_employee_journal(msgs)
    assert out == msgs, "空 journal 时 inject 应该原样返"


# ─────────────────────────────────────────────────────────
# Test 4: maybe_run_llm_distillation 真写 distilled_facts.md
# ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_baseline_memory_distill_writes_distilled_facts(
    fake_catfish_home: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """造一个 > 5KB 的 journal → maybe_run_llm_distillation (mock LLM) →
    distilled_facts.md 真被写, 含 LLM 返的 bullet 事实.

    迁移后等价: plugin 在 on_session_end 末尾按计数触发 distill, 调
    hermes-internal LLM, 写同一个 distilled_facts.md.
    """
    journal_path = fake_catfish_home / "employee_journal.md"
    # 凑 > INJECT_MAX_BYTES (5KB) 让 only_old_segments 有内容蒸馏
    big_journal = "\n\n".join(
        f"## 2026-05-{i:02d} 10:00 · session …test{i:03d}\n\n"
        f"### 第 {i} 段主题\n这是第 {i} 段内容. " + "x" * 200
        for i in range(1, 50)
    )
    journal_path.write_text(big_journal, encoding="utf-8")
    distilled_path = fake_catfish_home / "distilled_facts.md"

    monkeypatch.setattr(memory_distill, "DISTILL_THRESHOLD", 1)
    _patch_user_model_resolver(monkeypatch)
    # Mock LLM 调用返一段 bullet 蒸馏
    fake_bullets = "- 老板: 张总\n- 项目: EIS 资质管理 (4 段周报)\n- 偏好: 简短数字明确"
    monkeypatch.setattr(
        memory_distill, "_llm_distill_chunk",
        AsyncMock(return_value=fake_bullets),
    )

    result = await memory_distill.maybe_run_llm_distillation(
        user_email="test@example.com",
    )
    assert result is not None, "应该返写到的文本"
    assert distilled_path.exists(), "distilled_facts.md 应该被创建"
    written = distilled_path.read_text(encoding="utf-8")
    # LLM 返的 bullet 都到了
    assert "老板: 张总" in written
    assert "EIS 资质管理" in written
    assert "偏好: 简短数字明确" in written
    # state 文件也被 mark (防 24h 重跑)
    state_path = fake_catfish_home / "memory_distill_state.json"
    assert state_path.exists()


# ─────────────────────────────────────────────────────────
# Test 5: append_to_journal 追加格式
# ─────────────────────────────────────────────────────────


def test_baseline_append_to_journal_format(
    fake_catfish_home: Path,
):
    """append_to_journal(entry) 应该:
      - 自动 strip + 加 \\n\\n 后缀做段分隔
      - 累 append 不破坏前段
      - read_journal() 能读回 (for_injection=False 时拿全文)

    迁移后等价: plugin 内部用同样的 _append_to_journal 写, a2a_journal_hook
    继续走 gateway 这个 (Week 3 才搬).
    """
    journal_path = fake_catfish_home / "employee_journal.md"

    append_to_journal("## 2026-05-15 10:00 · session …aaa\n\n### 段 A\n内容 A")
    append_to_journal("## 2026-05-15 11:00 · session …bbb\n\n### 段 B\n内容 B")

    assert journal_path.exists()
    full = read_journal(for_injection=False)
    # 两段都在, 段 A 在前
    a_idx = full.find("段 A")
    b_idx = full.find("段 B")
    assert a_idx >= 0, "段 A 必须存在"
    assert b_idx > a_idx, "段 B 必须在段 A 后"
    # 段分隔 (两段之间有空行)
    between = full[a_idx:b_idx]
    assert "\n\n" in between, "段之间应该有空行分隔"


# ─────────────────────────────────────────────────────────
# Test 6: summarize_one_session 短 session / 没消息 → skip
# ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_baseline_summarize_skips_when_no_messages(
    fake_catfish_home: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """没消息可读 (state.db 没数据) → summarize_one_session 应该:
      - 返 False (不写 journal)
      - mark journaled (防再扫到这个永远无消息的 session)

    迁移后等价: plugin on_session_end 收到 empty messages list, 应该早返
    no-op, 不调 LLM 不写文件.
    """
    journal_path = fake_catfish_home / "employee_journal.md"
    monkeypatch.setattr(
        session_summarizer, "_read_session_messages", lambda sid: [],
    )
    session_summarizer._SESSION_COOL_DOWN.clear()
    session_summarizer._CURRENTLY_SUMMARIZING.clear()

    ok = await session_summarizer.summarize_one_session(
        "sess_empty", time.time() - 3600, 0,
    )
    assert ok is False, "没消息时 summarize 应该返 False"
    # journal 不应被创建 (没内容可写)
    assert not journal_path.exists(), "没消息时不该写 journal"
    # 但 marker 写了 (防再扫)
    assert (fake_catfish_home / "journaled_sessions.txt").exists()
