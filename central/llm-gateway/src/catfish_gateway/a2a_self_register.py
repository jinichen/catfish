"""Plan D · gateway 启动时自动 register 到中央 catfish-identity.

# 用途 (五一 sprint Day 5 B 方案)

每个 catfish 实例 (Alice / Bob / ...) 启动时:
1. 加载 ~/.catfish/identity/public.pem
2. POST CATFISH_REGISTRY_URL/registry/register, 带:
   - sub (= CATFISH_USER_SUB env)
   - catfish_endpoint (本机 gateway URL)
   - public_pem (上面加载的)
   - department / capabilities
3. 中央 registry 把 public_pem store 到 yaml, 暴露在
   `/registry/agents/<sub>/jwks.json` 让别人验签时 fetch

# 单机 mock vs 生产

- 单机 mock: 共享一个 registry (8998), alice/bob 各自的 public_pem 由各自 catfish_home 提供
- 生产: 每员工独立 catfish, register 到公司中央 catfish-identity

# 触发时机

gateway lifespan 启动期跑一次. 失败不阻塞启动 (a2a 功能不可用, 但聊天等其他功能正常).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger("catfish.gateway.a2a_self_register")


def _catfish_home() -> Path:
    return Path(os.environ.get("CATFISH_HOME") or (Path.home() / ".catfish")).expanduser()


def _public_pem_path() -> Path:
    return _catfish_home() / "identity" / "public.pem"


def _expertise_yaml_path() -> Path:
    """BL-FED2.2 — ~/.catfish/expertise.yaml 路径 (跟 tool-bridge expertise.py 对齐)."""
    return _catfish_home() / "expertise.yaml"


def _load_confirmed_expertise() -> list[str]:
    """读 ~/.catfish/expertise.yaml, 返 status=confirmed 的 tag 字符串列表.

    **隐私边界**:
      - 只返 confirmed (pending/rejected 永不出员工 mac)
      - 不返 evidence_count / aliases / source / last_reviewed_at
      - 用手写 yaml 解析, 不依赖 PyYAML, 跟 tool-bridge expertise.py 对齐 (避免
        gateway 拉新依赖)

    失败 (文件不在 / 解析坏 / 异常) 都返 [], 不阻塞 self_register.
    """
    p = _expertise_yaml_path()
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("self_register: 读 expertise.yaml 失败: %s", e)
        return []

    # 简易解析 — yaml 文件格式: tags 是顶层 list, 每条 dict 含 tag/status.
    # 跟 tool-bridge/expertise.py _yaml_dump 输出对齐:
    #   tags:
    #     - tag: 资质
    #       status: confirmed
    #       ...
    out: list[str] = []
    in_tag_block = False
    cur_tag = ""
    cur_status = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # 新 tag 块开始: "  - tag: XXX"
        stripped = line.lstrip()
        if stripped.startswith("- tag:"):
            # flush 上一块
            if in_tag_block and cur_tag and cur_status == "confirmed":
                out.append(cur_tag)
            cur_tag = _strip_yaml_str(stripped[len("- tag:"):].strip())
            cur_status = ""
            in_tag_block = True
            continue
        if in_tag_block and stripped.startswith("status:"):
            cur_status = _strip_yaml_str(stripped[len("status:"):].strip())
            continue
        # 顶层 key (如 extracted_at:) 表示 tags 块外
        if line and not line.startswith(" "):
            if in_tag_block and cur_tag and cur_status == "confirmed":
                out.append(cur_tag)
            in_tag_block = False
            cur_tag = ""
            cur_status = ""
    # 文件末尾 flush
    if in_tag_block and cur_tag and cur_status == "confirmed":
        out.append(cur_tag)

    # 去重 + 限上限 (防异常多 tag 一次性塞 register, registry 不堪)
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t and t not in seen and len(t) <= 50:
            seen.add(t)
            deduped.append(t)
        if len(deduped) >= 50:
            break
    return deduped


def _strip_yaml_str(s: str) -> str:
    """剥 yaml 字符串的引号 (跟 tool-bridge/expertise.py _strip_yaml_str 对齐)."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


async def self_register() -> bool:
    """启动时调用, register 到中央 registry.

    返回 True 成功, False 失败 (a2a 功能将不可用, 但其他功能正常).
    """
    sub = os.environ.get("CATFISH_USER_SUB", "").strip()
    if not sub:
        logger.info(
            "self_register: CATFISH_USER_SUB 未设, 跳过 (Plan D A2A 不可用). "
            "员工本机生产应通过 SSO 设置."
        )
        return False

    catfish_endpoint = os.environ.get("CATFISH_GATEWAY_URL", "").strip()
    if not catfish_endpoint:
        # 默认从端口推断
        port = os.environ.get("CATFISH_GATEWAY_PORT", "8999")
        catfish_endpoint = f"http://127.0.0.1:{port}"

    registry_url = os.environ.get("CATFISH_REGISTRY_URL", "http://127.0.0.1:8998").rstrip("/")

    # 加载 public.pem
    pub_path = _public_pem_path()
    if not pub_path.exists():
        logger.warning(
            "self_register: public.pem 不存在 (%s), Plan D A2A 不可用. "
            "跑 scripts/plan_d_mock_init.sh 生成 RSA keypair.",
            pub_path,
        )
        return False
    try:
        public_pem = pub_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("self_register: 读 public.pem 失败: %s", e)
        return False

    # 选 department / capabilities (env 或默认)
    department = os.environ.get("CATFISH_DEPARTMENT", "")
    capabilities = ["a2a.ask"]

    # BL-FED2.2 (5/12) 读 expertise (员工 confirmed 过的专长 tag) 上报到黄页.
    # 隐私边界: 只 confirmed, 失败/缺文件返 [], 不阻塞 self_register.
    expertise = _load_confirmed_expertise()

    # 显式算 jwks_uri 不依赖 registry 自动算 — 兼容老版 registry (jwks_uri 必填).
    jwks_uri = f"{registry_url}/registry/agents/{sub}/jwks.json"

    body = {
        "sub": sub,
        "catfish_endpoint": catfish_endpoint,
        "jwks_uri": jwks_uri,
        "public_pem": public_pem,
        "department": department,
        "capabilities": capabilities,
        "expertise": expertise,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{registry_url}/registry/register", json=body)
            if resp.status_code != 200:
                # 把 response body 一起记到 log, 方便诊断 422 / 5xx 等
                logger.warning(
                    "self_register: HTTP %d %s\nrequest body keys: %s\nresponse body: %s",
                    resp.status_code,
                    resp.reason_phrase,
                    list(body.keys()),
                    resp.text[:800],
                )
                return False
            data = resp.json()
        logger.info(
            "self_register: ✅ %s registered to %s (total %d agents online)",
            sub, registry_url, data.get("total_agents", "?"),
        )
        return True
    except Exception as e:
        logger.warning(
            "self_register: 失败 %s → %r. Plan D A2A 不可用, 其他功能正常.",
            registry_url, e,
        )
        return False


__all__ = ["self_register"]
