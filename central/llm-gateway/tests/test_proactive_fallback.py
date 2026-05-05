"""BL-F19 (5/5): proactive.generate_starter 候选切换行为回归.

修复背景 (5/5 18:00 鸿波报"小鲶不能自动聊天"):
    proactive.generate_starter 之前只在 429 quota error 时切候选, 5xx 直接 break.
    BL-F15 summarizer 改成所有 retriable 错误都切候选, proactive 漏了同步.

实际场景:
    candidate 1 catfish-private-main → VPN 断 → gateway 返 502
    candidate 2 catfish-public-qwen-flash → DashScope 免费层 403 → gateway 返 502
    原代码: 502 break → 不试 candidate 3+ deepseek-flash (健康)
    修后: 502 切下一个, candidate 3 deepseek-flash 200 → 主动闲聊正常

测试覆盖:
  - 候选 1 返 502 → 切候选 2 → candidate 2 返 200 → source=llm 成功
  - 候选 1 返 429 → 切候选 2 → 同上
  - 候选 1 返 401 (auth bad, 非 retriable) → break, 不切候选 2 → fallback
  - 全候选都 502 → fallback 模板
  - 候选列表空 → 直接 fallback, 不发 HTTP
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from catfish_gateway import proactive


class _MockResp:
    def __init__(self, status_code: int, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


def _ok_resp(text: str = "今天进展如何, 卡哪了?") -> _MockResp:
    return _MockResp(
        200,
        {"choices": [{"message": {"role": "assistant", "content": text}}]},
    )


def _make_candidate(name: str) -> MagicMock:
    """模拟 ModelEntry, generate_starter 只用 .name 字段."""
    m = MagicMock()
    m.name = name
    return m


def _patch_picker(monkeypatch, candidates):
    """generate_starter 内部 lazy import pick_internal_models_ordered + load_config,
    必须 patch 源模块."""
    from catfish_gateway import internal_models, config as config_mod  # noqa: PLC0415
    monkeypatch.setattr(internal_models, "pick_internal_models_ordered",
                        lambda *_a, **_kw: candidates)
    monkeypatch.setattr(config_mod, "load_config", lambda: MagicMock())
    monkeypatch.setattr(proactive, "_read_journal_tail", lambda: "")


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


@pytest.mark.asyncio
async def test_502_switches_to_next_candidate(monkeypatch):
    """BL-F19: 候选 1 返 502 → 切候选 2 → 200 成功. 5/5 鸿波 P0 修复关键场景."""
    candidates = [_make_candidate("private-main"), _make_candidate("deepseek-flash")]
    _patch_picker(monkeypatch, candidates)

    call_count = _patch_httpx(
        monkeypatch,
        lambda n: _MockResp(502) if n == 1 else _ok_resp("hello from candidate 2"),
    )

    result = await proactive.generate_starter()

    assert result["source"] == "llm", (
        f"应该切到 candidate 2 成功 (source=llm), 实际 source={result['source']}, "
        f"call_count={call_count['n']}. BL-F19 又坏了?"
    )
    assert "hello from candidate 2" in result["starter"]
    assert call_count["n"] == 2, f"应试 2 个候选, 实际 {call_count['n']}"


@pytest.mark.asyncio
async def test_429_switches_to_next_candidate(monkeypatch):
    """同上, 但是 429 quota_exceeded — 之前已正确切, 这条防回归."""
    candidates = [_make_candidate("private-main"), _make_candidate("qwen-flash")]
    _patch_picker(monkeypatch, candidates)

    call_count = _patch_httpx(
        monkeypatch,
        lambda n: _MockResp(429) if n == 1 else _ok_resp("ok"),
    )

    result = await proactive.generate_starter()
    assert result["source"] == "llm"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_401_does_not_switch_breaks_immediately(monkeypatch):
    """4xx 非 429 (auth bad / schema 等) 直接 break, 切了也是同样错."""
    candidates = [_make_candidate("private-main"), _make_candidate("qwen-flash")]
    _patch_picker(monkeypatch, candidates)

    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(401))

    result = await proactive.generate_starter()
    assert result["source"] == "fallback", "401 应直接 fallback 模板"
    assert call_count["n"] == 1, f"401 应 break 不切候选, 实际试了 {call_count['n']} 次"


@pytest.mark.asyncio
async def test_all_candidates_502_fallback(monkeypatch):
    """全候选都 502 → 走 fallback 模板."""
    candidates = [
        _make_candidate("c1"),
        _make_candidate("c2"),
        _make_candidate("c3"),
    ]
    _patch_picker(monkeypatch, candidates)

    call_count = _patch_httpx(monkeypatch, lambda n: _MockResp(502))

    result = await proactive.generate_starter()
    assert result["source"] == "fallback"
    assert call_count["n"] == 3, f"应试完所有 3 个候选, 实际 {call_count['n']}"


@pytest.mark.asyncio
async def test_no_candidates_skips_to_fallback(monkeypatch):
    """catalog 没可用模型 → 直接 fallback, 不发 HTTP."""
    _patch_picker(monkeypatch, [])

    result = await proactive.generate_starter()
    assert result["source"] == "fallback"


# ============================================================
# BL-F19+ (5/5 18:30): deepseek thinking mode content 兼容
# ============================================================

@pytest.mark.asyncio
async def test_deepseek_thinking_reasoning_content_fallback(monkeypatch):
    """BL-F19+: deepseek V4 thinking mode 把内容写在 reasoning_content,
    content 空. 之前 proactive 只读 content → 拿空字符串 → 后面 ValueError
    "全候选失败" 退到模板 fallback. 5/5 18:30 鸿波看到 log "切到第 3 候选 ...
    成功" 紧跟 "全候选失败 fallback 模板" 矛盾. 改成 content 优先,
    reasoning_content fallback. 这条防回归."""
    candidates = [_make_candidate("deepseek-flash")]
    _patch_picker(monkeypatch, candidates)

    # 模拟 deepseek thinking mode 响应: content="" + reasoning_content 有内容
    def _resp(_n: int) -> _MockResp:
        return _MockResp(
            200,
            {"choices": [{"message": {
                "role": "assistant",
                "content": "",
                "reasoning_content": "<think>思考...</think>\n今天工作如何, 卡哪了?",
            }}]},
        )
    _patch_httpx(monkeypatch, _resp)

    result = await proactive.generate_starter()
    assert result["source"] == "llm", (
        f"应从 reasoning_content 提取 starter, 实际 {result['source']}"
    )
    assert "今天工作如何" in result["starter"] or "<think>" in result["starter"]


@pytest.mark.asyncio
async def test_200_but_both_fields_empty_switches_candidate(monkeypatch):
    """edge case: 200 但 content + reasoning_content 都空 (罕见 / 上游故障).
    应切下一个候选, 不 break."""
    candidates = [_make_candidate("c1"), _make_candidate("c2")]
    _patch_picker(monkeypatch, candidates)

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
            if call_count["n"] == 1:
                # candidate 1 返 200 但 content + reasoning_content 都空
                return _MockResp(
                    200,
                    {"choices": [{"message": {"content": "", "reasoning_content": ""}}]},
                )
            return _ok_resp("from c2")

    import httpx  # noqa: PLC0415
    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    result = await proactive.generate_starter()
    assert result["source"] == "llm"
    assert "from c2" in result["starter"]
    assert call_count["n"] == 2, f"应试 2 个候选, 实际 {call_count['n']}"


@pytest.mark.asyncio
async def test_content_priority_over_reasoning(monkeypatch):
    """content 不空时, 优先用 content, 不动 reasoning_content."""
    candidates = [_make_candidate("c1")]
    _patch_picker(monkeypatch, candidates)

    def _resp(_n: int) -> _MockResp:
        return _MockResp(
            200,
            {"choices": [{"message": {
                "content": "答案在 content",
                "reasoning_content": "思考过程不该被员工看到",
            }}]},
        )
    _patch_httpx(monkeypatch, _resp)

    result = await proactive.generate_starter()
    assert result["source"] == "llm"
    assert "答案在 content" in result["starter"]
    assert "思考过程" not in result["starter"]
