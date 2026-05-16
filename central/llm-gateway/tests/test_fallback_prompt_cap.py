"""BL-FALLBACK-PROMPT-CAP (5/14 鸿波 token audit 后) — 公网 fallback 大 prompt 拦截单测.

跑法:
    cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_fallback_prompt_cap.py -q
"""
from __future__ import annotations

import pytest

from catfish_gateway.config import (
    Config,
    FallbackConfig,
    ModelConfig,
    UpstreamConfig,
)
from catfish_gateway.fallback import (
    LargePromptFallbackBlocked,
    estimate_prompt_tokens,
    resolve_chain,
    with_fallback,
)


# ─── estimate_prompt_tokens 纯函数 ─────────────────────────


def test_estimate_empty():
    assert estimate_prompt_tokens([]) == 0
    assert estimate_prompt_tokens(None) == 0


def test_estimate_single_string():
    """中文 1000 字 → ~500 tokens (chars / 2)"""
    msgs = [{"role": "user", "content": "中" * 1000}]
    assert estimate_prompt_tokens(msgs) == 500


def test_estimate_multipart_text_only_counts():
    """multipart vision: image_url 不算, 只算 text"""
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "看这张图"},  # 4 字 / 2 = 2
            {"type": "image_url", "image_url": {"url": "data:..."}},  # 不算
        ],
    }]
    assert estimate_prompt_tokens(msgs) == 2


def test_estimate_tool_call_arguments_counted():
    """assistant 的 tool_calls.arguments 也算 (不然漏算长 args)"""
    msgs = [{
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"function": {"name": "x", "arguments": "a" * 100}},
        ],
    }]
    assert estimate_prompt_tokens(msgs) == 50


def test_estimate_multi_turn():
    """多 message 累加"""
    msgs = [
        {"role": "system", "content": "S" * 100},  # 50
        {"role": "user", "content": "U" * 200},    # 100
        {"role": "assistant", "content": "A" * 50},  # 25
    ]
    assert estimate_prompt_tokens(msgs) == 175


# ─── resolve_chain 过滤逻辑 ──────────────────────────────


def _mk_model(name, tier="public", api_key_env="OPENAI_API_KEY", chain=None, mode="chat"):
    return ModelConfig(
        name=name,
        display_name=name,  # 必填
        tier=tier,
        upstream=UpstreamConfig(model=f"openai/{name}", api_base="http://localhost", api_key_env=api_key_env),
        mode=mode,
        fallback=FallbackConfig(chain=chain or []) if chain else None,
    )


def _mk_config(models, max_cap=30000, auto_fallback=True):
    """BL-FALLBACK-TOGGLE (5/16): 默认 auto_fallback=True 让现有 prompt-cap 测试继续测.
    单独覆盖 auto_fallback=False 时的行为在 test_fallback.py 加.
    """
    cfg = Config(
        version=1, models=models,
        max_fallback_prompt_tokens=max_cap,
        auto_fallback=auto_fallback,
    )
    return cfg


def test_resolve_chain_small_prompt_keeps_public(monkeypatch):
    """prompt 估算 < cap → 公网 candidate 保留"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["public_a", "public_b"])
    cfg = _mk_config([main, _mk_model("public_a"), _mk_model("public_b")])
    chain = resolve_chain(cfg, main, prompt_estimate=10000)
    names = [m.name for m in chain]
    assert names == ["public_a", "public_b"]


def test_resolve_chain_large_prompt_filters_public(monkeypatch):
    """prompt 估算 > cap → 公网 candidate 全过滤掉"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["public_a", "public_b"])
    cfg = _mk_config([main, _mk_model("public_a"), _mk_model("public_b")], max_cap=30000)
    chain = resolve_chain(cfg, main, prompt_estimate=50000)
    assert chain == []


def test_resolve_chain_large_prompt_keeps_private(monkeypatch):
    """prompt 估算 > cap → 私网 candidate **保留** (不公网才 ban)"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("INTERNAL_LLM_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["private_b", "public_a"])
    cfg = _mk_config([
        main,
        _mk_model("private_b", tier="private", api_key_env="INTERNAL_LLM_KEY"),
        _mk_model("public_a"),
    ], max_cap=30000)
    chain = resolve_chain(cfg, main, prompt_estimate=50000)
    names = [m.name for m in chain]
    assert names == ["private_b"]  # 公网过滤, 私网留


def test_resolve_chain_cap_zero_disables_filter(monkeypatch):
    """max_cap=0 = 关功能, 任意 prompt 都允许公网 fallback (老行为)"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["public_a"])
    cfg = _mk_config([main, _mk_model("public_a")], max_cap=0)
    chain = resolve_chain(cfg, main, prompt_estimate=999999)
    names = [m.name for m in chain]
    assert names == ["public_a"]


