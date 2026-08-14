"""_friendly_upstream_error 单测 — 把 LiteLLM trace 翻译成员工能看的话。

每个真实撞过的错误样本对应一条 case, 防 regression. 错误样本来自:
- CHANGELOG 4-26 / 4-27 entry 里的 "踩坑" 段
- 实测 traceback (gateway log)
"""
from __future__ import annotations

import pytest

from catfish_gateway.errors import friendly_upstream_error as _friendly_upstream_error


class TestProviderSpecific:
    """Provider 特有错误 — 优先匹配, 信号最强."""

    def test_gemini_quota_exhausted(self) -> None:
        raw = "google.api_core.exceptions.ResourceExhausted: 429 Quota exceeded for ..."
        msg = _friendly_upstream_error(raw)
        assert "Gemini" in msg
        assert "配额" in msg
        assert "Qwen" in msg  # 引导员工切

    def test_quota_exceeded_alt_phrasing(self) -> None:
        raw = "litellm.RateLimitError: ... quota was exceeded ..."
        msg = _friendly_upstream_error(raw)
        assert "配额" in msg

    def test_gemini_code_execution_disabled(self) -> None:
        raw = "Tool code_execution disabled for this model"
        msg = _friendly_upstream_error(raw)
        assert "Gemini" in msg
        assert "code_execution" in msg or "工具" in msg


class TestHttp4xx:
    """4xx 客户端错误."""

    def test_429_rate_limit(self) -> None:
        raw = "openai.RateLimitError: Error code: 429 - rate limit exceeded"
        msg = _friendly_upstream_error(raw)
        assert "429" in msg
        assert "频率" in msg or "限流" in msg or "再试" in msg

    def test_429_too_many_requests(self) -> None:
        raw = "HTTP 429: Too Many Requests"
        msg = _friendly_upstream_error(raw)
        assert "429" in msg

    def test_401_unauthorized(self) -> None:
        raw = "openai.AuthenticationError: Error code: 401 - Incorrect API key provided"
        msg = _friendly_upstream_error(raw)
        assert "401" in msg
        assert "Key" in msg or "key" in msg

    def test_401_invalid_api_key_phrasing(self) -> None:
        raw = "Invalid API Key for upstream"
        msg = _friendly_upstream_error(raw)
        assert "Key" in msg or "key" in msg

    def test_403_forbidden(self) -> None:
        raw = "HTTP 403: Forbidden - permission denied"
        msg = _friendly_upstream_error(raw)
        assert "403" in msg
        assert "权限" in msg

    def test_404_model_not_found(self) -> None:
        raw = "openai.NotFoundError: Error code: 404 - The model `qwen3.6-max-preview` does not exist"
        msg = _friendly_upstream_error(raw)
        assert "404" in msg
        assert "模型" in msg or "model" in msg

    def test_404_with_chinese_message(self) -> None:
        """内网平台 502 但消息体 '找不到服务' (实测 4-27 撞过)"""
        raw = "BadGatewayError: 502 found '找不到服务'"
        msg = _friendly_upstream_error(raw)
        # 应优先撞 502 而不是 404 (因为 502 在前面 match)
        # 但 '找不到服务' 也归入 404 类语义 — 这里测出哪个赢了
        # 实际上代码 502 case 在前, 应该 502 赢
        assert "502" in msg or "找不到" in msg or "404" in msg

    def test_400_bad_request_with_image(self) -> None:
        """模型不支持 vision 但收到 image_url — 4-27 实测撞过"""
        raw = "BadRequestError: ... image_url not supported by deepseek_v4_flash"
        msg = _friendly_upstream_error(raw)
        assert "图" in msg or "vision" in msg or "视觉" in msg

    def test_400_bad_request_tool_schema(self) -> None:
        raw = "BadRequestError: ... invalid tool function schema, missing 'name'"
        msg = _friendly_upstream_error(raw)
        assert "工具" in msg or "tool" in msg.lower()

    def test_400_generic(self) -> None:
        raw = "openai.BadRequestError: Error code: 400 - Invalid model parameter"
        msg = _friendly_upstream_error(raw)
        assert "400" in msg


class TestHttp5xx:
    """5xx 上游服务器错误."""

    def test_502_bad_gateway(self) -> None:
        raw = "HTTPStatusError: Server error '502 Bad Gateway'"
        msg = _friendly_upstream_error(raw)
        assert "502" in msg
        assert "网关" in msg or "endpoint" in msg

    def test_503_overloaded(self) -> None:
        raw = "litellm.ServiceUnavailableError: model is overloaded"
        msg = _friendly_upstream_error(raw)
        assert "503" in msg or "过载" in msg

    def test_504_gateway_timeout(self) -> None:
        raw = "504 Gateway Timeout"
        msg = _friendly_upstream_error(raw)
        assert "504" in msg
        assert "超时" in msg or "慢" in msg

    def test_500_internal_server_error(self) -> None:
        raw = "openai.InternalServerError: Connection error."
        # 内部 500 + connection error 都可能撞, 实测先撞 connection error 在前
        msg = _friendly_upstream_error(raw)
        # 接受任意一种翻译, 都是友好的
        assert "上游" in msg or "网络" in msg or "连接" in msg


