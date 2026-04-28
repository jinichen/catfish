"""auth/ 包 + AuthProvider ABC 边界单测.

Phase 1A 验收 — 现有行为 100% 兼容 + ABC 设计正确性.

覆盖:
  - DevTokenProvider 各种 input 边界
  - User dataclass 字段 + auth_method
  - AuthProvider ABC 强制子类实现
  - make_auth_provider 工厂
  - _get_provider 单例
  - _set_provider_for_testing 测试 hook
"""
from __future__ import annotations

import pytest

from catfish_gateway.auth import (
    AuthProvider,
    DevTokenProvider,
    User,
    _get_provider,
    _set_provider_for_testing,
    make_auth_provider,
)


# ============================================================
# DevTokenProvider 行为
# ============================================================


class TestDevTokenProviderBasic:
    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "secret123")
        p = DevTokenProvider()
        user = p.verify_bearer("Bearer secret123")
        assert user is not None
        assert user.sub == "dev-user"
        assert user.department == "engineering"
        assert user.tier == "employee"
        assert user.auth_method == "dev_token"

    def test_default_token_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """没设 CATFISH_DEV_TOKEN, 用默认 'dev-token-local'"""
        monkeypatch.delenv("CATFISH_DEV_TOKEN", raising=False)
        p = DevTokenProvider()
        user = p.verify_bearer("Bearer dev-token-local")
        assert user is not None

    def test_env_change_reflected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """每次 verify 重读 env, 支持测试动态改 token"""
        p = DevTokenProvider()
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "first")
        assert p.verify_bearer("Bearer first") is not None
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "second")
        assert p.verify_bearer("Bearer first") is None  # 旧 token 失效
        assert p.verify_bearer("Bearer second") is not None


class TestDevTokenProviderRejection:
    def setup_method(self) -> None:
        self.p = DevTokenProvider()

    def test_no_authorization_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "x")
        assert self.p.verify_bearer(None) is None
        assert self.p.verify_bearer("") is None

    def test_no_bearer_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "x")
        assert self.p.verify_bearer("x") is None  # 没 'Bearer '
        assert self.p.verify_bearer("Basic x") is None
        assert self.p.verify_bearer("Token x") is None

    def test_empty_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "x")
        assert self.p.verify_bearer("Bearer ") is None
        assert self.p.verify_bearer("Bearer    ") is None  # 全空白

    def test_wrong_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "real")
        assert self.p.verify_bearer("Bearer fake") is None

    def test_case_insensitive_bearer_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'bearer' / 'BEARER' 都该接受"""
        monkeypatch.setenv("CATFISH_DEV_TOKEN", "x")
        assert self.p.verify_bearer("bearer x") is not None
        assert self.p.verify_bearer("BEARER x") is not None
        assert self.p.verify_bearer("Bearer x") is not None


class TestDevTokenProviderMetadata:
    def test_name(self) -> None:
        assert DevTokenProvider().name == "dev_token"

    def test_is_strict_false(self) -> None:
        """dev_token 是 lax, 客户 IT 配错 SSO 时救急用. UI 应显 banner."""
        assert DevTokenProvider().is_strict is False


# ============================================================
# AuthProvider ABC 强制契约
# ============================================================


class TestAuthProviderABC:
    def test_cannot_instantiate_abstract_directly(self) -> None:
        """ABC 直接实例化应该抛 TypeError (Python 标准 ABC 行为)"""
        with pytest.raises(TypeError):
            AuthProvider()  # type: ignore[abstract]

    def test_subclass_missing_methods_cannot_instantiate(self) -> None:
        """子类没实现抽象方法应该抛 TypeError"""

        class IncompleteProvider(AuthProvider):
            pass  # 没实现 verify_bearer / name

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore[abstract]

    def test_subclass_with_methods_works(self) -> None:
        """子类实现完整就能实例化"""

        class CompleteProvider(AuthProvider):
            def verify_bearer(self, authorization):
                return None

            @property
            def name(self):
                return "test"

        p = CompleteProvider()
        assert p.name == "test"
        assert p.verify_bearer(None) is None

    def test_is_strict_default_true(self) -> None:
        """is_strict 默认 True (符合"严格 SSO" 默认假设)"""

        class StrictByDefault(AuthProvider):
            def verify_bearer(self, authorization):
                return None

            @property
            def name(self):
                return "strict"

        assert StrictByDefault().is_strict is True


# ============================================================
# 工厂 + 单例 + 测试 hook
# ============================================================


class TestMakeAuthProvider:
    def test_dev_env_returns_dev_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CATFISH_ENV", "dev")
        p = make_auth_provider()
        assert isinstance(p, DevTokenProvider)

    def test_prod_env_phase1a_still_dev_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 1A: prod env 也用 DevTokenProvider 兜底 (Phase 1B 上 OIDC 后改 Composite)"""
        monkeypatch.setenv("CATFISH_ENV", "prod")
        p = make_auth_provider()
        assert isinstance(p, DevTokenProvider)

    def test_unknown_env_fallback_dev_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CATFISH_ENV", "weird")
        p = make_auth_provider()
        assert isinstance(p, DevTokenProvider)


class TestSingletonAndTestingHook:
    def teardown_method(self) -> None:
        # 每个测试后 reset, 防影响别的测试
        _set_provider_for_testing(None)

    def test_singleton_lazy_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_provider_for_testing(None)  # reset
        monkeypatch.setenv("CATFISH_ENV", "dev")
        p1 = _get_provider()
        p2 = _get_provider()
        assert p1 is p2  # 同一实例

    def test_set_provider_for_testing_injects_mock(self) -> None:
        class MockProvider(AuthProvider):
            def verify_bearer(self, authorization):
                return User(sub="injected@example.com", auth_method="mock")

            @property
            def name(self):
                return "mock"

        mock = MockProvider()
        _set_provider_for_testing(mock)
        assert _get_provider() is mock

        user = _get_provider().verify_bearer("anything")
        assert user is not None
        assert user.sub == "injected@example.com"
        assert user.auth_method == "mock"

    def test_set_provider_none_resets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """传 None 让 singleton 下次 lazy init"""
        monkeypatch.setenv("CATFISH_ENV", "dev")
        _set_provider_for_testing(None)
        p_a = _get_provider()
        _set_provider_for_testing(None)
        p_b = _get_provider()
        # _set_provider_for_testing(None) 后 _get_provider 应该重新 init
        # (不是同一实例, 因为我们 reset 过)
        assert p_a is not p_b


# ============================================================
# User dataclass
# ============================================================


class TestUser:
    def test_default_fields(self) -> None:
        u = User(sub="alice@x.com")
        assert u.sub == "alice@x.com"
        assert u.department == ""
        assert u.tier == "employee"
        assert u.auth_method == "unknown"

    def test_explicit_fields(self) -> None:
        u = User(
            sub="bob@x.com",
            department="sales",
            tier="admin",
            auth_method="dev_token",
        )
        assert u.department == "sales"
        assert u.tier == "admin"
        assert u.auth_method == "dev_token"

    def test_can_access_all_models_phase_p0(self) -> None:
        """P0: 任何 user 能调任何 model. Phase 2 RBAC 才加限制."""
        u = User(sub="x")
        assert u.can_access(model=None) is True
        assert u.can_access(model="any") is True
