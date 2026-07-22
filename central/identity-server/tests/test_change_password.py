"""UserRegistry.change_password 单测 · BL-SELF-CHANGE-PASSWORD (Task #29 · 7/20).

覆盖:
  1. 正常改密 (旧对 · 新强) · 应通 · new hash 生效 · must_change_password=False
  2. 旧密码错 · 应返 (False, "旧密码错")
  3. 新密码 < 8 位 · 应返 (False, "新密码至少 8 位")
  4. 新密码 == 旧密码 · 应返 (False, "新密码不能与旧密码相同")
  5. 未知 user · 应返 (False, "用户 xx 不存在")
  6. 改密后 · verify_password(new) 通 · verify_password(old) 挂
  7. must_change_password=True 的 user · 改密后 flag 清

无 PG · pool=None · 走 memory-only 分支 (与 reset_password test 一样风格).

routes.py 层 (Bearer verify · service token reject · refresh_token revoke) 单测
另开 · 这里只覆盖 UserRegistry 逻辑.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_identity.users import UserRegistry, hash_password


@pytest.fixture(autouse=True)
def _force_memory_only(monkeypatch):
    """强制 pool=None · 所有 test 走 memory-only 分支.

    军规 7/20: 用户本机 5432 PG 已跑 · db.get_pool() 会返真 pool · 且
    _POOL module-level cache 跨 test 复用 · 撞出 'another operation is
    in progress'. Test 只测 UserRegistry 内存逻辑 · PG 侧走 test_pg_integration.
    """
    async def _fake_get_pool():
        return None
    monkeypatch.setattr("catfish_identity.db.get_pool", _fake_get_pool)


def _write_yaml(tmp_path: Path, content: str) -> Path:
    fp = tmp_path / "users.yaml"
    fp.write_text(content, encoding="utf-8")
    return fp


def _make_registry(tmp_path: Path, email: str = "alice@x.com",
                   password: str = "old_pass_12345",
                   must_change: bool = False) -> UserRegistry:
    """加 1 个 user · 返 registry.

    军规 7/20: yaml 加载 (users.py:118-127) IdentityUser 构造只传 7 字段 ·
    不读 must_change_password. 需 must_change=True 时手工设 · 不能靠 yaml.
    """
    pwd_hash = hash_password(password)
    _write_yaml(tmp_path, f"""
users:
  - email: {email}
    password_hash: {pwd_hash}
    name: Alice
    department: sales
    tier: employee
""")
    reg = UserRegistry(users_path=tmp_path / "users.yaml")
    if must_change:
        u = reg.find(email)
        assert u is not None
        u.must_change_password = True
    return reg


@pytest.mark.asyncio
async def test_change_password_ok(tmp_path) -> None:
    """正常改密 · 旧对 + 新强 · 应通"""
    reg = _make_registry(tmp_path, password="old_pass_12345")

    ok, msg = await reg.change_password(
        email="alice@x.com",
        old_password="old_pass_12345",
        new_password="NewStrong!2026",
    )
    assert ok, f"应通 · 实际返 msg={msg!r}"
    assert msg == ""

    # 新密码 verify 通
    assert reg.verify_password("alice@x.com", "NewStrong!2026") is not None
    # 老密码 verify 挂 (bcrypt hash 已换)
    assert reg.verify_password("alice@x.com", "old_pass_12345") is None


@pytest.mark.asyncio
async def test_change_password_wrong_old(tmp_path) -> None:
    """旧密码错 · 不改 · 返 (False, '旧密码错')"""
    reg = _make_registry(tmp_path, password="old_pass_12345")

    ok, msg = await reg.change_password(
        email="alice@x.com",
        old_password="WRONG_OLD",
        new_password="NewStrong!2026",
    )
    assert not ok
    assert msg == "旧密码错"

    # 老密码还生效 (未被误改)
    assert reg.verify_password("alice@x.com", "old_pass_12345") is not None


@pytest.mark.asyncio
async def test_change_password_new_too_short(tmp_path) -> None:
    """新密码 < 8 位 · 拒 · 返 (False, '新密码至少 8 位')"""
    reg = _make_registry(tmp_path, password="old_pass_12345")

    ok, msg = await reg.change_password(
        email="alice@x.com",
        old_password="old_pass_12345",
        new_password="short7!",  # 7 位
    )
    assert not ok
    assert msg == "新密码至少 8 位"

    # 老密码还生效
    assert reg.verify_password("alice@x.com", "old_pass_12345") is not None


@pytest.mark.asyncio
async def test_change_password_same_as_old(tmp_path) -> None:
    """新密码 == 旧密码 · 拒 · 防"改一下"式满足强制修改策略"""
    reg = _make_registry(tmp_path, password="old_pass_12345")

    ok, msg = await reg.change_password(
        email="alice@x.com",
        old_password="old_pass_12345",
        new_password="old_pass_12345",
    )
    assert not ok
    assert msg == "新密码不能与旧密码相同"


@pytest.mark.asyncio
async def test_change_password_unknown_user(tmp_path) -> None:
    """未知 user · 拒 · 不吐 timing info (走 verify_password 才 delegate 到 bcrypt)"""
    reg = _make_registry(tmp_path, email="alice@x.com", password="whatever")

    ok, msg = await reg.change_password(
        email="nobody@x.com",  # yaml 里没这 user
        old_password="anything",
        new_password="NewStrong!2026",
    )
    assert not ok
    assert "不存在" in msg


@pytest.mark.asyncio
async def test_change_password_clears_must_change_flag(tmp_path) -> None:
    """员工首次登录 (must_change_password=True) 改密后 · flag 清 · 不再强制"""
    reg = _make_registry(tmp_path, password="temp_pass_12345", must_change=True)
    user_before = reg.find("alice@x.com")
    assert user_before is not None
    assert user_before.must_change_password is True

    ok, msg = await reg.change_password(
        email="alice@x.com",
        old_password="temp_pass_12345",
        new_password="NewStrong!2026",
    )
    assert ok, f"应通 · 实际返 msg={msg!r}"

    user_after = reg.find("alice@x.com")
    assert user_after is not None
    assert user_after.must_change_password is False, (
        "改密后 must_change_password 应清 · 员工下次登录不再强制改"
    )


@pytest.mark.asyncio
async def test_change_password_case_insensitive_email(tmp_path) -> None:
    """email lookup case-insensitive · 与 verify_password 一致"""
    reg = _make_registry(tmp_path, email="alice@x.com", password="old_pass_12345")

    ok, msg = await reg.change_password(
        email="ALICE@X.COM",  # 大写
        old_password="old_pass_12345",
        new_password="NewStrong!2026",
    )
    assert ok, f"应通 · 实际返 msg={msg!r}"
    assert reg.verify_password("alice@x.com", "NewStrong!2026") is not None
