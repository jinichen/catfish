"""P3.5.194 (7/7 鸿波军规审判): catfish_email_read + catfish_email_attachment
—— 员工主权授权读邮件正文 + 导出附件.

# 军规审判背景
P3.5.194 之前 catfish 邮件工具只有 catfish_email_search, 返 snippet + 元数据,
LLM 拿不到正文和附件. 员工用手上有的微信版 pdf 对比邮件里附件是否是最新时,
只能"你自己去 Mail 里看". Workflow 硬生生断了.

鸿波 catch: 邮件都在员工本地 (Apple Mail / Foxmail sqlite), catfish 也在员工
本地, 员工是 authorized user — "隐私红线"其实是**数据传播面控制 + 保守 default**,
不是硬隐私约束. 保守 default 值得保留 (防 LLM 自动吞邮件到 memory), 但不该硬禁,
应该开员工主权 opt-in.

# 设计
- catfish_email_read(email_id): 读单封邮件全文, 返 body_text/body_html/attachments 完整
- catfish_email_attachment(email_id, filename): 导出附件到 tmp, 返 path 供员工点开

- **不硬 approval** (跟 execute_code 走 hermes approval guard 不同). email 走
  语义驱动: SOUL.md 教 LLM "只在员工明确说'读这封'/'取附件'时调, 不自动读".
- **不缓存**: tool 不做二级缓存, 每次调用都实时 shell out CLI, 用完就走.
- **不吞进 memory / wiki**: 靠 SOUL.md 教 LLM 不主动把邮件正文/附件塞进 memory/wiki 蒸馏管道.

# CLI 依赖
- catfish-email read --id <msg_id> --json
- catfish-email attachment --id <msg_id> --filename <name> --json
(P3.5.103 6/24 CLI 已实现, 之前没暴露给 LLM.)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.email_read")

_SUBPROCESS_TIMEOUT_READ = 15.0  # 读全文可能慢 (Apple Mail EMLX 解析 + 长附件列表)
_SUBPROCESS_TIMEOUT_ATTACHMENT = 30.0  # 附件导出 IO 可能慢 (大 PDF/xlsx)
_MAX_BODY_TEXT_CHARS = 40000  # 邮件正文超过 40k 字截断 (防 context 爆)


def _find_catfish_email() -> str | None:
    """跟 email_search 一样的 CLI 定位逻辑."""
    p = shutil.which("catfish-email")
    if p:
        return p
    cand = os.path.expanduser("~/.local/bin/catfish-email")
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    return None


def _run_cli(cmd: list[str], timeout: float) -> tuple[bool, str, str]:
    """跑 CLI, 返 (ok, stdout, error_msg). 单一入口便于错误处理复用."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"CLI 超时 ({timeout}s) — 邮件客户端卡了 or 附件太大?"
    except OSError as e:
        return False, "", f"catfish-email 调用失败: {e}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:500]
        return False, "", f"CLI 失败 (退出码 {result.returncode}): {stderr}"

    return True, result.stdout or "", ""


