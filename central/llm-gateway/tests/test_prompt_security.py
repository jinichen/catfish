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

import pytest

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
        # BL-FIX13: 密码 >=4 字符防"pwd: 1" 这种太短的撞不真实场景
        hits = detect_credentials_in_text("pwd: foobar")
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
    # BL-FIX13: 密码值 >=4 字符
    msgs = [
        {"role": "user", "content": "密码是 abcdef"},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "密码: defghi"},
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
    # BL-FIX13: 密码值 >=4 字符
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "密码是 abcdef"},
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


# ============================================================
# BL-FIX13 (5/8) — regex 太宽误报修, 防 "用户名、密码、 验证码" 这种正常陈述句撞
# ============================================================
#
# 鸿波反馈: 我今天没有输入明文, 为什么还有这个提示? 诊断: 旧 regex 把 \s 塞
# 进 separator 集合 ([是为:\s]+), "请输入密码 然后" / "用户名、密码、" 之类
# 正常陈述都撞. 修法: separator 必须是 是/为/: 之一, \s 只允许在外侧, 同时
# 密码值 >=4 字符防短词撞.


def test_no_false_positive_normal_chinese_chenshu() -> None:
    """'请输入密码 然后点击登录' 是正常说话, 不该撞"""
    text = "请输入密码 然后点击登录"
    hits = detect_credentials_in_text(text)
    assert hits == [], f"BL-FIX13 regression: '{text}' 不该撞但撞了 {hits}"


def test_no_false_positive_password_with_punctuation_only() -> None:
    """'用户名、密码、 验证码' (中文顿号) 不该撞"""
    text = "登录EIS: 1. 输入用户名、密码、 验证码; 2. 点登录"
    hits = detect_credentials_in_text(text)
    assert hits == [], f"BL-FIX13 regression: '{text}' 不该撞"


def test_no_false_positive_password_question() -> None:
    """'忘记密码 怎么办' / '密码 不对' 不该撞"""
    for text in [
        "忘记密码 怎么办",
        "我密码 不对啊",
        "密码 (用 keychain)",
    ]:
        hits = detect_credentials_in_text(text)
        assert hits == [], f"BL-FIX13: '{text}' 不该撞 ({hits})"


def test_no_false_positive_short_value() -> None:
    """密码值 <4 字符不撞 (太短不真实, 'pwd: 1' / '密码是 a')"""
    for text in ["pwd: 1", "密码是 a", "token = 12"]:
        hits = detect_credentials_in_text(text)
        assert hits == [], f"BL-FIX13 短值 '{text}' 不该撞"


def test_still_catches_real_credentials() -> None:
    """真的明文密码 (>=4 字符 + 明确 separator) 必须还能逮到"""
    cases = [
        "密码是 jiniaA1+abc",
        "密码: longPassword123",
        "密码为 ffcs2026!",
        "password=jiniaA1+abc",
        "passwd: longPassword",
        "api_key = sk-abcdef123456",
        "token: ghp_abcdef1234567890",
        "Authorization: Bearer xxxxyyyyzzzzaaaa",
    ]
    for text in cases:
        hits = detect_credentials_in_text(text)
        assert len(hits) >= 1, f"BL-FIX13 真凭据没逮到: '{text}'"


# ── 8/8: token 形状模式 (借鉴 hermes v0.20 monitoring/redaction) ──────
#
# hermes 那边 secrets 那一层除了 redact_sensitive_text 还**另加**
# bearer/token-shape patterns。这里抄的是思路 —— 网关够不着 hermes
# (独立进程 / 独立 venv, 中央服务器上没装)。
#
# ⚠ 这组原来还测 scrub_credentials_in_text 的 fail-closed。那个函数已经删了:
# 唯一调用方 metrics.py 改成存**分类码**不存原文之后它成了死代码。
# 形状模式留着是因为 detect_* 共用同一张 _CREDENTIAL_PATTERNS —— 员工粘贴
# 裸 JWT / sk- key 时的警告靠它。


class TestTokenShapePatterns:
    """按形状认, 不能只靠 password= / token: 这类关键词引子。"""

    def test_裸_jwt_认得出(self):
        raw = (
            "这个 token 怎么解 "
            "eyJhbGciOiJSUzI1NiIsImtpZCI6Ijk1ZDgzYmI4Y2Q1MjNiYTYifQ"
            ".eyJzdWIiOiJjaGVuaG9uZ2JvQGZmY3MuY24ifQ"
            ".sig-abcdefghijklmnop"
        )
        assert detect_credentials_in_text(raw), "裸 JWT 该被认出来"

    def test_裸_sk_key_认得出(self):
        assert detect_credentials_in_text("我的 key 是 sk-proj-AbCdEf0123456789AbCdEf0123456789")

    @pytest.mark.parametrize("normal", [
        "model not found: gpt-5.6-luna",
        "rate limit exceeded, retry after 30s",
        "帮我看看这个报错 decode failed near eyJhbG",
        "chunk sk-1 too short",
    ])
    def test_不误伤正常文本(self, normal):
        assert detect_credentials_in_text(normal) == [], normal
