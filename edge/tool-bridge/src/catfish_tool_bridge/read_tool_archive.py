"""catfish_read_tool_archive 工具实现 (BL-Q3-ARCHIVE, 5/11).

LLM 看到 prompt 里 `[已归档: archive_ref=...]` 时调这个工具, tool-bridge 走
gateway POST /api/tool-archives/read 拿回片段 (grep / line_range / 全文).

Token 来源跟 catfish_skill_publish 同款 — 从 ~/.catfish/oauth/id_token 读
Companion 写的 OAuth id_token (BL-FIX32), 这样鉴权是真员工 sub.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.read_tool_archive")

OAUTH_ID_TOKEN_PATH = Path.home() / ".catfish" / "oauth" / "id_token"

_GATEWAY_PORT = os.environ.get("CATFISH_GATEWAY_PORT", "8999")
_GATEWAY_HOST = os.environ.get("CATFISH_GATEWAY_HOST", "127.0.0.1")
GATEWAY_URL = os.environ.get(
    "CATFISH_GATEWAY_URL",
    f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}",
)


def _read_id_token() -> str | None:
    if not OAUTH_ID_TOKEN_PATH.exists():
        return None
    try:
        return OAUTH_ID_TOKEN_PATH.read_text(encoding="utf-8").strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning("读 id_token 失败: %s", e)
        return None


def read_tool_archive(args: dict[str, Any]) -> dict[str, Any]:
    """LLM 调过来. 参数:
      ref: str (必填)
      line_range: str | None  '40-80' / '47'
      grep: str | None
      max_bytes: int | None
    """
    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"ok": False, "error": "ref 必填"}

    token = _read_id_token()
    if not token:
        return {
            "ok": False,
            "error": "未登录 catfish (~/.catfish/oauth/id_token 没找到)",
        }

    payload = {"ref": ref}
    if args.get("line_range"):
        payload["line_range"] = str(args["line_range"])
    if args.get("grep"):
        payload["grep"] = str(args["grep"])
    if args.get("max_bytes"):
        try:
            payload["max_bytes"] = int(args["max_bytes"])
        except (TypeError, ValueError):
            pass

    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return {"ok": False, "error": "tool-bridge 缺 httpx 依赖"}

    url = f"{GATEWAY_URL}/api/tool-archives/read"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code == 404:
            return {
                "ok": False,
                "error": f"archive {ref} 不存在或已过期 (14 天保留)",
            }
        if resp.status_code == 403:
            return {
                "ok": False,
                "error": "无权读这条 archive (不是你的或权限不足)",
            }
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"gateway {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        return {
            "ok": True,
            "ref": data.get("ref", ref),
            "content": data.get("content", ""),
            "total_lines": data.get("total_lines"),
            "total_bytes": data.get("total_bytes"),
            "tool_name": data.get("tool_name"),
            "summary": data.get("summary"),
            "hint": (
                "已拿到片段. 若仍不够, 缩小 grep 范围或精确 line_range. "
                "不要在 prompt 里完整复述这段, 你已经看到了."
            ),
        }
    except Exception as e:  # noqa: BLE001
        logger.error("read_tool_archive HTTP 失败: %s", e)
        return {"ok": False, "error": f"网络异常: {type(e).__name__}: {e}"}


__all__ = ["read_tool_archive"]
