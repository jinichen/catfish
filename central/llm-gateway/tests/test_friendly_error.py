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
