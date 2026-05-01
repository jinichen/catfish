"""Plan D · A2A audit log — 五一 sprint Day 5 (BL-M5.2).

# 用途

每次 A2A 调用 (出/入), 双方 catfish 都写一行 jsonl 到 `~/.catfish/a2a_audit.jsonl`.

格式 (PLAN-D-PROTOCOL.md § 5):

A 端 (outbound):
  {ts, direction:"outbound", to_sub, question, jti, status:"ok"|"denied"|"error",
   error_code?, error_msg?, duration_ms?, chunks?, audit_id_remote?}

B 端 (inbound):
  {ts, direction:"inbound", from_sub, question, jti, status, allow_match?,
   duration_ms?, audit_id?}

# 隐私边界

- A2A audit 跟 gateway audit 一样, 中央**不看**, 只在员工本机.
- 客户 IT 想审 A2A 跨员工沟通, grep ~/.catfish/a2a_audit.jsonl 即可.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.a2a_audit")


def _audit_path() -> Path:
    """A2A audit 路径. ~/.catfish/a2a_audit.jsonl. CATFISH_A2A_AUDIT_PATH override."""
    custom = os.environ.get("CATFISH_A2A_AUDIT_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".catfish" / "a2a_audit.jsonl"


def write_audit(event: dict[str, Any]) -> None:
    """append 一行 JSON. 失败静默 (audit 不阻塞主流程)."""
    if "ts" not in event:
        event["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning("a2a_audit 写失败: %s", e)


__all__ = ["write_audit"]