def tool_email_read(args: dict[str, Any]) -> dict[str, Any]:
    """读单封邮件全文. args:
        email_id (str, 必填): 邮件 id (从 catfish_email_search 返的 matches[i].id 拿)
        mark_read (bool, 可选, 默认 True): 读完自动标已读 (跟员工日常习惯一致)

    Returns:
        {ok, subject, sender, recipients, cc, date, folder, adapter, account,
         is_read, body_text, has_attachments, attachments: [{filename, size_bytes, content_type}],
         error?}
    """
    email_id = str(args.get("email_id") or "").strip()
    if not email_id:
        return {"ok": False, "error": "email_id 必填 (从 catfish_email_search 返的 matches[i].id 拿)"}
    if email_id.lower() in {"null", "undefined", "none"}:
        return {"ok": False, "error": f"email_id 无效 (收到 {email_id!r}). 别用 jq 返 null 字面量."}

    bin_path = _find_catfish_email()
    if bin_path is None:
        return {
            "ok": False,
            "error": "catfish-email CLI 没装. 装一下: pip install -e ~/person_task/catfish/edge/email-agent",
        }

    mark_read = bool(args.get("mark_read", True))
    cmd = [bin_path, "read", "--id", email_id, "--json"]
    if not mark_read:
        cmd.append("--no-mark-read")

    ok, stdout, err = _run_cli(cmd, _SUBPROCESS_TIMEOUT_READ)
    if not ok:
        return {"ok": False, "error": err}

    stdout_s = stdout.strip()
    if not stdout_s:
        return {"ok": False, "error": "邮件 CLI 返了空 (邮件可能已被删/移动?)"}

    try:
        d = json.loads(stdout_s)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"邮件正文 JSON 解析失败: {e}"}

    if not isinstance(d, dict):
        return {"ok": False, "error": f"邮件正文格式意外 (期待 dict, 拿到 {type(d).__name__})"}

    # 组装 LLM 友好的返参. body_text 截断防 context 爆.
    body_text = str(d.get("body_text") or "")
    truncated = False
    if len(body_text) > _MAX_BODY_TEXT_CHARS:
        body_text = body_text[:_MAX_BODY_TEXT_CHARS] + f"\n\n[... 正文超过 {_MAX_BODY_TEXT_CHARS} 字截断; 完整正文请让员工去邮件客户端看]"
        truncated = True

    attachments = []
    for a in (d.get("attachments") or []):
        if isinstance(a, dict) and a.get("filename"):
            attachments.append({
                "filename": str(a["filename"]),
                "size_bytes": int(a.get("size_bytes") or 0),
                "content_type": str(a.get("content_type") or "application/octet-stream"),
            })

    return {
        "ok": True,
        "adapter": d.get("adapter"),
        "account": d.get("account") or "",
        "subject": d.get("subject") or "",
        "sender": d.get("sender") or "",
        "recipients": list(d.get("recipients") or []),
        "cc": list(d.get("cc") or []),
        "date": d.get("date") or "",
        "folder": d.get("folder") or "",
        "is_read": bool(d.get("is_read", False)),
        "body_text": body_text,
        "body_text_truncated": truncated,
        "has_attachments": bool(d.get("has_attachments", False)),
        "attachments": attachments,
        "attachments_count": len(attachments),
    }


def tool_email_attachment(args: dict[str, Any]) -> dict[str, Any]:
    """导出邮件附件到本地 tmp, 返 path (供员工用系统默认 app 打开, 或后续入库).

    args:
        email_id (str, 必填): 邮件 id
        filename (str, 必填): 附件 filename (从 catfish_email_read 返的 attachments 里挑)

    Returns:
        {ok, path, filename, size_bytes?, error?}

    路径处理: CLI 返 {"path": "..."}, tool 直接透传. 员工在 chat 里点或让 LLM
    调 catfish_wiki_ingest 入库.
    """
    email_id = str(args.get("email_id") or "").strip()
    filename = str(args.get("filename") or "").strip()
    if not email_id:
        return {"ok": False, "error": "email_id 必填 (从 catfish_email_search 返的 matches[i].id 拿)"}
    if not filename:
        return {"ok": False, "error": "filename 必填 (从 catfish_email_read 返的 attachments[i].filename 挑)"}
    if email_id.lower() in {"null", "undefined", "none"}:
        return {"ok": False, "error": f"email_id 无效 (收到 {email_id!r})"}

    bin_path = _find_catfish_email()
    if bin_path is None:
        return {"ok": False, "error": "catfish-email CLI 没装"}

    cmd = [bin_path, "attachment", "--id", email_id, "--filename", filename, "--json"]
    ok, stdout, err = _run_cli(cmd, _SUBPROCESS_TIMEOUT_ATTACHMENT)
    if not ok:
        return {"ok": False, "error": err, "filename": filename}

    stdout_s = stdout.strip()
    if not stdout_s:
        return {"ok": False, "error": "CLI 返了空 (附件可能不存在?)", "filename": filename}

    try:
        d = json.loads(stdout_s)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"附件导出结果 JSON 解析失败: {e}", "filename": filename}

    if not isinstance(d, dict) or not d.get("path"):
        return {"ok": False, "error": f"附件导出结果无 path 字段: {d}", "filename": filename}

    exported_path = str(d["path"])

    # 补 size_bytes (调用方常用来判断是否要入库). 从磁盘 stat 拿, 不依赖 CLI.
    size_bytes: int | None = None
    try:
        if os.path.exists(exported_path):
            size_bytes = os.path.getsize(exported_path)
    except OSError:
        pass

    return {
        "ok": True,
        "path": exported_path,
        "filename": filename,
        "size_bytes": size_bytes,
    }
