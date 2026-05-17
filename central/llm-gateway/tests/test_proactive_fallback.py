"""proactive.generate_starter 行为回归.

# 当前规则 (BL-INTERNAL-MODEL-FOLLOW-USER-FULL 5/17 拍板)

严格 **1 candidate** — 员工最近 session 的 model, 拿不到就 fallback 模板, **不切候选**.

历史背景 (供 git blame 追溯):
  - BL-F19 (5/5): 老多候选 fallback chain (private-main → qwen-flash → deepseek-flash),
    在 502 / 429 / 5xx 时切下一个. 当时是为了应对 VPN 断 + 公网配额耗光的混合故障.
  - BL-INTERNAL-MODEL-FOLLOW-USER (5/17): 鸿波拍板 "选哪个 model 所有 LLM 都用同款".
    多候选切换跟规则矛盾 — 员工选私有 main, proactive 偷偷切公网 qwen-flash 就是窜账.
    删掉切换逻辑, 严格 1 candidate; 撞错就 fallback 模板.

# 这个 test 覆盖什么

  - 正常: 拿到员工 model + gateway 200 → source=llm
  - 拿不到员工 model (新员工 / 老 schema) → fallback 模板, 不打 gateway
  - 单 candidate 502 → fallback (不切候选, 没下一个)
  - 单 candidate 401 → fallback (4xx 同 5xx 处理)
  - 200 但 content + reasoning_content 都空 → fallback
  - DeepSeek thinking mode (content="" + reasoning_content 有内容) → 用 reasoning
  - content 有值时优先 content 不动 reasoning_content
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def _patch_user_model(monkeypatch, *, model_name: str | None = "test-model"):
    """patch user_model_resolver: 返指定 model_name (None 表示员工没 session model).

    BL-INTERNAL-MODEL-FOLLOW-USER (5/17): generate_starter 不再用 picker,
    走 get_user_last_session_model + resolve_model_obj. 测试要 mock 这俩.
    """
    fake_model = SimpleNamespace(
        name=model_name,
        mode="chat",
        upstream=SimpleNamespace(is_available=True),
    ) if model_name else None
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
        lambda: SimpleNamespace(models=[fake_model] if fake_model else []),
    )
    monkeypatch.setattr(proactive, "_read_journal_tail", lambda: "")
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


# ── 正常路径 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_returns_llm_source(monkeypatch):
    """拿到员工 model + 200 → source=llm, starter 是 LLM 返的文本."""
    _patch_user_model(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _ok_resp("hello from llm"))

    result = await proactive.generate_starter(user_email="zhang@example.com")

    assert result["source"] == "llm"
    assert "hello from llm" in result["starter"]
    assert call_count["n"] == 1  # 严格 1 candidate


# ── Fallback 路径: 拿不到 model / 错误响应 ──────────────


@pytest.mark.asyncio
async def test_no_user_model_returns_fallback(monkeypatch):
    """员工没最近 session model (新员工 / 老 schema) → fallback 模板, 不打 gateway.

    BL-INTERNAL-MODEL-FOLLOW-USER: 不偷偷用别的 model.
    """
    _patch_user_model(monkeypatch, model_name=None)
    call_count = _patch_httpx(monkeypatch, lambda n: _ok_resp("不该被调"))

    result = await proactive.generate_starter(user_email="newbie@example.com")
    assert result["source"] == "fallback"
    assert call_count["n"] == 0  # gateway 没被打


@pytest.mark.asyncio
async def test_502_falls_back_no_switch(monkeypatch):
    """单 candidate 502 → fallback (不切候选, 没下一个).

    跟老 BL-F19 时代不同 — 老代码会试 candidate 2/3 直到 success. 现在 1 个撞错就完.
    """
    _patch_user_model(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(502))

    result = await proactive.generate_starter(user_email="zhang@example.com")
    assert result["source"] == "fallback"
    assert call_count["n"] == 1, f"严格 1 candidate, 实际 {call_count['n']}"


@pytest.mark.asyncio
async def test_429_falls_back_no_switch(monkeypatch):
    """单 candidate 429 quota_exceeded → fallback (不切候选).

    跟老 BL-F19 时代不同 — 老代码会切下一个候选避开员工配额墙. 现在严格 1 candidate,
    员工配额满就先看到 fallback 模板, 不偷偷消耗别的 model 配额.
    """
    _patch_user_model(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(429))

    result = await proactive.generate_starter(user_email="zhang@example.com")
    assert result["source"] == "fallback"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_401_falls_back(monkeypatch):
    """4xx 非 429 (auth bad / schema 等) → fallback."""
    _patch_user_model(monkeypatch, model_name="catfish-private-main")
    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(401))

    result = await proactive.generate_starter(user_email="zhang@example.com")
    assert result["source"] == "fallback"
    assert call_count["n"] == 1


# ── 200 但内容空 → fallback (不再切候选) ────────────────


@pytest.mark.asyncio
async def test_200_but_empty_content_falls_back(monkeypatch):
    """200 但 content + reasoning_content 都空 → fallback.

    跟老 BL-F19 时代不同 — 老代码会切下一个候选试. 现在 1 candidate 没下一个, fallback.
    """
    _patch_user_model(monkeypatch, model_name="catfish-private-main")

    def _resp(_n):
        return _MockResp(
            200,
            {"choices": [{"message": {"content": "", "reasoning_content": ""}}]},
        )

    call_count = _patch_httpx(monkeypatch, _resp)

    result = await proactive.generate_starter(user_email="zhang@example.com")
    assert result["source"] == "fallback"
    assert call_count["n"] == 1


# ── DeepSeek thinking mode reasoning_content 兼容 ──────


@pytest.mark.asyncio
async def test_deepseek_thinking_reasoning_content_fallback(monkeypatch):
    """BL-F19+: deepseek V4 thinking mode 把内容写在 reasoning_content,
    content 空. 改成 content 优先, reasoning_content fallback. 这条防回归.
    """
    _patch_user_model(monkeypatch, model_name="catfish-public-deepseek-flash")

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

    result = await proactive.generate_starter(user_email="zhang@example.com")
    assert result["source"] == "llm"
    assert "今天工作如何" in result["starter"] or "<think>" in result["starter"]


@pytest.mark.asyncio
async def test_content_priority_over_reasoning(monkeypatch):
    """content 不空时, 优先用 content, 不动 reasoning_content."""
    _patch_user_model(monkeypatch, model_name="catfish-private-main")

    def _resp(_n):
        return _MockResp(
            200,
            {"choices": [{"message": {
                "content": "答案在 content",
                "reasoning_content": "思考过程不该被员工看到",
            }}]},
        )

    _patch_httpx(monkeypatch, _resp)

    result = await proactive.generate_starter(user_email="zhang@example.com")
    assert result["source"] == "llm"
    assert "答案在 content" in result["starter"]
    assert "思考过程" not in result["starter"]


# ── 验证 follow-user 透传: 员工 model 真被请求 ────────


@pytest.mark.asyncio
async def test_request_uses_user_model_not_hardcoded(monkeypatch):
    """gateway 请求里 model 字段必须是员工 model, 不是写死的 'qwen-flash' / 别的.

    防硬编码回归 (memory_distill 撞过的坑).
    """
    _patch_user_model(monkeypatch, model_name="catfish-public-nvidia-nemotron")

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

    await proactive.generate_starter(user_email="zhang@example.com")

    assert captured_payloads, "应至少 1 个 gateway 请求"
    assert captured_payloads[0].get("model") == "catfish-public-nvidia-nemotron", (
        f"应原样用员工 model, 实际 {captured_payloads[0].get('model')!r}"
    )
