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


def _fake_picker_model(name: str = "catfish-public-qwen-flash"):
    """构造 fake picker 返的 ModelConfig (只用 name)"""
    m = MagicMock()
    m.name = name
    m.tier = "public"
    return m


def _patch_picker(model_name: str = "catfish-public-qwen-flash"):
    """patch pick_internal_models_ordered 返一个候选, 防 picker 因 env 没设而返空 list"""
    return patch(
        "catfish_gateway.internal_models.pick_internal_models_ordered",
        return_value=[_fake_picker_model(model_name)],
    )


@pytest.mark.asyncio
async def test_happy_path_extracts_content(fake_msgs):
    """gateway 200 → 提取 choices[0].message.content"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
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

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess_happy", 0.0, fake_msgs)

    assert out is not None
    assert "资质周报草稿" in out


@pytest.mark.asyncio
async def test_gateway_returns_500_returns_none(fake_msgs):
    """gateway 5xx → log warn + 返 None, 不抛"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    fake_resp = _mock_response(500, text_body="internal error")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess_500_old", 0.0, fake_msgs)

    assert out is None


@pytest.mark.asyncio
async def test_network_error_returns_none(fake_msgs):
    """httpx 抛 ConnectError → 返 None (全候选试完后)"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    import httpx
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess_neterr", 0.0, fake_msgs)

    assert out is None


@pytest.mark.asyncio
async def test_empty_messages_early_return():
    """没 messages → 早返 None, 不打 gateway"""
    out = await _summarize_with_llm("sess_empty", 0.0, [])
    assert out is None


@pytest.mark.asyncio
async def test_request_includes_skip_identity_header(fake_msgs):
    """关键: 请求必带 X-Catfish-Skip-Identity: true 防 SOUL/journal 循环注入"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess_skip", 0.0, fake_msgs)

    # 验证 post 调用的 headers
    call_args = mock_client.post.call_args
    headers = call_args.kwargs.get("headers") or {}
    assert headers.get("X-Catfish-Skip-Identity") == "true", (
        "必须有 skip-identity header 防 summarizer 自己看自己写的 journal 死循环"
    )


@pytest.mark.asyncio
async def test_request_uses_catalog_model_name(fake_msgs):
    """关键: 用 catalog 模型名 (跟 yaml 里 name 一致), 不写死上游真名.
    BL-F14 改后: 走 pick_internal_models_ordered, 实际选啥跟 catalog 走.
    """
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker("catfish-private-main"), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess_catname", 0.0, fake_msgs)

    json_body = mock_client.post.call_args.kwargs.get("json") or {}
    model_name = json_body.get("model", "")
    assert model_name.startswith("catfish-"), (
        f"模型名 {model_name!r} 不是 catalog 名形态."
    )
    assert "/" not in model_name


@pytest.mark.asyncio
async def test_request_uses_dev_token(fake_msgs, monkeypatch):
    """BL-FIX37 (5/10): summarizer 用 ensure_internal_dev_token() 拿进程内 random,
    不再走 CATFISH_DEV_TOKEN env. test 改成: 取出 internal token, 断言 header 用它."""
    from catfish_gateway import session_summarizer
    from catfish_gateway.auth.dev_token import ensure_internal_dev_token
    session_summarizer._SESSION_COOL_DOWN.clear()
    expected_token = ensure_internal_dev_token()
    assert expected_token, "internal dev token 未生成"

    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess_token", 0.0, fake_msgs)

    headers = mock_client.post.call_args.kwargs.get("headers") or {}
    assert headers.get("Authorization") == f"Bearer {expected_token}"


