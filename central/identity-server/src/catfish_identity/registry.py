"""Plan D · Catfish Federation registry — 五一 sprint Day 4 (BL-M4.1).

# 用途

每个 catfish 实例 (Alice / Bob / ...) 启动时调 `POST /registry/register` 自报家门,
中央 catfish-identity 写入 yaml. A 实例要调 B 实例时, 先 `GET /registry/lookup`
拿到 B 的 catfish_endpoint + jwks_uri.

# 数据流 (跟 PLAN-D-PROTOCOL.md § 6 对齐)

```
A catfish 启动 → POST /registry/register
                  body: {sub, catfish_endpoint, jwks_uri, department, capabilities}
                  ↓
                  写 ~/.catfish-identity/registry.yaml

A 想调 B → GET /registry/lookup?sub=bob@ffcs.cn
           ↓
           返 {catfish_endpoint, jwks_uri, department, last_seen, capabilities}

A 用 B 的 jwks 验签 + 调 B 的 catfish_endpoint
```

# 关键设计

- registry **只查 + 注册**, 不转中转流量, 不看对话内容
- yaml 短期 (Phase 1), Phase 2 升级 PG (BL-D17)
- last_seen 超过 2 分钟视为离线 (A 调时 -32004)
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

logger = logging.getLogger("catfish.identity.registry")


# 离线判断阈值
OFFLINE_AFTER_SECONDS = 120  # 2 分钟没 register 视为离线


def _registry_path() -> Path:
    """registry yaml 路径. 默认 ~/.catfish-identity/registry.yaml.

    可通过 env CATFISH_REGISTRY_PATH override (单机 mock 用).
    """
    custom = os.environ.get("CATFISH_REGISTRY_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".catfish-identity" / "registry.yaml"


@dataclass
class RegistryEntry:
    """单个 catfish 实例的注册信息."""

    sub: str  # SSO sub, 例 alice@ffcs.cn
    catfish_endpoint: str  # https://alice.catfish.local:8999
    jwks_uri: str  # 五一 sprint Day 5: 默认指向中央 catfish-identity 的 /agents/<sub>/jwks.json
    public_pem: str = ""  # 员工本机 RSA 公钥 PEM, register 时上传, 用于生成 jwks
    department: str = ""
    capabilities: list[str] = field(default_factory=list)
    # BL-FED2.2 (5/12 鸿波拍板) 黄页用 — 员工 confirmed 过的专长 tag 串.
    # 跟 capabilities 区分: capabilities 是协议层 ("a2a.ask"), expertise 是
    # 业务层 ("资质", "外勤报销"). 隐私边界: 由 tool-bridge expertise.export_for_registry()
    # 出, 只含 status=confirmed, 不含 evidence/aliases/source.
    expertise: list[str] = field(default_factory=list)
    last_seen_iso: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "catfish_endpoint": self.catfish_endpoint,
            "jwks_uri": self.jwks_uri,
            "public_pem": self.public_pem,
            "department": self.department,
            "capabilities": self.capabilities,
            "expertise": self.expertise,
            "last_seen": self.last_seen_iso,
        }

    def is_online(self) -> bool:
        """last_seen 在 2 分钟内 = 在线."""
        if not self.last_seen_iso:
            return False
        try:
            last = datetime.fromisoformat(self.last_seen_iso.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - last).total_seconds() < OFFLINE_AFTER_SECONDS
        except Exception:
            return False


def _load_registry() -> dict[str, RegistryEntry]:
    """从 yaml 加载 (sync). 文件不存在返空 dict.

    PG 模式由 _load_registry_async 走 (async, 优先 PG fallback yaml).
    sync 路径仅 unit test / dev mode.
    """
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("registry yaml 解析失败: %s", e)
        return {}

    agents = data.get("agents", {}) or {}
    result: dict[str, RegistryEntry] = {}
    for sub, entry in agents.items():
        if not isinstance(entry, dict):
            continue
        result[sub] = RegistryEntry(
            sub=sub,
            catfish_endpoint=entry.get("catfish_endpoint", ""),
            jwks_uri=entry.get("jwks_uri", ""),
            public_pem=entry.get("public_pem", ""),
            department=entry.get("department", ""),
            capabilities=entry.get("capabilities") or [],
            expertise=entry.get("expertise") or [],
            last_seen_iso=entry.get("last_seen", ""),
        )
    return result


async def _load_registry_async() -> dict[str, RegistryEntry]:
    """优先 PG, fallback yaml. catfish-identity app 内 endpoint 用这个 (async)."""
    from .db import get_pool  # noqa: PLC0415

    pool = await get_pool()
    if pool is None:
        return _load_registry()  # yaml fallback

    try:
        async with pool.acquire() as conn:
            # BL-FED2.2 (5/12) — expertise 列可能未 migration, 用条件取
            try:
                rows = await conn.fetch(
                    "SELECT sub, catfish_endpoint, jwks_uri, public_pem, "
                    "department, capabilities, expertise, last_seen FROM registry_agents"
                )
                _has_expertise_col = True
            except Exception:
                # 老 schema 没 expertise 列 (alembic 还没跑) — 兼容降级
                rows = await conn.fetch(
                    "SELECT sub, catfish_endpoint, jwks_uri, public_pem, "
                    "department, capabilities, last_seen FROM registry_agents"
                )
                _has_expertise_col = False
    except Exception as e:
        logger.warning("PG registry 读取失败, fallback yaml: %s", e)
        return _load_registry()

    import json as _json  # noqa: PLC0415

    def _coerce_jsonb_list(val: Any) -> list[str]:
        if isinstance(val, str):
            try:
                val = _json.loads(val)
            except Exception:
                val = []
        if not isinstance(val, list):
            val = []
        return [str(x) for x in val]

    result: dict[str, RegistryEntry] = {}
    for row in rows:
        capabilities = _coerce_jsonb_list(row["capabilities"])
        expertise = _coerce_jsonb_list(row["expertise"]) if _has_expertise_col else []
        last_seen = row["last_seen"]
        last_seen_iso = last_seen.isoformat() if last_seen else ""
        result[row["sub"]] = RegistryEntry(
            sub=row["sub"],
            catfish_endpoint=row["catfish_endpoint"] or "",
            jwks_uri=row["jwks_uri"] or "",
            public_pem=row["public_pem"] or "",
            department=row["department"] or "",
            capabilities=capabilities,
            expertise=expertise,
            last_seen_iso=last_seen_iso,
        )
    return result


async def _save_registry_async(entries: dict[str, RegistryEntry]) -> bool:
    """优先 PG (UPSERT), fallback yaml. 返 True 写 PG, False 写 yaml."""
    from .db import get_pool  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    pool = await get_pool()
    if pool is None:
        _save_registry(entries)
        return False

    # BL-FED2.2 (5/12) — 检测 expertise 列是否存在 (老 schema 兼容)
    has_expertise_col = False
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='registry_agents' AND column_name='expertise'"
            )
            has_expertise_col = row is not None
    except Exception:
        has_expertise_col = False

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 简化: 全删全插 (适合 < 100 个 agent 的场景, Phase 2 改成 UPSERT 单条)
                await conn.execute("TRUNCATE registry_agents")
                for sub, e in entries.items():
                    last_seen = None
                    if e.last_seen_iso:
                        try:
                            last_seen = datetime.fromisoformat(
                                e.last_seen_iso.replace("Z", "+00:00")
                            )
                        except Exception:
                            last_seen = datetime.now(timezone.utc)
                    if has_expertise_col:
                        await conn.execute(
                            "INSERT INTO registry_agents (sub, catfish_endpoint, jwks_uri, "
                            "public_pem, department, capabilities, expertise, last_seen) "
                            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)",
                            sub,
                            e.catfish_endpoint,
                            e.jwks_uri,
                            e.public_pem,
                            e.department,
                            _json.dumps(e.capabilities),
                            _json.dumps(e.expertise),
                            last_seen or datetime.now(timezone.utc),
                        )
                    else:
                        await conn.execute(
                            "INSERT INTO registry_agents (sub, catfish_endpoint, jwks_uri, "
                            "public_pem, department, capabilities, last_seen) "
                            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)",
                            sub,
                            e.catfish_endpoint,
                            e.jwks_uri,
                            e.public_pem,
                            e.department,
                            _json.dumps(e.capabilities),
                            last_seen or datetime.now(timezone.utc),
                        )
        return True
    except Exception as ex:
        logger.warning("PG registry 写失败, fallback yaml: %s", ex)
        _save_registry(entries)
        return False


def _save_registry(entries: dict[str, RegistryEntry]) -> None:
    """原子写 yaml (写 tmp + rename)."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {"agents": {sub: entry.to_dict() for sub, entry in entries.items()}}
    tmp_path = path.with_suffix(".yaml.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)
        tmp_path.replace(path)
    except Exception as e:
        logger.error("registry yaml 写入失败: %s", e)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ── API schemas ──────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    sub: str
    catfish_endpoint: str
    jwks_uri: str = ""  # 不填默认 = 中央 catfish-identity 的 /agents/<sub>/jwks.json
    public_pem: str = ""  # PEM 格式 RSA 公钥, register 时上传, 中央以此生成 jwks
    department: str = ""
    capabilities: list[str] = []
    expertise: list[str] = []  # BL-FED2.2 黄页 — 员工 confirmed 过的专长 tag


class RegisterResponse(BaseModel):
    ok: bool
    last_seen: str
    total_agents: int


class LookupResponse(BaseModel):
    sub: str
    catfish_endpoint: str
    jwks_uri: str
    department: str
    capabilities: list[str]
    expertise: list[str] = []  # BL-FED2.2 黄页 tag
    last_seen: str
    online: bool


class ByExpertiseMatch(BaseModel):
    """BL-FED2.2 黄页结果: 只返必要字段, 不返 jwks_uri/public_pem (敏感)."""
    sub: str
    department: str
    expertise: list[str]
    online: bool
    last_seen: str


class ByExpertiseResponse(BaseModel):
    tag: str  # 查询的 tag (echo)
    matched_count: int
    online_count: int
    matches: list[ByExpertiseMatch]


# ── PEM → JWK 转换 (per-agent jwks endpoint 用) ──────────────────


def _pem_to_jwk(pem: str, kid: str) -> dict[str, Any] | None:
    """RSA 公钥 PEM → JWK dict (RS256 用).

    返 None 表示 PEM 解析失败 (调用方决定 fallback).
    """
    try:
        from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

        pub = serialization.load_pem_public_key(pem.encode())
        numbers = pub.public_numbers()  # type: ignore[attr-defined]

        def int_to_b64(n: int) -> str:
            b = n.to_bytes((n.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

        return {
            "kty": "RSA",
            "kid": kid,
            "alg": "RS256",
            "use": "sig",
            "n": int_to_b64(numbers.n),
            "e": int_to_b64(numbers.e),
        }
    except Exception as e:
        logger.warning("PEM → JWK 转换失败 (kid=%s): %s", kid, e)
        return None


# ── FastAPI router ──────────────────────────────────────────────


def build_registry_router() -> APIRouter:
    """构造 registry routes. 注册到 catfish-identity app."""
    router = APIRouter(prefix="/registry", tags=["plan-d-registry"])

    @router.post("/register", response_model=RegisterResponse)
    async def register(req: RegisterRequest) -> RegisterResponse:
        """catfish 实例自报家门. 启动 + 每 60s 心跳.

        Plan D B 方案 (五一 Day 5): 接受 public_pem 字段, 中央以此生成 per-agent jwks.
        register 时:
          - jwks_uri 不填 → 默认 = 中央的 /registry/agents/<sub>/jwks.json
          - public_pem 必填 (生产) / mock 阶段缺也接受 (回退到全局 catfish-identity jwks)

        五一 sprint 5/4: 优先写 PG, fallback yaml.
        """
        if not req.sub or not req.catfish_endpoint:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sub, catfish_endpoint 必填",
            )

        jwks_uri = req.jwks_uri
        if not jwks_uri:
            issuer = os.environ.get("CATFISH_REGISTRY_ISSUER", "http://127.0.0.1:8998")
            jwks_uri = f"{issuer.rstrip('/')}/registry/agents/{req.sub}/jwks.json"

        entries = await _load_registry_async()
        now_iso = datetime.now(timezone.utc).isoformat()

        entries[req.sub] = RegistryEntry(
            sub=req.sub,
            catfish_endpoint=req.catfish_endpoint,
            jwks_uri=jwks_uri,
            public_pem=req.public_pem,
            department=req.department,
            capabilities=req.capabilities,
            expertise=req.expertise,
            last_seen_iso=now_iso,
        )

        wrote_pg = await _save_registry_async(entries)
        logger.info(
            "registry register: sub=%s endpoint=%s pub_key_len=%d capabilities=%s store=%s",
            req.sub,
            req.catfish_endpoint,
            len(req.public_pem),
            req.capabilities,
            "PG" if wrote_pg else "yaml",
        )

        return RegisterResponse(
            ok=True,
            last_seen=now_iso,
            total_agents=len(entries),
        )

    @router.get("/lookup", response_model=LookupResponse)
    async def lookup(sub: str = Query(..., description="员工 SSO sub")) -> LookupResponse:
        """A 调 B 前先 lookup, 拿 B 的 catfish_endpoint + jwks_uri."""
        entries = await _load_registry_async()
        if sub not in entries:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"agent not registered: {sub}",
            )
        e = entries[sub]
        return LookupResponse(
            sub=sub,
            catfish_endpoint=e.catfish_endpoint,
            jwks_uri=e.jwks_uri,
            department=e.department,
            capabilities=e.capabilities,
            expertise=e.expertise,
            last_seen=e.last_seen_iso,
            online=e.is_online(),
        )

    @router.get("/agents/{sub}/jwks.json")
    async def agent_jwks(sub: str) -> dict[str, Any]:
        """暴露指定 agent 的 JWKS — Plan D B 方案 per-agent jwks.

        别的 agent (B) 验 (A) 签的 JWT 时 fetch 这个 endpoint 拿 A 的公钥.
        """
        entries = await _load_registry_async()
        if sub not in entries:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"agent not registered: {sub}",
            )
        entry = entries[sub]
        if not entry.public_pem:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"agent {sub} has no registered public_pem. "
                    "register 时上传 public_pem 字段."
                ),
            )
        jwk = _pem_to_jwk(entry.public_pem, kid=sub)
        if jwk is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"public_pem of {sub} is malformed",
            )
        return {"keys": [jwk]}

    @router.get("/list", response_model=list[LookupResponse])
    async def list_agents() -> list[LookupResponse]:
        """列所有 registered agents (调试 + 仪表盘用)."""
        entries = await _load_registry_async()
        return [
            LookupResponse(
                sub=e.sub,
                catfish_endpoint=e.catfish_endpoint,
                jwks_uri=e.jwks_uri,
                department=e.department,
                capabilities=e.capabilities,
                expertise=e.expertise,
                last_seen=e.last_seen_iso,
                online=e.is_online(),
            )
            for e in entries.values()
        ]

    @router.get("/by-expertise", response_model=ByExpertiseResponse)
    async def by_expertise(
        tag: str = Query(..., description="要查的专长 tag (大小写不敏感)"),
        online_only: bool = Query(False, description="只返在线 (last_seen <2min)"),
    ) -> ByExpertiseResponse:
        """BL-FED2.2 (5/12 鸿波拍板) 黄页 — 按专长 tag 查员工.

        **隐私边界**:
          - 只返 employee 主动 register 上来的 expertise tag (member 自己机器
            上 catfish_confirm_expertise 通过的)
          - **不返** jwks_uri / public_pem / catfish_endpoint (敏感, 走 lookup
            才出)
          - 调用方拿 sub 后, 还要走 lookup + A2A 流程才能联系到员工 — 给员工
            "拒接" 的二次窗口
        """
        if not tag or not tag.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tag 必填",
            )
        norm_tag = tag.strip().lower()
        entries = await _load_registry_async()

        matches: list[ByExpertiseMatch] = []
        for e in entries.values():
            # 大小写不敏感匹配
            if any(t.strip().lower() == norm_tag for t in (e.expertise or [])):
                if online_only and not e.is_online():
                    continue
                matches.append(
                    ByExpertiseMatch(
                        sub=e.sub,
                        department=e.department,
                        expertise=e.expertise,
                        online=e.is_online(),
                        last_seen=e.last_seen_iso,
                    )
                )

        # 在线优先, 再按 last_seen 降序
        matches.sort(key=lambda m: (not m.online, m.last_seen), reverse=False)
        # last_seen 降序需要单独 — 拆两步: 先在线, 再按时间倒序
        on = sorted([m for m in matches if m.online], key=lambda m: m.last_seen, reverse=True)
        off = sorted([m for m in matches if not m.online], key=lambda m: m.last_seen, reverse=True)
        ordered = on + off

        return ByExpertiseResponse(
            tag=tag,
            matched_count=len(ordered),
            online_count=sum(1 for m in ordered if m.online),
            matches=ordered,
        )

    return router


__all__ = [
    "build_registry_router",
    "RegistryEntry",
    "RegisterRequest",
    "LookupResponse",
    "ByExpertiseMatch",
    "ByExpertiseResponse",
]
