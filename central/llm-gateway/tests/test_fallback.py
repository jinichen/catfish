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


def _config(*models):
    by_name = {m.name: m for m in models}
    return SimpleNamespace(
        models=list(models),
        get_model=lambda n: by_name.get(n),
    )


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