@pytest.mark.asyncio
async def test_request_uses_port_env(fake_msgs, monkeypatch):
    """gateway 端口可改 (PORT env)"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    monkeypatch.setenv("PORT", "9001")

    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess_port", 0.0, fake_msgs)

    url = mock_client.post.call_args.args[0]
    assert "127.0.0.1:9001" in url


# ─── BL-F15 候选切换 + 冷却 (5/5 修 quota_exceeded 死循环) ───


@pytest.mark.asyncio
async def test_429_quota_switches_to_next_candidate(fake_msgs, monkeypatch):
    """收到 429 quota_exceeded → 自动切下一个候选"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()  # 清冷却防干扰

    # 第 1 次 429, 第 2 次 200
    resp_429 = _mock_response(
        429,
        text_body='{"detail":{"error":"quota_exceeded","model":"catfish-private-main"}}',
    )
    resp_200 = _mock_response(
        200, json_body={"choices": [{"message": {"content": "总结好了"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[resp_429, resp_200])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess_quota_switch", 0.0, fake_msgs)

    assert out == "总结好了"
    # 调了 2 次 (主候选 429, 第 2 候选 200)
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_all_candidates_429_marks_cool_down(fake_msgs, monkeypatch):
    """所有候选都 429 → 标 session 冷却, 返 None"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()

    resp_429 = _mock_response(
        429,
        text_body='{"detail":{"error":"quota_exceeded"}}',
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp_429)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    sid = "sess_all_429"
    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(sid, 0.0, fake_msgs)

    assert out is None
    # 该 session 应已被标记冷却
    assert sid in session_summarizer._SESSION_COOL_DOWN
    assert session_summarizer._SESSION_COOL_DOWN[sid] > 0


@pytest.mark.asyncio
async def test_cool_down_skips_immediate_retry(fake_msgs):
    """冷却中的 session 直接返 None, 不打 gateway"""
    from catfish_gateway import session_summarizer
    import time
    sid = "sess_cool"
    session_summarizer._SESSION_COOL_DOWN[sid] = time.time() + 100  # 冷却中

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()  # 不该被调
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(sid, 0.0, fake_msgs)

    assert out is None
    mock_client.post.assert_not_called()  # 冷却中, 没打 gateway


@pytest.mark.asyncio
async def test_cool_down_expires_allows_retry(fake_msgs):
    """冷却过期后重新允许"""
    from catfish_gateway import session_summarizer
    import time
    sid = "sess_expired"
    session_summarizer._SESSION_COOL_DOWN[sid] = time.time() - 10  # 已过期

    resp_200 = _mock_response(
        200, json_body={"choices": [{"message": {"content": "成功"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp_200)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(sid, 0.0, fake_msgs)

    assert out == "成功"
    # 过期记录应被清
    assert sid not in session_summarizer._SESSION_COOL_DOWN


@pytest.mark.asyncio
async def test_500_does_not_switch_candidates(fake_msgs):
    """500 (非 quota) 不应切候选, 直接放弃 (大概率不是模型问题)"""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()

    resp_500 = _mock_response(500, text_body="internal error")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp_500)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm("sess_500", 0.0, fake_msgs)

    assert out is None
    # 只调 1 次 (没切候选)
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_request_includes_internal_header(fake_msgs):
    """BL-F17: summarizer 必带 X-Catfish-Internal: true 让 gateway 跳 quota 计算.
    不带这个 header 会导致 summarizer 后台总结消耗员工 user_day quota → 员工撞墙.
    """
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()

    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_picker(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm("sess_internal_hdr", 0.0, fake_msgs)

    headers = mock_client.post.call_args.kwargs.get("headers") or {}
    assert headers.get("X-Catfish-Internal") == "true", (
        "summarizer 后台 housekeeping 必须打 internal header, "
        "不该消耗员工 user_day quota (鸿波 5/5 explicit BL-F17)"
    )


# ─── BL-SESSION-MGMT B (5/15): _extract_short_title + _update_session_title ──


from catfish_gateway.session_summarizer import _extract_short_title, _update_session_title


def test_extract_short_title_first_sentence():
    summary = "员工要找 ISO 9001 资质. 已确认下次会议在 5/18, 由高江祥代表. 拍照存档."
    assert _extract_short_title(summary) == "员工要找 ISO 9001 资质"


def test_extract_short_title_english_punctuation():
    summary = "User asked about quota usage. Decided to check audit log."
    assert _extract_short_title(summary) == "User asked about quota usage"


def test_extract_short_title_truncate_to_30():
    summary = "员工 决定 把 这 个 很 长 很 长 很 长 很 长 很 长 很 长 的 话 一 直 写 下 去 没 标 点"
    title = _extract_short_title(summary)
    assert len(title) <= 30


def test_extract_short_title_empty():
    assert _extract_short_title("") == ""
    assert _extract_short_title("   ") == ""


def test_extract_short_title_no_punctuation():
    """全无标点 — 截前 30 字"""
    summary = "员工" * 20
    title = _extract_short_title(summary)
    assert len(title) <= 30
    assert title.startswith("员工")


def test_extract_short_title_chinese_punctuation():
    summary = "员工要做周报！下面是要点。"
    assert _extract_short_title(summary) == "员工要做周报"


def test_update_session_title_db_missing(monkeypatch, tmp_path):
    """state.db 不存在 → 返 False 不挂"""
    from catfish_gateway import session_summarizer
    monkeypatch.setattr(session_summarizer, "STATE_DB", tmp_path / "nope.db")
    assert _update_session_title("sess_x", "test title") is False


def test_update_session_title_real_write(monkeypatch, tmp_path):
    """造个 fake state.db, 写 title, 验真写进去 + 只在 NULL/空时 update"""
    import sqlite3
    from catfish_gateway import session_summarizer

    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO sessions (id, title) VALUES (?, NULL)", ("sess_empty",))
    conn.execute("INSERT INTO sessions (id, title) VALUES (?, '')", ("sess_blank",))
    conn.execute("INSERT INTO sessions (id, title) VALUES (?, '已有名')", ("sess_named",))
    conn.commit()
    conn.close()

    monkeypatch.setattr(session_summarizer, "STATE_DB", db)

    # NULL → 写
    assert _update_session_title("sess_empty", "新标题") is True
    # 空字符串 → 写
    assert _update_session_title("sess_blank", "新标题") is True
    # 已有名 → 不覆盖, 返 False (rowcount=0)
    assert _update_session_title("sess_named", "强改") is False

    # 验数据库
    conn = sqlite3.connect(str(db))
    rows = dict(conn.execute("SELECT id, title FROM sessions").fetchall())
    conn.close()
    assert rows["sess_empty"] == "新标题"
    assert rows["sess_blank"] == "新标题"
    assert rows["sess_named"] == "已有名"  # 没被覆盖