def test_resolve_chain_default_estimate_zero_keeps_public(monkeypatch):
    """没传 prompt_estimate (默认 0) → 公网保留 (老调用方兼容)"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["public_a"])
    cfg = _mk_config([main, _mk_model("public_a")])
    chain = resolve_chain(cfg, main)  # 默认 prompt_estimate=0
    names = [m.name for m in chain]
    assert names == ["public_a"]


# ─── with_fallback 端到端 ─────────────────────────────────


@pytest.mark.asyncio
async def test_with_fallback_large_prompt_blocked(monkeypatch):
    """主模型失败 + chain 全公网 + 大 prompt → 抛 LargePromptFallbackBlocked"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["public_a", "public_b"])
    main.fallback.on_errors = ["timeout"]
    cfg = _mk_config([main, _mk_model("public_a"), _mk_model("public_b")], max_cap=30000)

    async def _fail(model):
        raise TimeoutError("internal slow timeout")

    with pytest.raises(LargePromptFallbackBlocked) as exc_info:
        await with_fallback(cfg, main, _fail, prompt_estimate=50000)

    err = exc_info.value
    assert err.primary_name == "primary"
    assert err.prompt_estimate == 50000
    assert err.cap == 30000
    assert isinstance(err.last_exc, TimeoutError)
    assert err.blocked_chain == ["public_a", "public_b"]
    msg = err.friendly_message()
    assert "30,000" in msg
    assert "50,000" in msg
    assert "公网更慢更贵" in msg


@pytest.mark.asyncio
async def test_with_fallback_large_prompt_private_chain_still_works(monkeypatch):
    """大 prompt + chain 含私网 → 私网继续 try, 不抛 blocked"""
    monkeypatch.setenv("INTERNAL_LLM_KEY", "fake")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["private_b", "public_a"])
    main.fallback.on_errors = ["timeout"]
    cfg = _mk_config([
        main,
        _mk_model("private_b", tier="private", api_key_env="INTERNAL_LLM_KEY"),
        _mk_model("public_a"),
    ], max_cap=30000)

    call_log = []

    async def _invoker(model):
        call_log.append(model.name)
        if model.name == "primary":
            raise TimeoutError("internal slow timeout")
        return "fake_response"

    result, used, attempts = await with_fallback(cfg, main, _invoker, prompt_estimate=50000)
    assert used.name == "private_b"  # 私网兜底成功
    assert "public_a" not in call_log  # 公网被 cap 拦, 没 try


