"""11/03 鸿波「记到员工名下」: 网关替员工写主动搭话开场白, 配额日志记到员工, 不再是 internal:gateway-loopback。"""
from __future__ import annotations

from catfish_gateway import proactive
from catfish_gateway.auth import User, resolve_effective_user_email


def _loopback() -> User:
    return User(sub="internal:gateway-loopback", role="admin", tier="internal", auth_method="internal_loopback")


def test_网关自调带员工就记员工():
    assert resolve_effective_user_email(_loopback(), "chenhongbo@ffcs.cn") == "chenhongbo@ffcs.cn"


def test_没带或不像email还是内部身份():
    assert resolve_effective_user_email(_loopback(), None) == "internal:gateway-loopback"
    assert resolve_effective_user_email(_loopback(), "client:hermes-cli") == "internal:gateway-loopback"


def test_普通员工令牌带别人的头照旧忽略():
    assert resolve_effective_user_email(User(sub="alice@ffcs.cn"), "bob@ffcs.cn") == "alice@ffcs.cn"


def test_开场白请求带上员工():
    assert proactive._on_behalf_of("chenhongbo@ffcs.cn") == {"X-Catfish-User": "chenhongbo@ffcs.cn"}
    assert proactive._on_behalf_of(None) == {}
