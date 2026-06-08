"""PG 加密 backend 单测 (6/9 BL-BROKER-DEPLOY).

加密层走 cryptography 库 AESGCM, **不依赖真 PG**. PG 集成真测留给 docker-compose
smoke test (deploy.sh 跑完后 curl /v1/secret roundtrip).

这里覆盖:
- `_load_master_key()` env 各种边界 (空, 占位, 错 base64, 错长度, OK)
- AES-256-GCM 加密 / 解密 roundtrip
- tamper detection (改 ciphertext / nonce / key → 解不出 InvalidTag)
"""
from __future__ import annotations

import base64
import secrets

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from catfish_secret_broker.storage import _load_master_key


# ============================================================
# _load_master_key — env 边界
# ============================================================


def test_master_key_missing(monkeypatch):
    monkeypatch.delenv("CATFISH_SECRET_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="未设"):
        _load_master_key()


def test_master_key_empty(monkeypatch):
    monkeypatch.setenv("CATFISH_SECRET_MASTER_KEY", "   ")
    with pytest.raises(RuntimeError, match="未设"):
        _load_master_key()


def test_master_key_placeholder_change_me(monkeypatch):
    monkeypatch.setenv("CATFISH_SECRET_MASTER_KEY", "CHANGE_ME")
    with pytest.raises(RuntimeError, match="占位值"):
        _load_master_key()


def test_master_key_placeholder_deploy_sh(monkeypatch):
    monkeypatch.setenv("CATFISH_SECRET_MASTER_KEY", "CHANGE_ME_RUN_DEPLOY_SH")
    with pytest.raises(RuntimeError, match="占位值"):
        _load_master_key()


def test_master_key_not_base64(monkeypatch):
    # !!@@## 不是 base64 字符
    monkeypatch.setenv("CATFISH_SECRET_MASTER_KEY", "not-base64!!!")
    with pytest.raises(RuntimeError, match="base64"):
        _load_master_key()


def test_master_key_wrong_length(monkeypatch):
    # 16 byte (AES-128) — 不接受, 必须 32 byte AES-256
    short_key = base64.b64encode(secrets.token_bytes(16)).decode()
    monkeypatch.setenv("CATFISH_SECRET_MASTER_KEY", short_key)
    with pytest.raises(RuntimeError, match="长度.*≠ 32"):
        _load_master_key()


def test_master_key_ok(monkeypatch):
    # 真 32 byte (256 bit) key
    real_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("CATFISH_SECRET_MASTER_KEY", real_key)
    key = _load_master_key()
    assert len(key) == 32
    assert key == base64.b64decode(real_key)


def test_master_key_from_openssl_rand(monkeypatch):
    """模拟 deploy.sh 真生成: openssl rand -base64 32 输出格式."""
    # openssl 输出可能含尾随换行, _load_master_key 应该 strip
    key_b64 = base64.b64encode(secrets.token_bytes(32)).decode() + "\n  "
    monkeypatch.setenv("CATFISH_SECRET_MASTER_KEY", key_b64)
    # strip 后应能解出 32 byte
    key = _load_master_key()
    assert len(key) == 32


# ============================================================
# AES-256-GCM 加密层 — roundtrip + tamper detect
# ============================================================


def _new_aead() -> tuple[AESGCM, bytes]:
    """生成新 32 byte key 跟 AESGCM 实例."""
    key = secrets.token_bytes(32)
    return AESGCM(key), key


def test_encrypt_decrypt_roundtrip():
    aead, _ = _new_aead()
    plaintext = "alice oauth refresh token: gho_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    nonce = secrets.token_bytes(12)
    ct = aead.encrypt(nonce, plaintext.encode(), associated_data=None)
    decrypted = aead.decrypt(nonce, ct, associated_data=None).decode()
    assert decrypted == plaintext


def test_ciphertext_not_plaintext():
    """密文不能跟明文重复 — sanity check."""
    aead, _ = _new_aead()
    plaintext = "sensitive token"
    nonce = secrets.token_bytes(12)
    ct = aead.encrypt(nonce, plaintext.encode(), associated_data=None)
    assert plaintext.encode() not in ct
    # ciphertext 长度 = plaintext + 16 byte tag
    assert len(ct) == len(plaintext) + 16


def test_each_encryption_uses_unique_nonce():
    """同 plaintext + 同 key + 不同 nonce → 不同 ciphertext (防 attacker 看流量察觉 secret 不变).
    AES-GCM 安全前提是 (key, nonce) 不重用. 这里验真用了独立 nonce.
    """
    aead, _ = _new_aead()
    plaintext = "same token"
    ct1 = aead.encrypt(secrets.token_bytes(12), plaintext.encode(), associated_data=None)
    ct2 = aead.encrypt(secrets.token_bytes(12), plaintext.encode(), associated_data=None)
    assert ct1 != ct2


def test_tamper_ciphertext_detected():
    """密文改 1 bit → InvalidTag, 解不出. GCM 认证保证."""
    aead, _ = _new_aead()
    nonce = secrets.token_bytes(12)
    ct = aead.encrypt(nonce, b"important", associated_data=None)
    # 翻第一个 byte 的最低位
    tampered = bytes([ct[0] ^ 0x01]) + ct[1:]
    with pytest.raises(InvalidTag):
        aead.decrypt(nonce, tampered, associated_data=None)


def test_wrong_nonce_fails():
    """正确 key 但错 nonce → 解不出."""
    aead, _ = _new_aead()
    nonce_correct = secrets.token_bytes(12)
    nonce_wrong = secrets.token_bytes(12)
    ct = aead.encrypt(nonce_correct, b"token", associated_data=None)
    with pytest.raises(InvalidTag):
        aead.decrypt(nonce_wrong, ct, associated_data=None)


def test_wrong_master_key_fails():
    """换 master key → 老密文解不出 (这就是为啥 README 警告 '不要换 key').

    模拟客户 IT 换了 CATFISH_SECRET_MASTER_KEY 之后, DB 里所有密文不可解.
    """
    aead_old, _ = _new_aead()
    aead_new, _ = _new_aead()  # 新 key
    nonce = secrets.token_bytes(12)
    ct = aead_old.encrypt(nonce, b"alice token", associated_data=None)
    # 新 key 解老密文 → InvalidTag
    with pytest.raises(InvalidTag):
        aead_new.decrypt(nonce, ct, associated_data=None)