@pytest.mark.asyncio
async def test_with_fallback_small_prompt_falls_to_public_normal(monkeypatch):
    """小 prompt + 主模型失败 → 公网正常兜底 (老行为不变)"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["public_a"])
    main.fallback.on_errors = ["timeout"]
    cfg = _mk_config([main, _mk_model("public_a")], max_cap=30000)

    async def _invoker(model):
        if model.name == "primary":
            raise TimeoutError("internal slow timeout")
        return "fake_response"

    result, used, _ = await with_fallback(cfg, main, _invoker, prompt_estimate=10000)
    assert used.name == "public_a"  # 公网正常切


@pytest.mark.asyncio
async def test_with_fallback_no_estimate_arg_compatible(monkeypatch):
    """没传 prompt_estimate (默认 0) → 公网正常兜底 (老 caller 不挂)"""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    main = _mk_model("primary", tier="private", chain=["public_a"])
    main.fallback.on_errors = ["timeout"]
    cfg = _mk_config([main, _mk_model("public_a")])

    async def _invoker(model):
        if model.name == "primary":
            raise TimeoutError("oops timeout")
        return "ok"

    result, used, _ = await with_fallback(cfg, main, _invoker)  # 没传 prompt_estimate
    assert used.name == "public_a"


# ─── BL-TOOLS-IN-ESTIMATE (5/15 22:00 鸿波 'NVIDIA 还撞 ContextWindow') ──
#
# LiteLLM token_counter 只算 messages, 不算 chat completion body 的 tools field.
# 上游 (NVIDIA / OpenAI) 算 prompt 时把 tools schema 也当 input. 50 tools 漏算
# = 我们估 42K vs 真 69K 差 40%. 修: estimate 加 tools 参数, JSON char/4 估.


def test_estimate_includes_tools_schema():
    """tools schema 应该算进 prompt 估算"""
    from catfish_gateway.fallback import estimate_prompt_tokens

    messages = [{"role": "user", "content": "hi"}]
    # 不传 tools
    without_tools = estimate_prompt_tokens(messages)

    # 传 50 个 tool (模拟 catfish 实际场景)
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "A test tool that does X" * 20,  # ~400 char desc
                "parameters": {
                    "type": "object",
                    "properties": {
                        "arg1": {"type": "string", "description": "arg description"},
                        "arg2": {"type": "number"},
                    },
                },
            },
        }
        for i in range(50)
    ]
    with_tools = estimate_prompt_tokens(messages, tools=tools)

    # tools 50 个 * 大约 600 char/tool = 30K char / 4 char/token = 7.5K token
    assert with_tools > without_tools, "加 tools 后估算应该变大"
    assert with_tools - without_tools > 5000, (
        f"50 tools 应至少加 5K token, 实际只 +{with_tools - without_tools}"
    )


def test_estimate_no_tools_unchanged():
    """tools=None / [] → 跟没传 tools 一样"""
    from catfish_gateway.fallback import estimate_prompt_tokens

    messages = [{"role": "user", "content": "hi"}]
    none_val = estimate_prompt_tokens(messages, tools=None)
    empty = estimate_prompt_tokens(messages, tools=[])
    no_arg = estimate_prompt_tokens(messages)
    assert none_val == empty == no_arg


def test_estimate_tools_only_no_messages():
    """没 messages 但有 tools — 也能估"""
    from catfish_gateway.fallback import estimate_prompt_tokens

    tools = [
        {"type": "function", "function": {"name": "test", "parameters": {}}}
    ]
    n = estimate_prompt_tokens([], tools=tools)
    assert n > 0, "tools schema 也应有 token 数"


# ─── BL-FALLBACK-CAP-SCOPE (5/15 22:15 鸿波 'NVIDIA primary 撞错全链被拦') ───
#
# BL-FALLBACK-PROMPT-CAP 原意: 内网数据不应跨 public 边界.
# 修: 仅 primary.tier=='private' 时才 cap public candidate.
# public primary 撞错走 public fallback chain 不算"跨边界" — 应该允许.


def test_public_primary_no_cap_on_public_fallback():
    """primary 已经是 public — fallback 到 public 不应被 BL-FALLBACK-PROMPT-CAP 拦.

    回归实盘场景: catfish-public-nvidia-llama (public) 撞 ContextWindowExceeded,
    fallback chain qwen-flash/deepseek-flash/gemini-flash (全 public) 应该能 hop."""
    from types import SimpleNamespace

    from catfish_gateway.fallback import resolve_chain

    # 造 fake config with NVIDIA primary + public chain
    nvidia = SimpleNamespace(
        name="catfish-public-nvidia-llama",
        tier="public",
        mode="chat",
        upstream=SimpleNamespace(api_key_env="NVIDIA_API_KEY", is_available=True),
        fallback=SimpleNamespace(
            chain=["catfish-public-qwen-flash"],
            on_errors=["context window"],
            max_hops=2,
        ),
    )
    qwen = SimpleNamespace(
        name="catfish-public-qwen-flash",
        tier="public",
        mode="chat",
        upstream=SimpleNamespace(api_key_env="DASHSCOPE_API_KEY", is_available=True),
    )
    fake_config = SimpleNamespace(
        max_fallback_prompt_tokens=30000,
        get_model=lambda n: {nvidia.name: nvidia, qwen.name: qwen}.get(n),
    )

    # 大 prompt 50K, 远超 cap 30K — 但 primary 已是 public, 不应拦 public fallback
    chain = resolve_chain(fake_config, nvidia, prompt_estimate=50000)

    assert len(chain) == 1, (
        f"public primary 的 public fallback chain 不该被 cap 拦, "
        f"实际剩 {len(chain)} 个"
    )
    assert chain[0].name == "catfish-public-qwen-flash"


def test_private_primary_still_caps_public_fallback():
    """primary 是 private — 仍按 BL-FALLBACK-PROMPT-CAP 原设计拦 public.

    确保 BL-FALLBACK-CAP-SCOPE 改动没破坏内网→公网保密线."""
    from types import SimpleNamespace

    from catfish_gateway.fallback import resolve_chain

    private_main = SimpleNamespace(
        name="catfish-private-main",
        tier="private",  # ← 关键: private primary
        mode="chat",
        upstream=SimpleNamespace(api_key_env="INTERNAL_LLM_KEY", is_available=True),
        fallback=SimpleNamespace(
            chain=["catfish-public-qwen-flash"],
            on_errors=["timeout"],
            max_hops=2,
        ),
    )
    qwen = SimpleNamespace(
        name="catfish-public-qwen-flash",
        tier="public",
        mode="chat",
        upstream=SimpleNamespace(api_key_env="DASHSCOPE_API_KEY", is_available=True),
    )
    fake_config = SimpleNamespace(
        max_fallback_prompt_tokens=30000,
        get_model=lambda n: {private_main.name: private_main, qwen.name: qwen}.get(n),
    )

    # 大 prompt 50K, private primary → public fallback 应被拦 (跨边界)
    chain = resolve_chain(fake_config, private_main, prompt_estimate=50000)

    assert len(chain) == 0, (
        f"private primary → public fallback 大 prompt 应被拦 (跨保密边界), "
        f"实际剩 {len(chain)} 个"
    )
