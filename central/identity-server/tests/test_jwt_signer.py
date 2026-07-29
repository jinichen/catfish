"""jwt_signer 单测.

覆盖: 密钥生成 / 持久化 / 加载 / 签 / JWKS 导出 / 跨实例验签.
"""
from __future__ import annotations

import jwt
import pytest

from catfish_identity.jwt_signer import JwtSigner


def test_generates_keypair_on_first_run(tmp_path) -> None:
    signer = JwtSigner(key_dir=tmp_path)
    assert (tmp_path / "private.pem").exists()
    assert (tmp_path / "public.pem").exists()
    assert len(signer.kid) == 16  # sha256 前 16 hex


def test_reuses_existing_key(tmp_path) -> None:
    s1 = JwtSigner(key_dir=tmp_path)
    s2 = JwtSigner(key_dir=tmp_path)
    assert s1.kid == s2.kid  # 同一密钥 = 同一 kid


def test_sign_and_verify_roundtrip(tmp_path) -> None:
    signer = JwtSigner(key_dir=tmp_path)
    token = signer.sign_id_token(
        issuer="http://localhost:8998",
        subject="alice@x.com",
        audience="catfish-companion",
        claims={"email": "alice@x.com", "name": "Alice"},
        ttl_seconds=600,
    )
    # 用 jwks 验签
    jwks = signer.jwks()
    assert len(jwks["keys"]) == 1
    key_dict = jwks["keys"][0]
    assert key_dict["kty"] == "RSA"
    assert key_dict["alg"] == "RS256"
    assert key_dict["kid"] == signer.kid

    # PyJWK 从 jwks 构造验签 key
    public_jwk = jwt.PyJWK(key_dict)
    payload = jwt.decode(
        token, public_jwk.key, algorithms=["RS256"], audience="catfish-companion"
    )
    assert payload["sub"] == "alice@x.com"
    assert payload["iss"] == "http://localhost:8998"
    assert payload["email"] == "alice@x.com"


def test_kid_in_jwt_header(tmp_path) -> None:
    """gateway 验签时要从 header 拿 kid 找对应公钥, 必须签的时候带上."""
    signer = JwtSigner(key_dir=tmp_path)
    token = signer.sign_id_token(
        issuer="http://x", subject="x", audience="x", claims={}, ttl_seconds=60
    )
    headers = jwt.get_unverified_header(token)
    assert headers["kid"] == signer.kid
    assert headers["alg"] == "RS256"


def test_corrupted_private_key_raises(tmp_path) -> None:
    """私钥文件被乱写 → 加载时报错, 不静默 fallback"""
    bad = tmp_path / "private.pem"
    bad.write_text("not a real key")
    with pytest.raises((ValueError, Exception)):
        JwtSigner(key_dir=tmp_path)


# ── P3.5.80 (7/28 鸿波达华现场) · 并发生成竞态回归 ────────────────────
#
# 老实现是 `if exists(): load else: generate + write`, 无锁非原子.
# UVICORN_WORKERS 默认 2, 两 worker 同时启动各生成一把不同 RSA →
# kid 不同 → JWKS 返哪把取决于哪个 worker 应答 → JWT 验签约 50% 失败,
# 表现为"登录时好时坏". 现场日志实证: "私钥不存在, 生成新 RSA" 打了两行.
#
# 实测: 老逻辑 8 进程并发 → 8 个不同 kid; 修复后 → 1 个.

def _kid_in_subprocess(key_dir: str, q) -> None:
    """子进程里初始化 signer, 把 kid 丢回队列 (模块级函数才能被 pickle)."""
    from pathlib import Path

    from catfish_identity.jwt_signer import JwtSigner

    try:
        q.put(JwtSigner(key_dir=Path(key_dir))._kid)
    except Exception as e:  # noqa: BLE001
        q.put(f"ERR:{e}")


@pytest.mark.parametrize("n_workers", [2, 8])
def test_concurrent_init_yields_single_kid(tmp_path, n_workers) -> None:
    """N 个进程同时初始化 → 必须只有一把密钥 (一个 kid).

    n=2 对应 UVICORN_WORKERS 默认值; n=8 放大竞态窗口.
    """
    import multiprocessing as mp

    ctx = mp.get_context("spawn")   # fork 会继承已加载的 key, 测不出竞态
    q = ctx.Queue()
    procs = [
        ctx.Process(target=_kid_in_subprocess, args=(str(tmp_path), q))
        for _ in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)

    kids = [q.get() for _ in procs]
    errors = [k for k in kids if str(k).startswith("ERR:")]
    assert not errors, f"子进程报错: {errors}"
    assert len(set(kids)) == 1, (
        f"{n_workers} 个 worker 生成了 {len(set(kids))} 把不同密钥: {set(kids)}. "
        "两 worker kid 不一致会导致 JWT 验签随机失败."
    )


def test_no_temp_files_left_behind(tmp_path) -> None:
    """生成密钥后不留 .tmp 残file (抢输的一方也要清干净)."""
    JwtSigner(key_dir=tmp_path)
    leftovers = list(tmp_path.glob(".private.pem.tmp.*"))
    assert not leftovers, f"残留临时文件: {leftovers}"


def test_private_key_permission_0600(tmp_path) -> None:
    """私钥落盘必须 0600 —— link 之前就设好, 不留可读窗口."""
    import stat

    JwtSigner(key_dir=tmp_path)
    mode = (tmp_path / "private.pem").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600, f"私钥权限是 {oct(stat.S_IMODE(mode))}, 应为 0600"
