"""session_summarizer 单测.

# 历史

  - BL-F12 (5/4): 改成走 gateway loopback HTTP, 享 catalog fallback + quota + metrics.
  - BL-F15 (5/5): 修 quota_exceeded 死循环, 收 429 切下一候选 + cooldown 标记.
  - BL-F17 (5/5): 加 X-Catfish-Internal: true 让 gateway 跳 quota.
  - BL-FIX37 (5/10): 用 ensure_internal_dev_token() (进程内 random), 不再走 env.

# 当前规则 (BL-INTERNAL-MODEL-FOLLOW-USER-FULL 5/17 拍板)

严格 **1 candidate** — 员工选哪个 model, summarize 用同款, **不 fallback**:
  - 收 429 / 5xx → 标 session 5min cooldown, 不再 hammer; 不切候选 (没下一个)
  - 拿不到 origin_model (老 session / 拒访 sqlite) → 跳过 summarize
  - origin_model 在 catalog 不可达 → 跳过 (等员工下次用可达 model)

# 这个 test 覆盖什么

  - 正常: gateway 200, content 解析正确
  - 没 origin_model → skip 不打 gateway
  - 不可达 model → skip 不打 gateway
  - 5xx / 网络错 → 返 None + 标 cooldown (不切候选)
  - 429 → 返 None + 标 cooldown (不切候选)
  - 空 messages → 早返 None
  - 请求 headers 含 Skip-Identity / Internal / Authorization=internal dev token
  - 请求用 catalog 名 (不是上游真名)
  - cooldown 中跳过, cooldown 过期后允许重试

历史多候选切换测试 (BL-F15 / BL-F19 时代 test_429_quota_switches_to_next_candidate,
test_all_candidates_429_marks_cool_down) 已删 — 跟 follow-user 严格 1 candidate
规则矛盾, 行为永远走不到.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from catfish_gateway.session_summarizer import _summarize_with_llm


# ── 公共桩 ─────────────────────────────────────────────


@pytest.fixture
def fake_msgs() -> list[tuple[str, str]]:
    return [
        ("user", "hi"),
        ("assistant", "hello"),
        ("user", "写一份资质周报"),
        ("assistant", "好的, 我先看上次的格式"),
    ]


def _mock_response(status: int, json_body: dict | None = None, text_body: str = ""):
    """构造 httpx.Response 类似对象."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text_body or ""
    resp.json = MagicMock(return_value=json_body or {})
    return resp


def _patch_catalog(model_name: str = "catfish-public-qwen-flash"):
    """patch load_config 返个 catalog 含 model_name 这条 chat 模型, 可达.

    BL-INTERNAL-MODEL-FOLLOW-USER (5/17): summarizer 不再用 picker, 而是从 catalog
    按 origin_model 名字精确找. 测试要构造个含目标 model 的假 catalog.
    """
    fake_model = SimpleNamespace(
        name=model_name,
        mode="chat",
        upstream=SimpleNamespace(is_available=True),
    )
    fake_config = SimpleNamespace(models=[fake_model])
    # load_config 在 session_summarizer 里 lazy import `from .config import load_config`
    # 所以要 patch 源模块 catfish_gateway.config.load_config.
    return patch(
        "catfish_gateway.config.load_config",
        return_value=fake_config,
    )


