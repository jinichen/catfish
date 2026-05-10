"""catfish_skill_publish 工具实现 (BL-D2 5/10).

把员工本机一个 skill 目录打包 multipart 发到 gateway /v1/hub/skills/{ns}.

走 gateway (8999) 而不是直连 hub (8997), 因为:
  - 单 origin 简化 token 路径 (跟 chat / mcp-registry 同条 OAuth id_token)
  - gateway OIDC 验签后注入 X-Catfish-User-Sub, hub 端用 sub 当 published_by
  - 不暴露 hub 端口给员工本机进程, 防绕权限

# Token 来源

通过 secret_broker 拿当前员工的 OAuth id_token? 不 — tool-bridge 没接 OAuth.
通过 gateway dev_token? BL-FIX29 关掉了.

实际方案: tool-bridge 通过 gateway loopback 用 internal token (BL-FIX37 同款).
但 internal token 解码身份是 'internal:gateway-loopback', published_by 不准.

更好: 从 ~/.catfish/oauth/id_token 文件读 (Companion 写的, BL-FIX32). tool-bridge
跟 Companion 同机, 同 ~/.catfish 目录, 直接读. published_by 就是真员工 sub.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.skill_publish")

#: Companion 写 OAuth token 的位置 (BL-FIX32)
OAUTH_ID_TOKEN_PATH = Path.home() / ".catfish" / "oauth" / "id_token"

#: gateway URL — 跟 chat / mcp-registry 走同条
import os as _os
_GATEWAY_PORT = _os.environ.get("CATFISH_GATEWAY_PORT", "8999")
_GATEWAY_HOST = _os.environ.get("CATFISH_GATEWAY_HOST", "127.0.0.1")
GATEWAY_URL = _os.environ.get(
    "CATFISH_GATEWAY_URL",
    f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}",
)

#: 凭据 / 敏感字段正则 — publish 前 grep, 撞到拒绝
_SENSITIVE_PATTERNS = [
    re.compile(r"\bpassword\s*[:=]\s*['\"]?[A-Za-z0-9_\-!@#$%^&*]{4,}", re.IGNORECASE),
    re.compile(r"\bapi_?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"\bsecret_?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),  # OpenAI / DeepSeek key
    re.compile(r"\bAIza[A-Za-z0-9_\-]{35}"),  # Gemini key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # SSH / RSA key
]

#: skill 目录里要打包的文件后缀
_SKILL_FILE_EXTS = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".sh"}


def _read_id_token() -> str | None:
    """读 OAuth id_token, 没就返 None."""
    if not OAUTH_ID_TOKEN_PATH.exists():
        return None
    try:
        return OAUTH_ID_TOKEN_PATH.read_text(encoding="utf-8").strip() or None
    except Exception as e:
        logger.warning("读 id_token 失败: %s", e)
        return None


def _scan_sensitive(file_map: dict[str, bytes]) -> list[str]:
    """扫所有文件内容找凭据. 返命中文件名列表."""
    hits = []
    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            continue
        for pat in _SENSITIVE_PATTERNS:
            if pat.search(text):
                hits.append(f"{rel} (撞 {pat.pattern[:40]})")
                break
    return hits


def _collect_files(skill_dir: Path) -> dict[str, bytes]:
    """递归扫 skill 目录, 只收 _SKILL_FILE_EXTS 后缀的小文件 (单文件 < 1MB)."""
    if not skill_dir.exists() or not skill_dir.is_dir():
        return {}
    file_map: dict[str, bytes] = {}
    for p in skill_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _SKILL_FILE_EXTS:
            continue
        if p.stat().st_size > 1024 * 1024:  # 1MB 上限
            logger.warning("跳过大文件 %s (%d bytes)", p, p.stat().st_size)
            continue
        rel = str(p.relative_to(skill_dir))
        file_map[rel] = p.read_bytes()
    return file_map


def skill_publish(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry — 检查 + 打包 + multipart upload 到 gateway.

    参数:
      skill_path (必): 本机 skill 目录 (含 SKILL.md)
      namespace (必): hub 上 namespace
    """
    skill_path = (args.get("skill_path") or "").strip()
    namespace = (args.get("namespace") or "").strip()

    if not skill_path:
        return {"ok": False, "error": "skill_path 必填"}
    if not namespace:
        return {"ok": False, "error": "namespace 必填"}
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", namespace):
        return {
            "ok": False,
            "error": f"namespace 只允许小写字母/数字/-/_ (拿到 {namespace!r})",
        }

    skill_dir = Path(skill_path).expanduser().resolve()
    if not skill_dir.exists():
        return {"ok": False, "error": f"skill 目录不存在: {skill_dir}"}
    if not skill_dir.is_dir():
        return {"ok": False, "error": f"不是目录: {skill_dir}"}

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {
            "ok": False,
            "error": f"缺 SKILL.md ({skill_md}), 不是合法 skill 目录",
        }

    # 扫文件
    file_map = _collect_files(skill_dir)
    if not file_map:
        return {"ok": False, "error": "skill 目录里没扫到任何可打包文件"}
    if "SKILL.md" not in file_map:
        return {"ok": False, "error": "SKILL.md 没收到 (可能是软链或权限问题)"}

    # 凭据扫描 (publish 不能上传含密码 / api key 的文件)
    leaks = _scan_sensitive(file_map)
    if leaks:
        return {
            "ok": False,
            "error": (
                "publish 前安全扫描命中疑似凭据, 拒绝上传:\n  "
                + "\n  ".join(leaks)
                + "\n请把凭据改成 keychain:// ref 后重试."
            ),
        }

    # 拿 OAuth id_token
    token = _read_id_token()
    if not token:
        return {
            "ok": False,
            "error": (
                f"OAuth id_token 不存在 ({OAUTH_ID_TOKEN_PATH}). 在 Companion "
                "里点登录走 OIDC 流, 之后 publish 才能用真员工身份."
            ),
        }

    # POST multipart 到 gateway
    import httpx  # 懒 import

    url = f"{GATEWAY_URL}/v1/hub/skills/{namespace}"
    files_payload = [
        ("files", (rel, content, "application/octet-stream"))
        for rel, content in file_map.items()
    ]
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                files=files_payload,
            )
    except httpx.RequestError as e:
        return {"ok": False, "error": f"调 gateway 失败: {e!r}"}

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text[:500]
        return {
            "ok": False,
            "error": f"gateway {resp.status_code}: {detail}",
        }

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": f"gateway 返非 json: {resp.text[:200]}"}

    return {
        "ok": True,
        "namespace": data.get("namespace", namespace),
        "name": data.get("name"),
        "version": data.get("version"),
        "published_at": data.get("published_at"),
        "files_count": len(file_map),
        "hub_url": f"{GATEWAY_URL}/v1/hub/skills/{namespace}/{data.get('name')}",
    }
