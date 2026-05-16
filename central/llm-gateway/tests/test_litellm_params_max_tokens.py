"""_build_litellm_params 的 max_tokens 默认值演进:

  BL-FIX23 L4a (5/9): 兜底 4096 (Qwen vLLM 默认太小, 长 docx 半截就停)
  BL-MAX-TOKENS-32K (5/15 16:00): 兜底改 32768 (PPT 模板 858 行 4K 撑不住)
  BL-MAX-TOKENS-DYNAMIC (5/15 17:00 鸿波 'context 128K 还能放大'):
    动态算 = context_window - prompt_estimate - safety_margin, 下限 4K.
    短 prompt 场景 output 能用到 ~120K, 长 prompt 场景自动留够 prompt 空间.

防回归 + 验证 dynamic 边界.
"""
from __future__ import annotations

from types import SimpleNamespace

from catfish_gateway.app import _build_litellm_params


def _fake_model(
    name: str = "test-model",
    context_window: int = 0,
    max_output_tokens: int = 0,
):
    """造一个 ConfigModel 替身 (只填 _build_litellm_params 用到的字段).

    context_window=0 时模拟"没配 context window" 的 model — 走 fallback 32K.
    max_output_tokens=0 模拟"没配单次输出上限" → 沿用 cw (老行为).
    """
    return SimpleNamespace(
        name=name,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        upstream=SimpleNamespace(
            model="openai/test",
            api_key="test-key",
            api_base="http://127.0.0.1:9999/v1",
            timeout=60,
            param_overrides={},
        ),
    )


# ── 客户端显式传值 — 永远尊重 ────────────────────────────


def test_max_tokens_client_value_preserved():
    """client 真传了 max_tokens (例如 summarizer 设 600) → 不动, 走 client 值."""
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 600,
    }
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    assert params["max_tokens"] == 600


def test_max_tokens_client_zero_preserved():
    """client 传 0 (虽然没意义) 不被覆盖 — 不要 auto-magic 改员工显式值."""
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0}
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    assert params["max_tokens"] == 0


# ── 客户端没传, fallback 路径 ─────────────────────────────


def test_fallback_32k_when_no_context_window():
    """model 没配 context_window → fallback CATFISH_MAX_TOKENS_FALLBACK (32768)"""
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    params = _build_litellm_params(body, _fake_model(context_window=0))
    assert params["max_tokens"] == 32768


def test_fallback_when_client_passes_none():
    """client 显式传 max_tokens=None → 跟没传一样, 走 fallback."""
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "max_tokens": None,
    }
    params = _build_litellm_params(body, _fake_model(context_window=0))
    assert params["max_tokens"] == 32768


# ── BL-MAX-TOKENS-DYNAMIC — 真 dynamic 路径 ──────────────


def test_dynamic_max_tokens_short_prompt_uses_most_of_window():
    """BL-TOKEN-COUNTER-LITELLM (5/15 拆 HARD-CAP 偷懒):
    estimator 改用 LiteLLM token_counter 准估后, dynamic 不再需要硬上限.
    短 prompt + 128K context → max_tokens 接近 128K (留 safety + prompt)."""
    body = {
        "messages": [{"role": "user", "content": "hello"}],  # ~5 token
        "stream": True,
    }
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    # 128000 - prompt - 2K safety 接近 128K (具体数 LiteLLM tokenizer 算)
    assert params["max_tokens"] > 100000
    assert params["max_tokens"] <= 128000


def test_dynamic_max_tokens_long_prompt_leaves_safety_margin():
    """长 prompt + context 128K → max_tokens 留够 prompt + safety.

    BL-TOKEN-COUNTER-LITELLM: LiteLLM tokenizer 准估 prompt, dyn 不会撞 context.
    用 ascii 'x' 重复保证 LiteLLM tokenizer 估算稳定 (英文 ~ 4 char/token).
    100K chars 'x' ≈ 25K-35K token. dyn = 128K - 30K - 2K ≈ 96K. 留余量给 prompt."""
    long_content = "x " * 50000  # 100K chars (含空格切 word)
    body = {
        "messages": [{"role": "user", "content": long_content}],
        "stream": True,
    }
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    # dyn 应该 < 128K 且 > 30K (留够 prompt + safety)
    assert params["max_tokens"] < 128000
    assert params["max_tokens"] > 30000


def test_dynamic_max_tokens_clipped_to_4k_minimum_when_prompt_near_full():
    """prompt 几乎占满 context → max_tokens 至少 4K 下限 (不让出 0 或负数).

    用真 1MB ascii 让 LiteLLM 估算稳超 128K context."""
    huge_content = "x " * 600000  # 1.2M chars ≈ 300K token, 超 128K
    body = {
        "messages": [{"role": "user", "content": huge_content}],
        "stream": True,
    }
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    # 即使 dyn 算出负数, 也要至少 4K
    assert params["max_tokens"] >= 4096


def test_dynamic_max_tokens_safety_margin_env_override(monkeypatch):
    """env CATFISH_MAX_TOKENS_SAFETY 调整安全余量"""
    monkeypatch.setenv("CATFISH_MAX_TOKENS_SAFETY", "8192")
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    # 128000 - prompt - 8K safety ≈ 119000-120000
    assert 118000 < params["max_tokens"] < 120000


