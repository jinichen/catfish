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
   - hermes-cli service token + 没 header: **fallback 返 sub** (P3.5.17 6/17 改, 见下)
   - hermes-cli service token + 非 email 格式: 401
   - 非白名单 service token + X-Catfish-User: 忽略 header, 返 sub

# 契约变更 · P3.5.17 (6/17 鸿波)

原设计"白名单 service token 缺 X-Catfish-User → 400". 但 hermes 自带
auxiliary_client (context 压缩 summary 用) 调 gateway **不传**这个 header —
那是 hermes 内部 system 行为, 不属于某个员工请求. 400 让 context_compressor
报 "Error code: 400" + 60s pause, 鸿波 304K 长任务永远不压缩.

改成 fallback 到 sub (跟"非白名单 service token"行为一致): quota / RBAC / audit
归 client:hermes-cli 头上, 仍可追溯不绕审计. chat 主路径 hermes 照传 header, 行为不变.

实现见 `auth/__init__.py:145-165`.

⚠ 7/27 BL-PLUGIN-AUTH-FIX: 本文件 3 个测试当时没跟着改 (仍断言 raise 400),
一直红着. 借这次 gateway scope 改动一并修 — 红测试留着会让真回归被忽略.
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

    def test_hermes_cli_no_header_falls_back_to_sub(self):
        """P3.5.17 (6/17): 缺 X-Catfish-User **不再 400** · fallback 到 sub.

        真因: hermes auxiliary_client (context 压缩) 不传这 header, 400 会让
        context_compressor 报错 + 60s pause, 长任务永远不压缩. 改 fallback 后
        quota/audit 归 client:hermes-cli 头上, 仍可追溯.
        (老测试断言 raise 400 · 7/27 BL-PLUGIN-AUTH-FIX 一并修.)
        """
        u = User(sub="client:hermes-cli", role="service")
        eff = resolve_effective_user_email(u, None)
        assert eff == "client:hermes-cli"

    def test_hermes_cli_empty_header_falls_back_to_sub(self):
        """空字符串 / 纯空白 跟缺 header 同款处理 (auth/__init__.py 先 .strip())."""
        u = User(sub="client:hermes-cli", role="service")
        assert resolve_effective_user_email(u, "") == "client:hermes-cli"
        assert resolve_effective_user_email(u, "   ") == "client:hermes-cli"

    def test_hermes_cli_non_email_header_raises_401(self):
        """X-Catfish-User 形状不对 → 401 (拒绝, 防 garbage / injection).

        注 · 这是**唯一还 raise** 的分支 (P3.5.17 后缺 header 改 fallback 了).
        缺 header = caller 没打算指定员工 (auxiliary 路径, 合法);
        header 有值但不像 email = caller 传错了 / 注入尝试, 必须拒.
        """
        u = User(sub="client:hermes-cli", role="service")
        for bad in ("not-an-email", "no-at-sign.com", "@no-local.com", "x@y", "x@y."):
            with pytest.raises(HTTPException) as exc:
                resolve_effective_user_email(u, bad)
            assert exc.value.status_code == 401, f"expected 401 for {bad!r}"
            # 7/27: error detail 含 header 名, hermes 维护者一看就知道是哪条 contract
            # (老代码这条断言在 no_header 测试里, 那测试改 fallback 后断言挪到这)
            assert X_CATFISH_USER_HEADER in str(exc.value.detail)

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

    def test_hermes_auxiliary_flow_without_header(self):
        """P3.5.17 (6/17): hermes auxiliary 路径 (context 压缩) 不传 header.

        原 A2 设计是 400 逼 hermes 传 header. 实测 hermes auxiliary_client 就是
        不传 (系统级调用, 不属某员工), 400 直接卡死长任务压缩. 改 fallback 到 sub.

        代价 · 失去"强制 hermes-cli 传 header"硬约束. 可接受: chat 主路径
        Companion 走 sub_email 必传, 不传的一定是后台 auxiliary, 归 service
        自己头上语义更准.
        (老测试名 test_hermes_cli_flow_requires_header + 断言 400 · 7/27 一并修.)
        """
        user = User(sub="client:hermes-cli", role="service")
        eff = resolve_effective_user_email(user, None)
        # quota / audit 归 service 自己 · 仍是可追溯字符串 (不绕审计)
        assert eff == "client:hermes-cli"