class TestNetworkLayer:
    """网络层错误 — 内网 VPN 抖动 / Clash 干扰时撞."""

    def test_connection_refused(self) -> None:
        raw = "ConnectionRefusedError: [Errno 61] Connect call failed ('10.10.40.102', 32730)"
        msg = _friendly_upstream_error(raw)
        assert "网络" in msg or "连接" in msg or "VPN" in msg

    def test_cannot_connect(self) -> None:
        raw = "httpx.ConnectError: Cannot connect to host 10.10.40.102:32730"
        msg = _friendly_upstream_error(raw)
        assert "网络" in msg or "连接" in msg or "VPN" in msg

    def test_api_connection_error(self) -> None:
        raw = "openai.APIConnectionError: Connection error."
        msg = _friendly_upstream_error(raw)
        assert "网络" in msg or "连接" in msg

    def test_broken_pipe(self) -> None:
        raw = "BrokenPipeError: [Errno 32] Broken pipe"
        msg = _friendly_upstream_error(raw)
        assert "断" in msg or "重试" in msg or "抖动" in msg

    def test_timeout(self) -> None:
        raw = "asyncio.TimeoutError: connection timed out"
        msg = _friendly_upstream_error(raw)
        assert "超时" in msg or "慢" in msg


class TestFallback:
    """没匹配上的 raw 走兜底 — 截断 200 字."""

    def test_unknown_error_truncated(self) -> None:
        raw = "x" * 300
        msg = _friendly_upstream_error(raw)
        assert len(msg) <= 200

    def test_empty(self) -> None:
        msg = _friendly_upstream_error("")
        assert msg == ""

    def test_short_unknown_passes_through(self) -> None:
        raw = "some weird new error nobody saw before"
        msg = _friendly_upstream_error(raw)
        assert msg == raw  # 没匹配上, 直接给 raw (200 字内)


class TestPriorityOrdering:
    """同时含多个关键词时, 优先级要稳定 — 4xx 比 generic 优先, provider-specific 比通用优先."""

    def test_quota_beats_429(self) -> None:
        """resource_exhausted + 429 同现 → 选 Gemini quota 文案 (信号更强)"""
        raw = "ResourceExhausted: 429 quota exceeded for Gemini"
        msg = _friendly_upstream_error(raw)
        assert "Gemini" in msg
        assert "配额" in msg

    def test_400_image_beats_generic_400(self) -> None:
        """同时含 'image' 和 '400' → 选 image 专用文案"""
        raw = "BadRequestError: 400 - image_url field not supported"
        msg = _friendly_upstream_error(raw)
        assert "图" in msg or "vision" in msg or "视觉" in msg


# ════════════════════════════════════════════════════════════════
# 8/15: 配额烧光 ≠ 限流, 以及 UTC 恢复时间换算
# ════════════════════════════════════════════════════════════════

import datetime as _dt

from catfish_gateway.errors import _QUOTA_EXHAUSTED, localize_reset_hint

# 2026-08-15 现场原文 (阿里云 token-plan 周配额)
现场原文 = (
    "litellm.RateLimitError: RateLimitError: OpenAIException - Error code: 429 - "
    "{'error': {'message': 'Your token-plan 1-week quota has been exhausted. "
    "The quota will reset at 08-14 23:54:00 UTC.', "
    "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}"
)


def test_配额烧光不再被当成限流():
    """现场那条报文里带 429, 原来被判成"等几秒再试" —— 而实际要等 1.4 小时。"""
    out = _friendly_upstream_error(现场原文)
    assert "配额" in out
    assert "等几秒" not in out or "等几秒没用" in out


def test_限流还是限流_没被配额那条抢走():
    out = _friendly_upstream_error("litellm.RateLimitError: Error code: 429 - too many requests")
    assert "调用频率超限" in out


def test_恢复时间换算成本地_期望值手算():
    """UTC 22:54 → 东八区 06:54 次日。期望值是手算的, 不是抄函数输出。"""
    now = _dt.datetime(2026, 8, 14, 22, 32, tzinfo=_dt.timezone.utc)
    hint = localize_reset_hint(现场原文, now=now)
    assert hint is not None
    # 只断言 UTC 侧的事实 (跑测试的机器时区未知): 差 1.4 小时, 且给的是未来时间
    assert "1.4 小时" in hint, hint
    assert "恢复" in hint


def test_已经过了恢复时间就别再说还要等():
    now = _dt.datetime(2026, 8, 15, 3, 0, tzinfo=_dt.timezone.utc)  # 已过 23:54
    hint = localize_reset_hint(现场原文, now=now)
    assert "应已恢复" in hint, hint


def test_没写_utc_的时间不认():
    """没标时区的时间戳换算过去只会更误导, 宁可不给。"""
    assert localize_reset_hint("quota will reset at 08-14 23:54:00") is None


def test_没有恢复时间就返_none():
    assert localize_reset_hint("Insufficient Balance") is None


def test_跨年不会算成半年前():
    """12-31 UTC 的恢复时间, 在 1 月 1 日看应该是"昨天", 不是"今年 12 月"。"""
    now = _dt.datetime(2027, 1, 1, 2, 0, tzinfo=_dt.timezone.utc)
    hint = localize_reset_hint("reset at 12-31 23:00 UTC", now=now)
    assert hint is not None and "应已恢复" in hint, hint


def test_配额词表是_fallback_那组的子集():
    """防漂移: errors.py 这份是给员工看文案用的, fallback.py 那份决定切不切模型。

    两处故意分开 (问的问题不同), 但这边不该出现那边没有的词 —— 否则会出现
    "文案说配额尽了, 而 fallback 认为不是配额问题"这种自相矛盾的状态。
    """
    from catfish_gateway.fallback import _ERROR_KEYWORDS

    多的 = set(_QUOTA_EXHAUSTED) - set(_ERROR_KEYWORDS["insufficient balance"])
    assert not 多的, f"errors.py 多出这些词, fallback.py 没有: {多的}"