def test_dynamic_works_with_param_overrides():
    """param_overrides 跟 dynamic max_tokens 互不影响"""
    model = _fake_model(context_window=128000)
    model.upstream.param_overrides = {"temperature": 1.0}
    body = {"messages": [{"role": "user", "content": "hi"}]}
    params = _build_litellm_params(body, model)
    # 不再硬上限 32K
    assert params["max_tokens"] > 100000
    assert params["temperature"] == 1.0


# ─── BL-MAX-TOKENS-CLIP (5/15 21:30 鸿波撞 NVIDIA ContextWindowExceededError) ──
#
# client 显式传 max_tokens 时, 仍 clip 到 model 真实剩余空间 (cw - prompt - safety).
# 防 fallback 切到小 context model 时撞 400 ContextWindowExceededError.


def test_clip_max_tokens_when_client_value_exceeds_model_window():
    """实盘场景: client 传 max_tokens 超 model 剩余空间 → clip 到剩余.

    用真大 prompt + 128K context, client 要 200K (远超 cw) → clip 到 ~125K."""
    long_content = "x " * 30000  # ~15K-20K token, 留 100K+ 给 output
    body = {
        "messages": [{"role": "user", "content": long_content}],
        "max_tokens": 200000,  # client 要 200K, 但 cw 只 128K
    }
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    # 应被 clip 到 cw 内
    assert params["max_tokens"] < 200000, (
        f"client 200K 超 cw 128K, 应该被 clip. 实际 {params['max_tokens']}"
    )
    assert params["max_tokens"] <= 128000
    assert params["max_tokens"] >= 4096


def test_no_clip_when_client_value_fits():
    """client 传 max_tokens=4096, model 装得下 → 不动"""
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 4096,
    }
    params = _build_litellm_params(body, _fake_model(context_window=128000))
    # 4096 < 剩余空间 (128K - few tokens - 2K) — 不 clip
    assert params["max_tokens"] == 4096


def test_no_clip_when_no_context_window():
    """没 context_window — 不算 clip (无法判断超不超)"""
    body = {
        "messages": [{"role": "user", "content": "x" * 100000}],
        "max_tokens": 100000,
    }
    params = _build_litellm_params(body, _fake_model(context_window=0))
    # 没 cw 信息 — 尊重 client 值不动 (因为算不出 max_allowed)
    assert params["max_tokens"] == 100000


# ─── BL-MAX-OUTPUT-TOKENS (5/15 22:31 鸿波撞 DeepSeek 400 [1, 393216]) ─────
#
# context_window (prompt+output 总上限) 跟 max_output_tokens (单次 output 上限)
# 是 model 的两个不同属性. 没区分会撞:
#   DeepSeek V4   context 1M  / output 393K  ← dyn 算 ~990K 超 → 400
#   Gemini 2.5 Pro context 2M / output 65K   ← dyn 算 ~1.9M 超 → 400


def test_max_output_tokens_caps_dyn_when_no_client_value():
    """model 配了 max_output_tokens → dyn 不会超过它, 哪怕 cw 远大于它.

    DeepSeek 场景: cw=1M, max_out=393K, 短 prompt → dyn 应被 cap 到 ≤393K."""
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    params = _build_litellm_params(
        body,
        _fake_model(context_window=1_000_000, max_output_tokens=393216),
    )
    assert params["max_tokens"] <= 393216, (
        f"max_output_tokens=393216 必须封顶, 实际 {params['max_tokens']}"
    )
    # 仍应接近上限 (短 prompt 拿满)
    assert params["max_tokens"] > 300_000


def test_max_output_tokens_caps_client_value_too():
    """client 传 max_tokens=500K, model max_out=393K → clip 到 393K (防 400)."""
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 500_000,
    }
    params = _build_litellm_params(
        body,
        _fake_model(context_window=1_000_000, max_output_tokens=393216),
    )
    assert params["max_tokens"] <= 393216


def test_gemini_pro_2m_context_65k_output():
    """Gemini 2.5 Pro: context 2M / output 65K — dyn 必须 ≤ 65K."""
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    params = _build_litellm_params(
        body,
        _fake_model(context_window=2_000_000, max_output_tokens=65536),
    )
    assert params["max_tokens"] <= 65536
    assert params["max_tokens"] >= 4096  # 不被压成 0


def test_no_max_output_tokens_falls_back_to_context_window():
    """没配 max_output_tokens (老 model) → 上限 = cw (老 BL-MAX-TOKENS-DYNAMIC 行为)"""
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    params = _build_litellm_params(
        body,
        _fake_model(context_window=128_000, max_output_tokens=0),
    )
    # 128K cw, 短 prompt → dyn 接近 128K (老行为)
    assert params["max_tokens"] > 100_000
    assert params["max_tokens"] <= 128_000


def test_nvidia_nim_small_output_cap():
    """NVIDIA NIM Qwen 122B: cw 128K / output 16K — dyn ≤ 16K (NIM cap 比 cw 小很多)"""
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    params = _build_litellm_params(
        body,
        _fake_model(context_window=128_000, max_output_tokens=16384),
    )
    assert params["max_tokens"] <= 16384
    assert params["max_tokens"] >= 4096
