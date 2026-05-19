"""BL-AUTH-DECOUPLE-A1 (5/19) — service token + X-Catfish-User header.

# 背景

hermes daemon 现在调 gateway 用员工 user JWT (1h, sub=chenhongbo@ffcs.cn), 每小时
被 hermes OAuth refresh 覆盖, 跨小时 401 race. 治本: hermes 用 service token (sub=
client:hermes-cli, 30 天). gateway 看 X-Catfish-User header 知道"代表哪个员工调",
quota / RBAC / audit 都按这个员工归账, 不按 client:hermes-cli (会让所有员工共享一个
quota bucket).

# 安全约束

只有白名单的 client_id (写死在 const 里) 允许通过 X-Catfish-User 覆盖. 普通员工 user
token 即使带 X-Catfish-User 也**完全忽略** — 防越权冒充. 别的 service token
(e.g. cron client) X-Catfish-User 也忽略 — quota 归自己头上.

# 覆盖

1. is_service_principal / service_client_id 边界
2. resolve_effective_user_email:
   - user JWT (sub=email): 忽略 X-Catfish-User, 返 sub
   - hermes-cli service token + 合法 email: 返 header
   - hermes-cli service token + 没 header: 400
   - hermes-cli service token + 非 email 格式: 401
   - 非白名单 service token + X-Catfish-User: 忽略 header, 返 sub
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from catfish_gateway.auth import (
    SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE,
    User,
    X_CATFISH_USER_HEADER,
    is_service_principal,
    resolve_effective_user_email,
    service_client_id,
)


# ============================================================
# is_service_principal / service_client_id
# ============================================================


class TestIsServicePrincipal:
    def test_user_token_email_sub_is_not_service(self):
        u = User(sub="chenhongbo@ffcs.cn")
        assert is_service_principal(u) is False
        assert service_client_id(u) is None

    def test_service_token_client_prefix_is_service(self):
        u = User(sub="client:hermes-cli", role="service")
        assert is_service_principal(u) is True
        assert service_client_id(u) == "hermes-cli"

    def test_none_user_is_not_service(self):
        assert is_service_principal(None) is False
        assert service_client_id(None) is None

    def test_empty_sub_is_not_service(self):
        u = User(sub="")
        assert is_service_principal(u) is False

    def test_other_prefix_is_not_service(self):
        """sub 不是以 'client:' 起头不算 service (e.g. 假装绕过)"""
        u = User(sub="clientfoo:bar")
        assert is_service_principal(u) is False

    def test_only_prefix_no_id_returns_none_client_id(self):
        """sub = 'client:' 空 client_id 不算合法 service principal"""
        u = User(sub="client:")
        # is_service_principal 看 prefix, 但 service_client_id 解出空 cid 返 None
        assert is_service_principal(u) is True
        assert service_client_id(u) is None


# ============================================================
# resolve_effective_user_email
# ============================================================


class TestResolveEffectiveUserEmailUserToken:
    """普通 user JWT 路径 — X-Catfish-User 必须被完全忽略."""

    def test_user_token_no_header_returns_sub(self):
        u = User(sub="chenhongbo@ffcs.cn")
        assert resolve_effective_user_email(u, None) == "chenhongbo@ffcs.cn"

    def test_user_token_with_header_ignores_header(self):
        """普通员工不能通过 X-Catfish-User 冒充别人 — 必须忽略."""
        u = User(sub="alice@ffcs.cn")
        eff = resolve_effective_user_email(u, "victim@ffcs.cn")
        assert eff == "alice@ffcs.cn"
        # 显式说明: 不是 victim
        assert eff != "victim@ffcs.cn"

    def test_user_token_with_garbage_header_still_ignores(self):
        u = User(sub="alice@ffcs.cn")
        # garbage header 也忽略 (不应该 raise — 我们不验它, 它根本没被读)
        assert resolve_effective_user_email(u, "not-an-email") == "alice@ffcs.cn"
        assert resolve_effective_user_email(u, "  ") == "alice@ffcs.cn"


class TestResolveEffectiveUserEmailServiceTokenWhitelisted:
    """hermes-cli (白名单 service) — X-Catfish-User 决定 effective user."""

    def test_hermes_cli_with_valid_email_header(self):
        u = User(sub="client:hermes-cli", role="service")
        eff = resolve_effective_user_email(u, "chenhongbo@ffcs.cn")
        assert eff == "chenhongbo@ffcs.cn"

    def test_hermes_cli_no_header_raises_400(self):
        """白名单 service token 必须配 X-Catfish-User, 缺 → 400 (caller bug)."""
        u = User(sub="client:hermes-cli", role="service")
        with pytest.raises(HTTPException) as exc:
            resolve_effective_user_email(u, None)
        assert exc.value.status_code == 400
        assert X_CATFISH_USER_HEADER in str(exc.value.detail)

    def test_hermes_cli_empty_header_raises_400(self):
        u = User(sub="client:hermes-cli", role="service")
        with pytest.raises(HTTPException) as exc:
            resolve_effective_user_email(u, "")
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc2:
            resolve_effective_user_email(u, "   ")
        assert exc2.value.status_code == 400

    def test_hermes_cli_non_email_header_raises_401(self):
        """X-Catfish-User 形状不对 → 401 (拒绝, 防 garbage / injection)."""
        u = User(sub="client:hermes-cli", role="service")
        for bad in ("not-an-email", "no-at-sign.com", "@no-local.com", "x@y", "x@y."):
            with pytest.raises(HTTPException) as exc:
                resolve_effective_user_email(u, bad)
            assert exc.value.status_code == 401, f"expected 401 for {bad!r}"

    def test_hermes_cli_header_trimmed(self):
        """前后空白容忍."""
        u = User(sub="client:hermes-cli", role="service")
        eff = resolve_effective_user_email(u, "  chenhongbo@ffcs.cn  ")
        assert eff == "chenhongbo@ffcs.cn"


class TestResolveEffectiveUserEmailServiceTokenNotWhitelisted:
    """非白名单 service token — X-Catfish-User 应被忽略 (防误开 impersonation)."""

    def test_unknown_service_ignores_header(self):
        # 假设有个 cron-client, 没在 SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE 里
        u = User(sub="client:cron-helper", role="service")
        assert "cron-helper" not in SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE
        # 即使带 X-Catfish-User 也忽略, effective = sub
        eff = resolve_effective_user_email(u, "victim@ffcs.cn")
        assert eff == "client:cron-helper"

    def test_unknown_service_no_header_uses_sub(self):
        u = User(sub="client:cron-helper", role="service")
        eff = resolve_effective_user_email(u, None)
        assert eff == "client:cron-helper"

    def test_whitelist_contains_only_hermes_cli(self):
        """A1 阶段只允许 hermes-cli. 加新 client 要改 code + 走 review."""
        assert SERVICE_CLIENTS_ALLOWING_USER_OVERRIDE == frozenset({"hermes-cli"})


# ============================================================
# 端到端 (跟 chat_completions 集成): 间接通过组合调用模拟
# ============================================================


class TestIntegrationFlow:
    """模拟 chat_completions 入口段对 effective_user_email 的使用."""

    def test_normal_user_flow_uses_sub_for_quota(self):
        """普通员工 + 带不带 X-Catfish-User 都用 user.sub 归账."""
        user = User(sub="employee@ffcs.cn", department="sales", role="employee")
        # 模拟 chat_completions 入口段
        for header_value in (None, "victim@ffcs.cn", "garbage"):
            eff = resolve_effective_user_email(user, header_value)
            assert eff == "employee@ffcs.cn"
            # quota.check_quota(user_email=eff, ...) 用 eff
            # audit.log_request_metadata(user=eff, ...) 也用 eff

    def test_hermes_cli_flow_uses_header_for_quota(self):
        """hermes service token + X-Catfish-User → quota / audit 归员工."""
        user = User(
            sub="client:hermes-cli",
            role="service",
            department="infra",  # service token 自己的 dept
            auth_method="oidc:test",
        )
        eff = resolve_effective_user_email(user, "chenhongbo@ffcs.cn")
        # quota 归员工 (不是 hermes-cli), 解决跨小时 401 + quota bucket 共享问题
        assert eff == "chenhongbo@ffcs.cn"

    def test_hermes_cli_flow_requires_header(self):
        """A2 hermes 升级时如果忘了加 X-Catfish-User → 400 报错明显, 容易调试."""
        user = User(sub="client:hermes-cli", role="service")
        with pytest.raises(HTTPException) as exc:
            resolve_effective_user_email(user, None)
        assert exc.value.status_code == 400
        # 错误信息含 header 名, hermes 维护者一看就知道是这条 contract
        assert X_CATFISH_USER_HEADER in str(exc.value.detail)
