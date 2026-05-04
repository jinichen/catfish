"""session_summarizer 单测 — BL-F12 (5/4 改成走 gateway loopback HTTP).

之前直接 import litellm 绕过 gateway 自带的 fallback / quota / metrics. 鸿波 5/4 看到
qwen-flash 免费配额耗尽后 summarizer 就死, 让改成走 gateway loopback. 改完 catalog
里的 fallback chain 自动接管 (qwen-flash → gemini-flash 等).

测什么:
  - 正常: gateway 返 200, content 正确解析
  - gateway 返 401/500 等: 返 None, 不抛
  - gateway 网络错: 返 None, 不抛
  - 空 messages 早返 None
  - HTTP body 含 X-Catfish-Skip-Identity: true (防 SOUL/journal 二次注入循环)
  - 模型名是 catfish-public-qwen-flash (catalog 名, 不是上游真模型名)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from catfish_gateway.session_summarizer import _summarize_with_llm


@pytest.fixture
def fake_msgs() -> list[tuple[str, str]]:
    return [
        ("user", "hi"),
        ("assistant", "hello"),
        ("user", "写一份资质周报"),
        ("assistant", "好的, 我先看上次的格式"),
    ]


def _mock_response(status: int, json_body: dict | None = None, text_body: str = ""):
    """构造 httpx.Response 类似对象"""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text_body or ""
    resp.json = MagicMock(return_value=json_body or {})
    return resp


@pytest.mark.asyncio
async def test_happy_path_extracts_content(fake_msgs):
    """gateway 200 → 提取 choices[0].message.content"""
    fake_resp = _mock_response(
        200,
        json_body={
            "choices": [{"message": {"content": "### 资质周报草稿\n讨论了 X."}}]
        },
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess1", 0.0, fake_msgs)

    assert out is not None
    assert "资质周报草稿" in out


@pytest.mark.asyncio
async def test_gateway_returns_500_returns_none(fake_msgs):
    """gateway 5xx → log warn + 返 None, 不抛"""
    fake_resp = _mock_response(500, text_body="internal error")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess1", 0.0, fake_msgs)

    assert out is None


@pytest.mark.asyncio
async def test_network_error_returns_none(fake_msgs):
    """httpx 抛 ConnectError → 返 None"""
    import httpx
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess1", 0.0, fake_msgs)

    assert out is None


@pytest.mark.asyncio
async def test_empty_messages_early_return():
    """没 messages → 早返 None, 不打 gateway"""
    out = await _summarize_with_llm("sess1", 0.0, [])
    assert out is None


@pytest.mark.asyncio
async def test_request_includes_skip_identity_header(fake_msgs):
    """关键: 请求必带 X-Catfish-Skip-Identity: true 防 SOUL/journal 循环注入"""
    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess1", 0.0, fake_msgs)

    # 验证 post 调用的 headers
    call_args = mock_client.post.call_args
    headers = call_args.kwargs.get("headers") or {}
    assert headers.get("X-Catfish-Skip-Identity") == "true", (
        "必须有 skip-identity header 防 summarizer 自己看自己写的 journal 死循环"
    )


@pytest.mark.asyncio
async def test_request_uses_catalog_model_name(fake_msgs):
    """关键: 用 catalog 模型名 catfish-public-qwen-flash, 不写死上游真名.
    gateway 看到 catalog 名自动走 fallback chain (qwen 挂时 gemini-flash 接管).
    """
    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess1", 0.0, fake_msgs)

    call_args = mock_client.post.call_args
    json_body = call_args.kwargs.get("json") or {}
    assert json_body.get("model") == "catfish-public-qwen-flash"
    # 不该写死成上游模型名 (e.g. openai/qwen3.5-flash-...)
    assert "/" not in json_body["model"]


@pytest.mark.asyncio
async def test_request_uses_dev_token(fake_msgs, monkeypatch):
    """auth header 用 CATFISH_DEV_TOKEN env"""
    monkeypatch.setenv("CATFISH_DEV_TOKEN", "fake-test-token-xxx")

    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess1", 0.0, fake_msgs)

    headers = mock_client.post.call_args.kwargs.get("headers") or {}
    assert headers.get("Authorization") == "Bearer fake-test-token-xxx"


@pytest.mark.asyncio
async def test_request_uses_port_env(fake_msgs, monkeypatch):
    """gateway 端口可改 (PORT env)"""
    monkeypatch.setenv("PORT", "9001")

    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess1", 0.0, fake_msgs)

    url = mock_client.post.call_args.args[0]
    assert "127.0.0.1:9001" in url
