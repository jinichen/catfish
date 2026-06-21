"""P3.5.44 (鸿波 6/20 拍 'token 30 天过期了无人续, cascade fail'):
HERMES_SERVICE_TOKEN 自动续期.

# 治本

老设计: setup-catfish-edge.sh 跑一次 mint-hermes-service-token.sh, 拿 30 天 JWT
写 ~/.hermes/.env. 鸿波要手动 cron 才续, 没设 cron → 6/12 过期 → 所有走 P7
转发 gateway 的 /api/* 全 401 (录屏 / advisory / proactive / fetchMe / 仪表盘
quota / audit). chat 不挂仅因 hermes 直连上游 LLM, 不经 gateway.

新设计: P7 plugin 每次转发前调 `get_fresh_service_token()`:
  - 解 JWT exp (不验签), 剩 > 5 天 → 用现有
  - 剩 < 5 天 → 调 catfish-identity /token mint 新, 写 .env + os.environ → 返新
  - 没 CLIENT_SECRET / 网络挂 → fallback 返当前 (即使过期), gateway 401 让用户
    看到错误信息

# 跟手动 mint 脚本协议

mint-hermes-service-token.sh 写 ~/.hermes/.env HERMES_SERVICE_TOKEN=<jwt>.
本模块复用同 env key + 同 endpoint (catfish-identity /token client_credentials).

新需要的 env (没设就不自动续, 不破老行为):
  - CATFISH_HERMES_CLIENT_ID (默认 'hermes-cli')
  - CATFISH_HERMES_CLIENT_SECRET (优先) / CLIENT_SECRET (fallback, mint script
    老 env name). 任一设了才能 auto-renew. P3.5.48: 双 env 兼容兜底装机
    setup 跟手动 cron 用不同 name 的两套场景.
  - CATFISH_IDENTITY_URL (默认 'http://localhost:8998')
  - HERMES_ENV_PATH (默认 '~/.hermes/.env')

# fail-silent 哲学

renewal 是 hermes 进程内续命, 跟 chat / 录屏 业务无关. 网络挂 / secret 错
不阻塞业务流, 返当前 token 让 caller 看 gateway 401 (跟现状一样, 没退化).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("catfish.hermes_token_renewal")


# ── 阈值 ──────────────────────────────────────────────────────────

#: 剩余 < 5 天就 mint 新的. 给 IdP 不可达场景留 buffer (5 天内多次重试机会).
RENEW_THRESHOLD_SECS = 5 * 86400

#: mint HTTP 超时. 短 — 续 token 不该卡转发流程.
MINT_TIMEOUT_SECS = 8.0


# ── env helpers ──────────────────────────────────────────────────


def _env_path() -> Path:
    """~/.hermes/.env (跟 mint-hermes-service-token.sh 默认对齐)."""
    raw = os.environ.get("HERMES_ENV_PATH", "")
    if raw.strip():
        return Path(raw).expanduser()
    return Path.home() / ".hermes" / ".env"


def _identity_url() -> str:
    return os.environ.get(
        "CATFISH_IDENTITY_URL", "http://localhost:8998",
    ).rstrip("/")


def _client_id() -> str:
    return os.environ.get("CATFISH_HERMES_CLIENT_ID", "hermes-cli")


def _client_secret() -> Optional[str]:
    """读 client_secret. 优先 CATFISH_HERMES_CLIENT_SECRET (P3.5.44 新 env),
    fallback CLIENT_SECRET (mint-hermes-service-token.sh 老 env name).

    P3.5.48 (6/21 鸿波 catch '为啥自动续期没起来'): 老 design 只读
    CATFISH_HERMES_CLIENT_SECRET, 但鸿波装机时是按 mint script docs 设
    CLIENT_SECRET=xxx, 两个 env name 不一致 → plugin silent fail. 一致后
    setup 写一次 .env, mint 跟 plugin 自动续期都 work.
    """
    s = (
        os.environ.get("CATFISH_HERMES_CLIENT_SECRET", "").strip()
        or os.environ.get("CLIENT_SECRET", "").strip()
    )
    return s or None


# ── JWT exp 解 (不验签, 仅看 payload) ────────────────────────────


def decode_jwt_exp(token: str) -> Optional[int]:
    """从 JWT payload 抽 exp 字段 (unix ts). 不验签 — 只看 caller 想要的 exp.

    失败 (token 不是 JWT / payload 无 exp / 解 base64 错) → None.
    """
    if not token or token.count(".") != 2:
        return None
    try:
        _, payload_b64, _ = token.split(".", 2)
        # JWT base64url, 补 padding
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(payload_bytes)
        exp = payload.get("exp")
        if isinstance(exp, int):
            return exp
        if isinstance(exp, float):
            return int(exp)
        return None
    except (ValueError, json.JSONDecodeError, Exception) as e:  # noqa: BLE001
        logger.debug("decode_jwt_exp 失败 (token preview=%r): %s", token[:20], e)
        return None


def should_renew(token: str, threshold_secs: int = RENEW_THRESHOLD_SECS) -> bool:
    """剩余 < threshold (默认 5 天) 就该 renew. token 不是 JWT / 无 exp 也算该 renew
    (保险起见 — 不能用陌生 token).
    """
    exp = decode_jwt_exp(token)
    if exp is None:
        return True
    remaining = exp - int(time.time())
    return remaining < threshold_secs


# ── .env 文件原子写 ──────────────────────────────────────────────


def persist_to_env_file(env_path: Path, key: str, value: str) -> bool:
    """更新 / 追加 env 文件里的 KEY=value. 返 True (写盘成功) / False (失败).

    格式跟 mint-hermes-service-token.sh:139-177 Python 段一致: 不动注释, 找到
    key 替换原值, 没找到追加. tmp + rename 原子.
    """
    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines(keepends=False)
        else:
            lines = []

        out: list[str] = []
        found = False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                out.append(line)
                continue
            if "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k.startswith("export "):
                    k = k[len("export "):].strip()
                if k == key:
                    out.append(f"{key}={value}")
                    found = True
                    continue
            out.append(line)
        if not found:
            if out and out[-1] != "":
                out.append("")
            out.append("# P3.5.44 hermes service token auto-renew")
            out.append(f"{key}={value}")

        tmp = env_path.with_suffix(env_path.suffix + ".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(env_path)
        return True
    except OSError as e:
        logger.warning("写 %s 失败: %s", env_path, e)
        return False


# ── mint HTTP (aiohttp, plugin 内已有依赖) ───────────────────────


async def mint_service_token(
    identity_url: str, client_id: str, client_secret: str,
    timeout_secs: float = MINT_TIMEOUT_SECS,
) -> Optional[str]:
    """调 catfish-identity /token (grant_type=client_credentials) mint 新 token.

    返新 token / None (失败). 失败原因 (网络/凭据错/IdP挂) 走 log warn.
    """
    import aiohttp  # noqa: PLC0415

    url = f"{identity_url}/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_secs)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        "mint_service_token 失败 HTTP %d: %s",
                        resp.status, body[:200],
                    )
                    return None
                payload = await resp.json()
                token = payload.get("access_token")
                if not isinstance(token, str) or not token:
                    logger.warning(
                        "mint_service_token /token 响应缺 access_token: %s",
                        str(payload)[:200],
                    )
                    return None
                exp = payload.get("expires_in", "?")
                logger.info(
                    "P3.5.44 mint_service_token 成功 (expires_in=%s, ~30天)", exp,
                )
                return token
    except Exception as e:  # noqa: BLE001
        logger.warning("mint_service_token 异常: %s", e)
        return None


# ── 主入口: 续期一次性 helper ────────────────────────────────────

# 防并发 mint (多 request 同时撞过期). asyncio.Lock 在 hermes event loop 内安全.
_RENEW_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _RENEW_LOCK
    if _RENEW_LOCK is None:
        _RENEW_LOCK = asyncio.Lock()
    return _RENEW_LOCK


async def get_fresh_service_token() -> Optional[str]:
    """P7 plugin 转发前调这个. 返当前可用 token (新 mint 的 / 现有未过期 / 现有
    过期但 mint 失败时仍返).

    流程:
      1. 读 env HERMES_SERVICE_TOKEN. 没设 → 返 None (caller 该 fail-silent).
      2. should_renew=False (剩 > 5 天) → 返当前 token
      3. should_renew=True + 有 CLIENT_SECRET → 尝试 mint:
         - 成功: 写 ~/.hermes/.env + 更新 os.environ + 返新
         - 失败: warn + 返当前 token (即使过期, 让 caller 看 gateway 401 报警)
      4. should_renew=True + 无 CLIENT_SECRET → warn (员工没配 secret 不自动续)
         + 返当前 token
    """
    current = os.environ.get("HERMES_SERVICE_TOKEN", "").strip()
    if not current:
        return None
    if not should_renew(current):
        return current

    # 需要续, 拿锁 (防多个并发请求同时 mint)
    async with _get_lock():
        # double-check 等锁期间别的 task 是不是已经续了
        current = os.environ.get("HERMES_SERVICE_TOKEN", "").strip()
        if current and not should_renew(current):
            logger.debug("等锁期间 HERMES_SERVICE_TOKEN 已被别人续上, 用新的")
            return current

        secret = _client_secret()
        if not secret:
            exp = decode_jwt_exp(current)
            remain = (exp - int(time.time())) if exp else "?"
            logger.warning(
                "P3.5.44/.48 HERMES_SERVICE_TOKEN 剩 %s 秒该续但 secret 没配. "
                "设 CATFISH_HERMES_CLIENT_SECRET 或 CLIENT_SECRET 到 ~/.hermes/.env "
                "(跟 catfish-identity clients.yaml hermes-cli secret 一致), 重启 "
                "hermes 即可永续. 一次性补法: 跑 scripts/setup-catfish-edge.sh "
                "(会检测+交互式填补) 或 scripts/mint-hermes-service-token.sh "
                "--persist-secret 一次.",
                remain,
            )
            return current

        new_token = await mint_service_token(
            _identity_url(), _client_id(), secret,
        )
        if not new_token:
            # mint 失败, 用旧的 (即使过期). caller 看 gateway 401 自己处理.
            return current

        # 写盘 + 内存 env 都更新
        env_path = _env_path()
        if persist_to_env_file(env_path, "HERMES_SERVICE_TOKEN", new_token):
            logger.info("P3.5.44 token 续期写盘 %s", env_path)
        os.environ["HERMES_SERVICE_TOKEN"] = new_token
        return new_token


__all__ = [
    "RENEW_THRESHOLD_SECS",
    "decode_jwt_exp",
    "should_renew",
    "mint_service_token",
    "persist_to_env_file",
    "get_fresh_service_token",
]
