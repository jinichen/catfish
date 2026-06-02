"""fallback 模块单测 —— 模型链 + 错误判定 + 重试编排。

边界覆盖:
    1. should_fallback —— int (status) / str (keyword) / 异常的多种 status 提取
    2. resolve_chain —— 跳过自引用 / 不存在 / api_key 没配 / mode 不一致
    3. with_fallback —— 主成功 / 主败 chain 救回 / 全败 / max_hops 截断 /
                         非 fallback-able 错误直接抛
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from catfish_gateway.fallback import (
    resolve_chain,
    should_fallback,
    with_fallback,
)


# ---------- helpers ----------

class FakeRateLimit(Exception):
    status_code = 429


class FakeBadRequest(Exception):
    status_code = 400


class FakeUnauthorized(Exception):
    def __init__(self, msg: str = "Unauthorized") -> None:
        super().__init__(msg)
        self.response = SimpleNamespace(status_code=401)


def _model(name, *, mode="chat", api_key_configured=True, fallback=None):
    """构造跟 ModelConfig duck-type 兼容的对象"""
    upstream = SimpleNamespace(
        api_key_env=f"{name}_KEY",
        is_available=api_key_configured,
    )
    return SimpleNamespace(
        name=name,
        mode=mode,
        upstream=upstream,
        fallback=fallback,
    )


def _fb(chain, on_errors=None, max_hops=2):
    """构造 FallbackConfig duck-type"""
    return SimpleNamespace(
        chain=chain,
        on_errors=on_errors or [429, 503, 504, "timeout"],
        max_hops=max_hops,
    )


def _config(*models, auto_fallback=True):
    """BL-FALLBACK-TOGGLE (5/16): 默认 auto_fallback=True 让现有测试继续测 fallback 行为.

    单独有一个测试覆盖 auto_fallback=False 时 with_fallback 直抛, 不走 chain.
    """
    by_name = {m.name: m for m in models}
    return SimpleNamespace(
        models=list(models),
        get_model=lambda n: by_name.get(n),
        auto_fallback=auto_fallback,
    )


# ---------- BL-FALLBACK-TOGGLE (默认关) ----------


def test_fallback_disabled_by_default_raises_primary_error() -> None:
    """auto_fallback=False (新默认) → primary 错直抛, 不走 chain."""
    a = _model("a", fallback=SimpleNamespace(
        chain=["b"], on_errors=[429], max_hops=2,
    ))
    b = _model("b")
    cfg = _config(a, b, auto_fallback=False)  # 显式关

    async def invoke(m):
        if m.name == "a":
            raise FakeRateLimit()
        return f"served by {m.name}"

    # 应该抛 FakeRateLimit, 不走 chain 到 b
    with pytest.raises(FakeRateLimit):
        asyncio.run(with_fallback(cfg, a, invoke))


def test_fallback_enabled_via_env_override(monkeypatch) -> None:
    """env CATFISH_AUTO_FALLBACK=1 → 即使 yaml 关也开."""
    monkeypatch.setenv("CATFISH_AUTO_FALLBACK", "1")
    a = _model("a", fallback=SimpleNamespace(
        chain=["b"], on_errors=[429], max_hops=2,
    ))
    b = _model("b")
    cfg = _config(a, b, auto_fallback=False)  # yaml 关, env 开

    async def invoke(m):
        if m.name == "a":
            raise FakeRateLimit()
        return f"served by {m.name}"

    result, used, _ = asyncio.run(with_fallback(cfg, a, invoke))
    assert used.name == "b"  # env 强制开了 fallback, 走到 b
    assert result == "served by b"


# ---------- should_fallback ----------

def test_should_fallback_int_status_via_attr() -> None:
    assert should_fallback(FakeRateLimit("rate limited"), [429]) is True


def test_should_fallback_int_status_via_response() -> None:
    assert should_fallback(FakeUnauthorized("nope"), [401]) is True


def test_should_fallback_int_no_match() -> None:
    assert should_fallback(FakeBadRequest("schema bad"), [429, 503]) is False


def test_should_fallback_string_keyword() -> None:
    e = Exception("Connection timeout after 60s")
    assert should_fallback(e, ["timeout"]) is True


def test_should_fallback_string_alias() -> None:
    """timeout 别名: timed out"""
    e = Exception("Request timed out")
    assert should_fallback(e, ["timeout"]) is True


def test_should_fallback_string_quota_alias() -> None:
    """rate limit 别名: quota / RESOURCE_EXHAUSTED"""
    e = Exception("RESOURCE_EXHAUSTED — Quota exceeded")
    assert should_fallback(e, ["rate limit"]) is True


def test_should_fallback_status_in_message() -> None:
    """状态码嵌在异常消息里也认"""
    e = Exception("API call failed: HTTP 503 Service Unavailable")
    assert should_fallback(e, [503]) is True


def test_should_fallback_no_match_returns_false() -> None:
    e = Exception("schema validation error")
    assert should_fallback(e, [429, "timeout"]) is False


def test_should_fallback_empty_on_errors() -> None:
    e = FakeRateLimit("nope")
    assert should_fallback(e, []) is False


# ---------- resolve_chain ----------

def test_resolve_chain_basic() -> None:
    a = _model("a", fallback=_fb(["b", "c"]))
    b = _model("b")
    c = _model("c")
    cfg = _config(a, b, c)
    out = resolve_chain(cfg, a)
    assert [m.name for m in out] == ["b", "c"]


def test_resolve_chain_skips_self_reference() -> None:
    a = _model("a", fallback=_fb(["a", "b"]))
    b = _model("b")
    cfg = _config(a, b)
    out = resolve_chain(cfg, a)
    assert [m.name for m in out] == ["b"]


def test_resolve_chain_skips_unknown() -> None:
    a = _model("a", fallback=_fb(["nope", "b"]))
    b = _model("b")
    cfg = _config(a, b)
    out = resolve_chain(cfg, a)
    assert [m.name for m in out] == ["b"]


def test_resolve_chain_skips_no_api_key() -> None:
    a = _model("a", fallback=_fb(["b", "c"]))
    b = _model("b", api_key_configured=False)
    c = _model("c")
    cfg = _config(a, b, c)
    out = resolve_chain(cfg, a)
    assert [m.name for m in out] == ["c"]


def test_resolve_chain_skips_mode_mismatch() -> None:
    """chat 模型不应该 fallback 到 embedding 模型"""
    a = _model("a", fallback=_fb(["b", "c"]))
    b = _model("b", mode="embedding")
    c = _model("c", mode="chat")
    cfg = _config(a, b, c)
    out = resolve_chain(cfg, a)
    assert [m.name for m in out] == ["c"]


def test_resolve_chain_no_fallback_returns_empty() -> None:
    a = _model("a", fallback=None)
    cfg = _config(a)
    assert resolve_chain(cfg, a) == []


def test_resolve_chain_empty_chain_returns_empty() -> None:
    a = _model("a", fallback=_fb([]))
    cfg = _config(a)
    assert resolve_chain(cfg, a) == []


# ---------- with_fallback: 编排逻辑 ----------

def test_with_fallback_primary_succeeds() -> None:
    """主模型成功 → chain 不动用"""
    primary = _model("a", fallback=_fb(["b"]))
    cfg = _config(primary, _model("b"))

    async def invoke(m):
        return f"ok-{m.name}"

    result, used, attempts = asyncio.run(with_fallback(cfg, primary, invoke))
    assert result == "ok-a"
    assert used.name == "a"
    assert attempts == ["a=ok"]


def test_with_fallback_no_chain_re_raises() -> None:
    """没配 fallback → 异常直接抛"""
    primary = _model("a", fallback=None)
    cfg = _config(primary)

    async def invoke(m):
        raise FakeRateLimit("limited")

    with pytest.raises(FakeRateLimit):
        asyncio.run(with_fallback(cfg, primary, invoke))


def test_with_fallback_non_fallbackable_error_re_raises() -> None:
    """400 / schema 错误不在 on_errors 里, 不切, 直接抛"""
    primary = _model("a", fallback=_fb(["b"], on_errors=[429]))
    cfg = _config(primary, _model("b"))

    async def invoke(m):
        raise FakeBadRequest("schema invalid")

    with pytest.raises(FakeBadRequest):
        asyncio.run(with_fallback(cfg, primary, invoke))


def test_with_fallback_chain_succeeds_on_second() -> None:
    """主败, chain[0] 救回"""
    primary = _model("a", fallback=_fb(["b", "c"]))
    cfg = _config(primary, _model("b"), _model("c"))

    async def invoke(m):
        if m.name == "a":
            raise FakeRateLimit("a rate-limited")
        return f"ok-{m.name}"

    result, used, attempts = asyncio.run(with_fallback(cfg, primary, invoke))
    assert result == "ok-b"
    assert used.name == "b"
    assert attempts == ["a=err:FakeRateLimit", "b=ok"]


def test_with_fallback_chain_skips_to_third() -> None:
    """主败, chain[0] 也败, chain[1] 救回"""
    primary = _model("a", fallback=_fb(["b", "c"], max_hops=3))
    cfg = _config(primary, _model("b"), _model("c"))

    async def invoke(m):
        if m.name in ("a", "b"):
            raise FakeRateLimit(f"{m.name} 429")
        return f"ok-{m.name}"

    result, used, _attempts = asyncio.run(with_fallback(cfg, primary, invoke))
    assert used.name == "c"
    assert result == "ok-c"


def test_with_fallback_max_hops_truncates() -> None:
    """chain 比 max_hops 长 → 只跑 max_hops 个"""
    primary = _model("a", fallback=_fb(["b", "c", "d"], max_hops=1))
    cfg = _config(primary, _model("b"), _model("c"), _model("d"))

    invoked_for = []

    async def invoke(m):
        invoked_for.append(m.name)
        raise FakeRateLimit(f"{m.name} 429")

    with pytest.raises(FakeRateLimit):
        asyncio.run(with_fallback(cfg, primary, invoke))

    # 应该试 a (主) + b (1 hop). c/d 不动
    assert invoked_for == ["a", "b"]


def test_with_fallback_all_fail_raises_last_error() -> None:
    """全失败 → 抛最后一个 (最新) 错误"""
    primary = _model("a", fallback=_fb(["b"]))
    cfg = _config(primary, _model("b"))

    last_err = None

    async def invoke(m):
        nonlocal last_err
        e = FakeRateLimit(f"{m.name} 429")
        last_err = e
        raise e

    with pytest.raises(FakeRateLimit) as exc_info:
        asyncio.run(with_fallback(cfg, primary, invoke))
    # 抛的是 b 的错 (最新), 不是 a 的
    assert "b 429" in str(exc_info.value)


def test_with_fallback_candidate_non_fallbackable_propagates() -> None:
    """chain 中某个候选挂了但是非 fallback-able 错 → 直接抛, 不再继续 chain"""
    primary = _model("a", fallback=_fb(["b", "c"], on_errors=[429]))
    cfg = _config(primary, _model("b"), _model("c"))

    invoked = []

    async def invoke(m):
        invoked.append(m.name)
        if m.name == "a":
            raise FakeRateLimit("a")
        if m.name == "b":
            raise FakeBadRequest("b: schema bad")  # 非 fallback-able
        return "ok-c"

    with pytest.raises(FakeBadRequest):
        asyncio.run(with_fallback(cfg, primary, invoke))

    # 应该试 a + b (b 挂了非 fallback-able), c 不试
    assert invoked == ["a", "b"]


# ── 6/2 BL-TRANSIENT-NETWORK-RETRY (鸿波 6/2 下午 audit) ────────────────────


from catfish_gateway.fallback import (
    _call_with_transient_retry,
    _is_transient_network_error,
)


class FakeServerDisconnected(Exception):
    """模拟 aiohttp.client_exceptions.ServerDisconnectedError (类名匹配)."""
    pass
FakeServerDisconnected.__name__ = "ServerDisconnectedError"


class FakeConnectionReset(Exception):
    pass
FakeConnectionReset.__name__ = "ConnectionResetError"


class FakeLitellmInternal(Exception):
    """模拟 litellm.InternalServerError, 消息含 'Connection error' (鸿波 6/2 log 真错).

    litellm 把网络层错包装成 InternalServerError + 字符串, 类型层抓不到, 必须看消息.
    """
    pass


class FakeUnrelated400(Exception):
    """模拟业务错 (e.g. schema 错 400), 不该被 transient retry."""
    status_code = 400


def test_transient_detect_class_name() -> None:
    """类名匹配: ServerDisconnectedError / ConnectionResetError 真识别."""
    assert _is_transient_network_error(FakeServerDisconnected("idle gone")) is True
    assert _is_transient_network_error(FakeConnectionReset("[Errno 54]")) is True


def test_transient_detect_message_substring() -> None:
    """消息层 fallback — litellm 包装的 'Connection error' 字符串 (6/2 鸿波 log 真错)."""
    e = FakeLitellmInternal(
        "litellm.InternalServerError: InternalServerError: OpenAIException - Connection error."
    )
    assert _is_transient_network_error(e) is True

    e2 = Exception("Server disconnected while reading")
    assert _is_transient_network_error(e2) is True

    e3 = Exception("[Errno 54] Connection reset by peer")
    assert _is_transient_network_error(e3) is True


def test_transient_detect_cause_chain() -> None:
    """嵌套异常链 — litellm 套 openai 套 httpx 套 aiohttp, 最内层才是 transient."""
    inner = FakeServerDisconnected("aiohttp keep-alive idle")
    middle = Exception("httpx ReadError wrap")
    outer = Exception("litellm wrap")
    try:
        try:
            try:
                raise inner
            except Exception as e1:
                raise middle from e1
        except Exception as e2:
            raise outer from e2
    except Exception as final_exc:
        assert _is_transient_network_error(final_exc) is True


def test_transient_does_not_match_business_errors() -> None:
    """业务错 (4xx/429/500) 不该被识别为 transient."""
    assert _is_transient_network_error(FakeUnrelated400("schema bad")) is False
    assert _is_transient_network_error(FakeRateLimit("rate limited")) is False
    assert _is_transient_network_error(Exception("API error 503")) is False
    assert _is_transient_network_error(Exception("Bad request - missing field")) is False


def test_call_with_transient_retry_succeeds_on_second_attempt() -> None:
    """模拟鸿波 6/2 真场景: 第一次 transient, 第二次新连接好."""
    m = _model("private-main")
    attempts = []

    async def invoke(model):
        attempts.append(model.name)
        if len(attempts) == 1:
            raise FakeServerDisconnected("keep-alive idle gone")
        return "ok"

    result = asyncio.run(_call_with_transient_retry(invoke, m))
    assert result == "ok"
    assert len(attempts) == 2  # 重试了 1 次


def test_call_with_transient_retry_does_not_retry_business_error() -> None:
    """业务错 (e.g. 400 schema 错) 不该 retry, 第一次抛立即上去."""
    m = _model("private-main")
    attempts = []

    async def invoke(model):
        attempts.append(model.name)
        raise FakeUnrelated400("schema bad")

    with pytest.raises(FakeUnrelated400):
        asyncio.run(_call_with_transient_retry(invoke, m))
    assert len(attempts) == 1  # 没重试


def test_call_with_transient_retry_second_failure_propagates() -> None:
    """第二次仍 transient → 抛上去 (说明真上游挂了, 不只是残连)."""
    m = _model("private-main")
    attempts = []

    async def invoke(model):
        attempts.append(model.name)
        raise FakeServerDisconnected("really down")

    with pytest.raises(FakeServerDisconnected):
        asyncio.run(_call_with_transient_retry(invoke, m))
    assert len(attempts) == 2  # 重试了 1 次, 仍失败


def test_with_fallback_transient_retry_saves_primary() -> None:
    """end-to-end: with_fallback 看到 primary transient retry 成功 → 不走 chain.

    鸿波 6/2 真场景: catfish-private-main 第一次 ServerDisconnectedError, 第二次 OK
    → 不该切到公网 fallback (虽然 chain 配了但不需要).
    """
    a = _model("a", fallback=_fb(["b"]))
    b = _model("b")
    cfg = _config(a, b, auto_fallback=True)
    attempts = []

    async def invoke(m):
        attempts.append(m.name)
        if m.name == "a" and len(attempts) == 1:
            raise FakeServerDisconnected("idle")
        return f"served by {m.name}"

    result, used, _hops = asyncio.run(with_fallback(cfg, a, invoke))
    assert used.name == "a"   # primary 成功 (第二次), 没切到 b
    assert result == "served by a"
    assert attempts == ["a", "a"]  # transient retry 真发生


def test_with_fallback_transient_then_real_failure_goes_to_chain() -> None:
    """primary 两次都挂 (第二次仍 transient = 真上游 down) → 切到 chain.

    on_errors 用 should_fallback 真匹配的 keyword ("Connection error" / "Server
    disconnected"), 异常 msg 也含这些字串 — should_fallback 子串匹配能命中切链.
    """
    a = _model("a", fallback=_fb(["b"], on_errors=[
        "Connection error", "Server disconnected", 429,
    ]))
    b = _model("b")
    cfg = _config(a, b, auto_fallback=True)
    attempts = []

    async def invoke(m):
        attempts.append(m.name)
        if m.name == "a":
            # msg 含 "Server disconnected" 让 should_fallback 命中 (transient retry 内自己用类名匹配)
            raise FakeServerDisconnected("Server disconnected: upstream really down")
        return f"served by {m.name}"

    result, used, _hops = asyncio.run(with_fallback(cfg, a, invoke))
    assert used.name == "b"
    assert attempts == ["a", "a", "b"]  # primary transient retry 2 次, 然后 chain b