# ── 正常路径 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_extracts_content(fake_msgs):
    """gateway 200 → 提取 choices[0].message.content."""
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

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(
            "sess_happy", 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    assert out is not None
    assert "资质周报草稿" in out


# ── 早退路径: 没 origin_model / 不可达 model ─────────────


@pytest.mark.asyncio
async def test_no_origin_model_skips(fake_msgs):
    """BL-INTERNAL-MODEL-FOLLOW-USER-FULL: 没传 origin_model → skip 不打 gateway."""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()  # 不该被调
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(
            "sess_no_origin", 0.0, fake_msgs, origin_model=None,
        )

    assert out is None
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_model_not_in_catalog_skips(fake_msgs):
    """origin_model 名字在 catalog 找不到 (可能员工切了 model 但 catalog reload 没跟上)
    → skip, 不 fallback 别的 model."""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()  # 不该被调
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_catalog("catfish-public-qwen-flash"), patch(
        "httpx.AsyncClient", return_value=mock_client,
    ):
        out = await _summarize_with_llm(
            "sess_bad_model", 0.0, fake_msgs,
            origin_model="catfish-doesnt-exist",
        )

    assert out is None
    mock_client.post.assert_not_called()


# ── 错误路径: 5xx / 429 / 网络错 → 返 None + cooldown ─────


@pytest.mark.asyncio
async def test_gateway_returns_500_marks_cooldown(fake_msgs):
    """BL-INTERNAL-MODEL-FOLLOW-USER-FULL: 5xx → 返 None + 标 cooldown, 不切候选."""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    fake_resp = _mock_response(500, text_body="internal error")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    sid = "sess_500"
    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(
            sid, 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    assert out is None
    # 只调 1 次 (严格 1 candidate, 不切)
    assert mock_client.post.call_count == 1
    # 该 session 被标 cooldown
    assert sid in session_summarizer._SESSION_COOL_DOWN


@pytest.mark.asyncio
async def test_gateway_returns_429_marks_cooldown(fake_msgs):
    """BL-INTERNAL-MODEL-FOLLOW-USER-FULL: 429 quota_exceeded → cooldown, 不切候选.

    跟老 BL-F15 时代不同 — 老代码会 try 下一个候选, 现在严格 1 candidate 不切.
    员工 quota 满了就先撞墙, 等 cooldown 过 + 员工清完 quota 再 retry.
    """
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    resp_429 = _mock_response(
        429,
        text_body='{"detail":{"error":"quota_exceeded","model":"catfish-public-qwen-flash"}}',
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp_429)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    sid = "sess_429"
    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(
            sid, 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    assert out is None
    assert mock_client.post.call_count == 1  # 不切候选
    assert sid in session_summarizer._SESSION_COOL_DOWN


@pytest.mark.asyncio
async def test_network_error_marks_cooldown(fake_msgs):
    """httpx 抛 ConnectError → 返 None + cooldown."""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    import httpx
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    sid = "sess_neterr"
    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(
            sid, 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    assert out is None
    assert sid in session_summarizer._SESSION_COOL_DOWN


@pytest.mark.asyncio
async def test_empty_messages_early_return():
    """没 messages → 早返 None, 不打 gateway, 也不查 catalog."""
    out = await _summarize_with_llm(
        "sess_empty", 0.0, [],
        origin_model="catfish-public-qwen-flash",
    )
    assert out is None


# ── 请求格式: headers / token / port / model name ────────


@pytest.mark.asyncio
async def test_request_includes_skip_identity_header(fake_msgs):
    """请求必带 X-Catfish-Skip-Identity: true 防 SOUL/journal 二次注入循环."""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm(
            "sess_skip", 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    headers = mock_client.post.call_args.kwargs.get("headers") or {}
    assert headers.get("X-Catfish-Skip-Identity") == "true", (
        "必须有 skip-identity header 防 summarizer 自己看自己写的 journal 死循环"
    )


@pytest.mark.asyncio
async def test_request_uses_origin_model_name(fake_msgs):
    """BL-INTERNAL-MODEL-FOLLOW-USER-FULL: 用员工传的 origin_model 名, 不写死."""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()
    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_catalog("catfish-private-main"), patch(
        "httpx.AsyncClient", return_value=mock_client,
    ):
        await _summarize_with_llm(
            "sess_catname", 0.0, fake_msgs,
            origin_model="catfish-private-main",
        )

    json_body = mock_client.post.call_args.kwargs.get("json") or {}
    model_name = json_body.get("model", "")
    assert model_name == "catfish-private-main", (
        f"应原样用员工选的 catalog 名, 实际 {model_name!r}"
    )
    assert "/" not in model_name


@pytest.mark.asyncio
async def test_request_uses_dev_token(fake_msgs):
    """BL-FIX37 (5/10): summarizer 用 ensure_internal_dev_token() (进程内 random)."""
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

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm(
            "sess_token", 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    headers = mock_client.post.call_args.kwargs.get("headers") or {}
    assert headers.get("Authorization") == f"Bearer {expected_token}"


@pytest.mark.asyncio
async def test_request_uses_port_env(fake_msgs, monkeypatch):
    """gateway 端口可改 (PORT env)."""
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

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm(
            "sess_port", 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    url = mock_client.post.call_args.args[0]
    assert "127.0.0.1:9001" in url


@pytest.mark.asyncio
async def test_request_includes_internal_header(fake_msgs):
    """BL-F17: summarizer 必带 X-Catfish-Internal: true 让 gateway 跳 quota 计算."""
    from catfish_gateway import session_summarizer
    session_summarizer._SESSION_COOL_DOWN.clear()

    fake_resp = _mock_response(
        200, json_body={"choices": [{"message": {"content": "ok"}}]}
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        await _summarize_with_llm(
            "sess_internal_hdr", 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    headers = mock_client.post.call_args.kwargs.get("headers") or {}
    assert headers.get("X-Catfish-Internal") == "true", (
        "summarizer 后台 housekeeping 必须打 internal header, "
        "不该消耗员工 user_day quota (BL-F17)"
    )


# ── Cooldown ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cool_down_skips_immediate_retry(fake_msgs):
    """冷却中的 session 直接返 None, 不打 gateway."""
    from catfish_gateway import session_summarizer
    import time
    sid = "sess_cool"
    session_summarizer._SESSION_COOL_DOWN[sid] = time.time() + 100  # 冷却中

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()  # 不该被调
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(
            sid, 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    assert out is None
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_cool_down_expires_allows_retry(fake_msgs):
    """冷却过期后重新允许."""
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

    with _patch_catalog(), patch("httpx.AsyncClient", return_value=mock_client):
        out = await _summarize_with_llm(
            sid, 0.0, fake_msgs,
            origin_model="catfish-public-qwen-flash",
        )

    assert out == "成功"
    # 过期记录应被清
    assert sid not in session_summarizer._SESSION_COOL_DOWN


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
    """全无标点 — 截前 30 字."""
    summary = "员工" * 20
    title = _extract_short_title(summary)
    assert len(title) <= 30
    assert title.startswith("员工")


def test_extract_short_title_chinese_punctuation():
    summary = "员工要做周报！下面是要点。"
    assert _extract_short_title(summary) == "员工要做周报"


def test_update_session_title_db_missing(monkeypatch, tmp_path):
    """state.db 不存在 → 返 False 不挂."""
    from catfish_gateway import session_summarizer
    monkeypatch.setattr(session_summarizer, "STATE_DB", tmp_path / "nope.db")
    assert _update_session_title("sess_x", "test title") is False


def test_update_session_title_real_write(monkeypatch, tmp_path):
    """造个 fake state.db, 写 title, 验真写进去 + 只在 NULL/空时 update."""
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
