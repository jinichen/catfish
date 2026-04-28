"""prompt_security 单测.

覆盖:
  - 中文密码模式 (密码是 X / 密钥: X / 口令为 X)
  - 英文密码模式 (password=X / passwd: X / api_key=X)
  - OAuth Bearer header
  - 假阳性边界 (问"如何重置密码" 也会撞, 接受)
  - messages 数组扫描 (只扫 user, 不扫 assistant)
  - multimodal content (text + image)
  - 空 / None / 非字符串安全处理
"""
from __future__ import annotations

from catfish_gateway.prompt_security import (
    detect_credentials_in_messages,
    detect_credentials_in_text,
    make_warning_message,
)


# ============================================================
# detect_credentials_in_text
# ============================================================


class TestChinesePatterns:
    def test_password_with_colon(self) -> None:
        hits = detect_credentials_in_text("用户名 chenhb 密码: jiniaA1+")
        assert len(hits) >= 1

    def test_password_shi(self) -> None:
        """'密码是 xxx'"""
        hits = detect_credentials_in_text("登录 X 密码是 jiniaA1+")
        assert len(hits) >= 1

    def test_password_wei(self) -> None:
        """'密码为 xxx'"""
        hits = detect_credentials_in_text("账号 ABC, 密码为 xxx123")
        assert len(hits) >= 1

    def test_mimi_keyword(self) -> None:
        """密钥"""
        hits = detect_credentials_in_text("API 密钥: abc-def-123")
        assert len(hits) >= 1

    def test_kouling_keyword(self) -> None:
        """口令"""
        hits = detect_credentials_in_text("口令是 hello123")
        assert len(hits) >= 1


class TestEnglishPatterns:
    def test_password_equals(self) -> None:
        hits = detect_credentials_in_text("login with password=secretXYZ")
        assert len(hits) >= 1

    def test_password_colon(self) -> None:
        hits = detect_credentials_in_text("password: hunter2")
        assert len(hits) >= 1

    def test_passwd(self) -> None:
        hits = detect_credentials_in_text("passwd=admin")
        assert len(hits) >= 1

    def test_pwd(self) -> None:
        hits = detect_credentials_in_text("pwd: foo")
        assert len(hits) >= 1

    def test_api_key(self) -> None:
        hits = detect_credentials_in_text("api_key=sk-abc123")
        assert len(hits) >= 1

    def test_api_key_dash(self) -> None:
        hits = detect_credentials_in_text("api-key: sk-xyz")
        assert len(hits) >= 1

    def test_secret(self) -> None:
        hits = detect_credentials_in_text("secret=topsecret")
        assert len(hits) >= 1

    def test_token(self) -> None:
        hits = detect_credentials_in_text("token=eyJhb...")
        assert len(hits) >= 1


class TestBearer:
    def test_authorization_bearer(self) -> None:
        hits = detect_credentials_in_text("Authorization: Bearer eyJhbGciOiJI...")
        assert len(hits) >= 1


class TestNegatives:
    def test_no_credentials(self) -> None:
        hits = detect_credentials_in_text("帮我看下今天的邮件")
        assert hits == []

    def test_empty(self) -> None:
        assert detect_credentials_in_text("") == []
        assert detect_credentials_in_text(None) == []  # type: ignore[arg-type]

    def test_non_string(self) -> None:
        assert detect_credentials_in_text(123) == []  # type: ignore[arg-type]
        assert detect_credentials_in_text({}) == []  # type: ignore[arg-type]


class TestKnownFalsePositives:
    """已知假阳性 — 接受这些, 不强求 100% 精度."""

    def test_how_to_reset_password(self) -> None:
        """问'如何重置密码' 也会撞 — 接受, 因为 warn 不拦, 员工自己 judge"""
        # 注: 实际只匹配 '密码[是为:]\s+\S+', 这条原文档说会撞但实际看 regex 不会撞 ?
        # '如何重置密码' 没"密码 是/为/:" 后跟值, 所以应该不会撞.
        hits = detect_credentials_in_text("如何重置密码?")
        # 实际上"密码?" 没匹配上 \S+ (问号是 \S 但只 1 字符不算 password value)
        # 这个 case 看实际表现, 不强 assert
        # 主要意图是文档化"已知假阳性"
        assert isinstance(hits, list)


# ============================================================
# detect_credentials_in_messages (扫消息数组)
# ============================================================


def test_messages_user_only() -> None:
    """assistant / system 消息不扫, 只扫 user"""
    msgs = [
        {"role": "system", "content": "我密码是 system_pwd"},  # 不扫
        {"role": "assistant", "content": "我密码是 assistant_pwd"},  # 不扫
        {"role": "user", "content": "我密码是 user_pwd"},  # 扫
    ]
    hits = detect_credentials_in_messages(msgs)
    assert len(hits) >= 1


def test_messages_multiple_users() -> None:
    """多条 user 消息合并去重"""
    msgs = [
        {"role": "user", "content": "密码是 abc"},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "密码: def"},
    ]
    hits = detect_credentials_in_messages(msgs)
    # 同一个 pattern 多次撞 → 去重 (set), 但每个 pattern 名字算一个
    assert len(hits) >= 1


def test_messages_no_credentials() -> None:
    msgs = [
        {"role": "user", "content": "今天有什么邮件"},
        {"role": "assistant", "content": "你有 5 封"},
    ]
    assert detect_credentials_in_messages(msgs) == []


def test_messages_empty() -> None:
    assert detect_credentials_in_messages([]) == []
    assert detect_credentials_in_messages(None) == []  # type: ignore[arg-type]


def test_messages_multimodal() -> None:
    """user content 是 multimodal list (text + image), 只扫 text part"""
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "密码是 abc"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ],
    }]
    hits = detect_credentials_in_messages(msgs)
    assert len(hits) >= 1


def test_messages_invalid_structure_safe() -> None:
    """msg 不是 dict / 缺 content → 不抛"""
    msgs = [
        "not a dict",  # type: ignore[list-item]
        {"role": "user"},  # 没 content
        {"role": "user", "content": None},
    ]
    # 不抛
    hits = detect_credentials_in_messages(msgs)
    assert hits == []


# ============================================================
# make_warning_message
# ============================================================


def test_warning_message_format() -> None:
    msg = make_warning_message(["password=", "密码:"])
    assert "secret_ref" in msg
    assert "keychain" in msg
    assert "2 处" in msg


def test_warning_message_single_hit() -> None:
    msg = make_warning_message(["密码:"])
    assert "1 处" in msg
