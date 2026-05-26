"""proactive.generate_starter 行为回归 (5/26 BL-PROACTIVE-DECOUPLE 后).

# 5/26 重大变化 (BL-PROACTIVE-DECOUPLE)

老 generate_starter 自己读员工本机 `~/.catfish/employee_journal.md` + hermes      # noqa: BOUNDARY
state.db 拿最近 session model. 5/26 audit 砍 — gateway 不读员工 fs.

Companion 调 /api/proactive/starter 时把 `journal_tail` (Companion 自己读完
裁切到 8KB) + `model_name` (Companion 从 hermes state.db 拿) 当 body 字段传过来.
gateway 不再自读.

  - 没传 journal_tail / model_name → 立即返 fallback 模板, 不打 LLM
  - 都传了 → 走 LLM, 失败 fallback (维持 BL-INTERNAL-MODEL-FOLLOW-USER 1 candidate 行为)

# 这个 test 覆盖什么 (5/26 重写后)

  - Companion 没传 model_name → fallback (gateway 不再自查 state.db)
  - Companion 没传 journal_tail → fallback
  - 都传了 + 200 → source=llm, starter 是 LLM 文本
  - 都传了 + model 不可达 → fallback (resolve_model_obj 返 None)
  - 都传了 + 502 → fallback (严格 1 candidate)
  - 都传了 + 429 → fallback (严格 1 candidate, 不偷消耗别 model 配额)
  - 都传了 + 401 → fallback
  - 200 但 content + reasoning_content 都空 → fallback
  - DeepSeek thinking mode content="" reasoning_content 有 → 用 reasoning
  - content 优先于 reasoning_content
  - 请求里 model 字段是员工传过来的 model_name (防硬编码回归)

# 历史背景 (供 git blame 追溯)

  - BL-F19 (5/5): 老多候选 fallback chain. BL-INTERNAL-MODEL-FOLLOW-USER (5/17)
    拍板严格 1 candidate. 5/26 BL-PROACTIVE-DECOUPLE 在此基础上把 model_name
    /journal_tail 的来源从 "gateway 自己读" 改成 "Companion 透传".
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from catfish_gateway import proactive


# ── 公共桩 ─────────────────────────────────────────────


class _MockResp:
    def __init__(self, status_code: int, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self.text = ""
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


def _ok_resp(text: str = "今天进展如何, 卡哪了?") -> _MockResp:
    return _MockResp(
        200,
        {"choices": [{"message": {"role": "assistant", "content": text}}]},
    )


def _patch_model_resolve(monkeypatch, *, model_name: str = "test-model",
                         available: bool = True):
    """patch resolve_model_obj — 返指定 model 对象 (available=False 模拟不可达).

    5/26 后: gateway 不再调 get_user_last_session_model (那是 stub 抛 RuntimeError).
    只 patch resolve_model_obj + load_config.
    """
    fake_model = SimpleNamespace(
        name=model_name,
        mode="chat",
        upstream=SimpleNamespace(is_available=available),
    )
    monkeypatch.setattr(
        "catfish_gateway.user_model_resolver.resolve_model_obj",
        lambda name, config: fake_model if (name == model_name and available) else None,
    )
    monkeypatch.setattr(
        "catfish_gateway.config.load_config",
        lambda: SimpleNamespace(models=[fake_model]),
    )
    return fake_model


def _patch_httpx(monkeypatch, post_handler):
    """让 httpx.AsyncClient().post() 走 post_handler (call_idx -> _MockResp)."""
    call_count = {"n": 0}

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **kw):
            call_count["n"] += 1
            return post_handler(call_count["n"])

    import httpx  # noqa: PLC0415
    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    return call_count


# ── 5/26 新行为: 没传 journal_tail/model_name → fallback ────────


@pytest.mark.asyncio
async def test_no_journal_tail_returns_fallback(monkeypatch):
    """Companion 没传 journal_tail body 字段 → fallback (gateway 不自读 fs)."""
    call_count = _patch_httpx(monkeypatch, lambda n: _ok_resp("不该被调"))

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="",  # 空 → fallback
        model_name="catfish-private-main",
    )
    assert result["source"] == "fallback"
    assert call_count["n"] == 0  # LLM 没被调
    assert "Companion 没传" in result["context_hint"]


@pytest.mark.asyncio
async def test_no_model_name_returns_fallback(monkeypatch):
    """Companion 没传 model_name → fallback (gateway 不再自查 state.db)."""
    call_count = _patch_httpx(monkeypatch, lambda n: _ok_resp("不该被调"))

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# 今天的工作\n- 改 proactive",
        model_name=None,
    )
    assert result["source"] == "fallback"
    assert call_count["n"] == 0


# ── 正常路径: 都传了 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_returns_llm_source(monkeypatch):
    """journal_tail + model_name 都传 + 200 → source=llm."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _ok_resp("hello from llm"))

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# 今天\n- 改 proactive",
        model_name="catfish-private-main",
    )

    assert result["source"] == "llm"
    assert "hello from llm" in result["starter"]
    assert call_count["n"] == 1  # 严格 1 candidate


