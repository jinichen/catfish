"""审批留痕 (10/14, hermes v0.22 起审批默认 smart: AI 先判断, 拿不准再问员工)。

每次审批有了结果, 往 ~/.catfish/approval_audit.jsonl 追加一行 —— 管理员能查"AI 自动批了哪些、
员工批了哪些、哪些超时没执行"。两个来源:

  · hermes 的 ``post_approval_response`` 钩子: smart 判断 (decided_by=aux_llm, choice=smart_approve /
    smart_deny)、员工在微信 / Companion 里的回答、超时、通知失败
  · P54 只读代码免批 (不经过 hermes 审批流程, 没有钩子): plugin_approval_ux 直接调 ``record``

命令文字 hermes 已经脱敏过 (redact_sensitive_text), 这里再截短, 不记完整脚本。
写失败不影响审批 (审批是安全关键路径, 留痕不是)。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.plugin")

AUDIT_FILE = "approval_audit.jsonl"
_COMMAND_MAX = 300
_TEXT_MAX = 200


def _catfish_home() -> Path:
    return Path(os.environ.get("CATFISH_HOME") or (Path.home() / ".catfish"))


#: 由 plugin.py 注入 plugin_ctx (同一份实例才读得到请求上下文里的员工; 插件目录名带横线,
#: 这里自己 import 会拿到另一份模块、另一个 ContextVar)
ctx_module: Any = None


def _employee() -> str:
    try:
        return (ctx_module.CV_CF_USER.get() or "").strip() if ctx_module is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _short(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


def record(choice: str, *, decided_by: str, command: Any = "", description: Any = "",
           pattern_key: Any = "", surface: Any = "", session_key: Any = "", **_: Any) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "employee": _employee(),
        "decided_by": decided_by,
        "choice": str(choice or ""),
        "surface": str(surface or ""),
        "pattern_key": str(pattern_key or ""),
        "description": _short(description, _TEXT_MAX),
        "command": _short(command, _COMMAND_MAX),
        "session_key": str(session_key or ""),
    }
    path = _catfish_home() / AUDIT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("approval_audit: 写 %s 失败 (不影响审批): %s", path, e)


def post_approval_response_hook(**kwargs: Any) -> None:
    """hermes ``post_approval_response`` 钩子。decided_by 只有 smart 判断会带 (aux_llm), 其余是人或超时。"""
    try:
        kw = dict(kwargs)
        choice = kw.pop("choice", "")
        decided_by = kw.pop("decided_by", "") or "human"
        record(choice, decided_by=decided_by, **kw)
    except Exception as e:  # noqa: BLE001
        logger.debug("approval_audit hook 失败 (不影响审批): %s", e)
