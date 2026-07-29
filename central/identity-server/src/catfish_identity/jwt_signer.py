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

    # 输的一方等赢家写完的最长时间. RSA 2048 生成 ~0.1s, 8s 是大裕量.
    _PEER_WAIT_SEC = 8.0

    def _load_or_generate(self) -> rsa.RSAPrivateKey:
        """加载本地密钥, 没有就生成 + 持久化.

        # 为啥要防并发 (P3.5.80 · 7/28 鸿波达华现场血泪)

        老实现是 `if exists(): load  else: generate + write`, **无锁且非原子**.
        `UVICORN_WORKERS` 默认 2, 两个 worker 同时启动时都看到文件不存在,
        于是**各自生成一把不同的 RSA**; 写盘时后写的覆盖先写的, 但每个
        worker 内存里留着自己那把.

        后果: kid = sha256(公钥)[:16], 两把密钥 → 两个不同 kid.
        `/.well-known/jwks.json` 由哪个 worker 应答就返哪把 ——
        worker A 签发的 token 拿 worker B 的 JWKS 去验必然失败,
        表现为**登录时好时坏, 大约一半概率**. 7/28 达华现场日志实证:
        "私钥不存在, 生成新 RSA" 连打两行 = 两个 worker 都走了生成分支.

        # 修法

        先写唯一临时文件, 再 `os.link()` 原子挂到最终路径 ——
        link 的目标已存在会直接 FileExistsError, 所以:
          · 最终文件**永远不会**出现"已创建但内容不全"的中间态
          · 抢输的一方读到的必定是赢家写完的完整密钥
        输的一方轮询等待 (赢家可能还在 link 的路上), 超时 fail-loud.
        """
        # ── 快路径: 已经有了直接读 ──
        # strict=True: 文件非空却解析不了 = 真损坏, 立刻 raise, 不静默重生成
        key = self._try_load(strict=True)
        if key is not None:
            return key

        # ── 慢路径: 生成 → 临时文件 → 原子挂载 ──
        new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_pem = new_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = new_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        tmp = self.key_dir / f".private.pem.tmp.{os.getpid()}"
        try:
            # 0600 落盘, 再 link. 权限在 link 前设好, 避免出现可读窗口.
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(priv_pem)

            try:
                os.link(tmp, self._private_path)
                won = True
            except FileExistsError:
                # 别的 worker 先挂上去了 —— 用它那把, 丢掉自己生成的
                won = False
            except (AttributeError, OSError):
                # 文件系统不支持 hardlink (罕见). 退回 O_EXCL 直写,
                # 仍是原子创建, 只是内容有极短的空窗.
                try:
                    fd2 = os.open(
                        self._private_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                    with os.fdopen(fd2, "wb") as f:
                        f.write(priv_pem)
                    won = True
                except FileExistsError:
                    won = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        if won:
            logger.warning("私钥不存在, 已生成新 RSA 2048 密钥对到 %s", self.key_dir)
            try:
                self._public_path.write_bytes(pub_pem)
            except OSError as e:
                # 公钥只是方便运维查看, JWKS 是从私钥现算的, 写失败不致命
                logger.warning("public.pem 写入失败 (不影响签发): %s", e)
            return new_key

        # 抢输了 —— 等赢家的密钥落定再读, 保证两个 worker 同一把 kid
        logger.info("另一个 worker 已生成密钥, 加载它那把 (避免 kid 不一致)")
        peer = self._wait_for_peer_key()
        if peer is None:
            raise RuntimeError(
                f"等待另一个 worker 写入 {self._private_path} 超时 "
                f"({self._PEER_WAIT_SEC}s). 若该文件残留为空, 删掉它重启 identity."
            )
        return peer

    def _try_load(self, *, strict: bool) -> rsa.RSAPrivateKey | None:
        """读现有私钥. 不存在 / 空 → None.

        strict=True  (启动快路径): 文件非空却解析不了 = 真损坏 → raise.
                     不能静默重新生成 —— 那会悄悄换掉签名密钥,
                     已签发的 token 全部失效且无人察觉.
        strict=False (等待另一个 worker): 解析不了当作"还没写完" → None,
                     让调用方继续轮询.
        """
        try:
            pem = self._private_path.read_bytes()
        except (FileNotFoundError, OSError):
            return None
        if not pem.strip():
            return None
        try:
            key = serialization.load_pem_private_key(pem, password=None)
        except (ValueError, TypeError):
            if strict:
                raise
            return None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise RuntimeError(
                f"{self._private_path} 不是 RSA 密钥. 删了让自动重新生成."
            )
        return key

    def _wait_for_peer_key(self) -> rsa.RSAPrivateKey | None:
        """轮询等另一个 worker 把密钥写完. 超时返 None."""
        deadline = time.monotonic() + self._PEER_WAIT_SEC
        while time.monotonic() < deadline:
            key = self._try_load(strict=False)
            if key is not None:
                return key
            time.sleep(0.05)
        return None

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