# ── Fallback 路径 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_unavailable_returns_fallback(monkeypatch):
    """传了 model_name 但 resolve_model_obj 返 None (model 不在 config 或挂) → fallback."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main", available=False)
    call_count = _patch_httpx(monkeypatch, lambda n: _ok_resp("不该被调"))

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# 今天\n- x",
        model_name="catfish-private-main",
    )
    assert result["source"] == "fallback"
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_502_falls_back_no_switch(monkeypatch):
    """单 candidate 502 → fallback (严格 1 candidate, 不切)."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(502))

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# x",
        model_name="catfish-private-main",
    )
    assert result["source"] == "fallback"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_429_falls_back_no_switch(monkeypatch):
    """单 candidate 429 quota_exceeded → fallback (不偷消耗别 model 配额)."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(429))

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# x",
        model_name="catfish-private-main",
    )
    assert result["source"] == "fallback"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_401_falls_back(monkeypatch):
    """4xx 非 429 (auth bad / schema 等) → fallback."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(401))

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# x",
        model_name="catfish-private-main",
    )
    assert result["source"] == "fallback"
    assert call_count["n"] == 1


# ── 200 但内容空 → fallback ────────────────────────────────────


@pytest.mark.asyncio
async def test_200_but_empty_content_falls_back(monkeypatch):
    """200 但 content + reasoning_content 都空 → fallback (没下一个 candidate 切)."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")

    def _resp(_n):
        return _MockResp(
            200,
            {"choices": [{"message": {"content": "", "reasoning_content": ""}}]},
        )

    call_count = _patch_httpx(monkeypatch, _resp)

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# x",
        model_name="catfish-private-main",
    )
    assert result["source"] == "fallback"
    assert call_count["n"] == 1


# ── DeepSeek thinking mode reasoning_content 兼容 ──────────────


@pytest.mark.asyncio
async def test_deepseek_thinking_reasoning_content_fallback(monkeypatch):
    """BL-F19+: deepseek V4 thinking mode content 空, 内容在 reasoning_content."""
    _patch_model_resolve(monkeypatch, model_name="catfish-public-deepseek-flash")

    def _resp(_n):
        return _MockResp(
            200,
            {"choices": [{"message": {
                "role": "assistant",
                "content": "",
                "reasoning_content": "<think>思考...</think>\n今天工作如何, 卡哪了?",
            }}]},
        )

    _patch_httpx(monkeypatch, _resp)

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# x",
        model_name="catfish-public-deepseek-flash",
    )
    assert result["source"] == "llm"
    assert "今天工作如何" in result["starter"] or "<think>" in result["starter"]


@pytest.mark.asyncio
async def test_content_priority_over_reasoning(monkeypatch):
    """content 不空时, 优先用 content, 不动 reasoning_content."""
    _patch_model_resolve(monkeypatch, model_name="catfish-private-main")

    def _resp(_n):
        return _MockResp(
            200,
            {"choices": [{"message": {
                "content": "答案在 content",
                "reasoning_content": "思考过程不该被员工看到",
            }}]},
        )

    _patch_httpx(monkeypatch, _resp)

    result = await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# x",
        model_name="catfish-private-main",
    )
    assert result["source"] == "llm"
    assert "答案在 content" in result["starter"]
    assert "思考过程" not in result["starter"]


# ── 验证 follow-user 透传: Companion 传的 model 真被请求 ───────


@pytest.mark.asyncio
async def test_request_uses_user_model_not_hardcoded(monkeypatch):
    """gateway 请求里 model 字段必须是 Companion 传过来的 model_name, 不是写死."""
    _patch_model_resolve(monkeypatch, model_name="catfish-public-nvidia-nemotron")

    captured_payloads: list[dict] = []

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **kw):
            captured_payloads.append(kw.get("json") or {})
            return _ok_resp("ok")

    import httpx  # noqa: PLC0415
    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    await proactive.generate_starter(
        user_email="zhang@example.com",
        journal_tail="# x",
        model_name="catfish-public-nvidia-nemotron",
    )

    assert captured_payloads, "应至少 1 个 gateway 请求"
    assert captured_payloads[0].get("model") == "catfish-public-nvidia-nemotron", (
        f"应原样用 Companion 透传的 model_name, 实际 {captured_payloads[0].get('model')!r}"
    )
