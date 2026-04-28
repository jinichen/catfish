"""JWT 签名 + JWKS 导出.

# 设计

启动时检查 ~/.catfish/identity-server/keys/private.pem 不存在就生成 RSA 2048 密钥对.
生产部署时客户应该自己生成密钥, 通过 env CATFISH_IDENTITY_PRIVATE_KEY_PATH 指定.

为啥 RS256 不 HS256:
  - RS256 公钥可以发布 (jwks_uri), gateway 拉公钥验签
  - HS256 共享 secret, 双方都要存, 部署复杂
  - OIDC 标准要求 RS256 至少支持

# kid (Key ID)

每个 RSA 密钥有 kid (key identifier), 让 gateway 验签时知道用哪个公钥.
现在只支持单 key, 但 JWKS 格式 list of keys 留扩展空间.
kid = sha256(public_key_pem)[:16]
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

logger = logging.getLogger("catfish.identity.jwt_signer")


def _default_key_dir() -> Path:
    """密钥默认存这里. 客户可以 env CATFISH_IDENTITY_KEY_DIR 覆盖."""
    if env := os.environ.get("CATFISH_IDENTITY_KEY_DIR"):
        return Path(env).expanduser()
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(home) / ".catfish" / "identity-server" / "keys"


def _b64url(data: bytes) -> str:
    """base64url encode (无 padding), JWKS 标准."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class JwtSigner:
    """RSA 私钥签 + JWKS 公钥导出.

    Stateless 业务上 — 私钥加载一次, 签 / 导出 JWKS 都 thread-safe.
    """

    def __init__(self, key_dir: Path | None = None) -> None:
        self.key_dir = key_dir or _default_key_dir()
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self._private_path = self.key_dir / "private.pem"
        self._public_path = self.key_dir / "public.pem"

        self._private_key = self._load_or_generate()
        self._public_key = self._private_key.public_key()
        self._kid = self._compute_kid(self._public_key)
        logger.info(
            "jwt_signer 初始化: kid=%s, key_dir=%s", self._kid, self.key_dir
        )

    def _load_or_generate(self) -> rsa.RSAPrivateKey:
        """加载本地密钥, 没有就生成 + 持久化."""
        if self._private_path.exists():
            with open(self._private_path, "rb") as f:
                pem = f.read()
            key = serialization.load_pem_private_key(pem, password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise RuntimeError(
                    f"{self._private_path} 不是 RSA 密钥. 删了让自动重新生成."
                )
            return key

        logger.warning("私钥不存在, 生成新 RSA 2048 密钥对到 %s", self.key_dir)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # 持久化
        priv_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        with open(self._private_path, "wb") as f:
            f.write(priv_pem)
        with open(self._public_path, "wb") as f:
            f.write(pub_pem)
        # 私钥权限 0600 (只有 owner 可读)
        try:
            os.chmod(self._private_path, 0o600)
        except OSError:
            # Windows 不支持 chmod, 忽略
            pass
        return key

    @staticmethod
    def _compute_kid(public_key: rsa.RSAPublicKey) -> str:
        """kid = sha256(public_pem)[:16] hex."""
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(pem).hexdigest()[:16]

    @property
    def kid(self) -> str:
        return self._kid

    def sign_id_token(
        self,
        *,
        issuer: str,
        subject: str,
        audience: str,
        claims: dict,
        ttl_seconds: int = 3600,
    ) -> str:
        """签 ID Token.

        Args:
            issuer: OIDC issuer URL (本 server 的 base URL, 例 http://127.0.0.1:8998)
            subject: User.sub (Phase 1 决议: email)
            audience: client_id (谁要这个 token)
            claims: 额外 claims (email / name / department / etc.)
            ttl_seconds: 默认 1h

        Returns:
            JWT string (RS256 签).
        """
        now = int(time.time())
        payload = {
            "iss": issuer,
            "sub": subject,
            "aud": audience,
            "iat": now,
            "exp": now + ttl_seconds,
            **claims,
        }
        priv_pem = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return jwt.encode(
            payload,
            priv_pem,
            algorithm="RS256",
            headers={"kid": self._kid},
        )

    def jwks(self) -> dict:
        """JWKS 公钥 (供 gateway 验签).

        格式: https://datatracker.ietf.org/doc/html/rfc7517

        现在只支持单 key. 多 key (例: 在用 + 退役但还在验) Phase 2 加.
        """
        pub_numbers = self._public_key.public_numbers()
        n_bytes = pub_numbers.n.to_bytes(
            (pub_numbers.n.bit_length() + 7) // 8, "big"
        )
        e_bytes = pub_numbers.e.to_bytes(
            (pub_numbers.e.bit_length() + 7) // 8, "big"
        )
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self._kid,
                    "n": _b64url(n_bytes),
                    "e": _b64url(e_bytes),
                }
            ]
        }
