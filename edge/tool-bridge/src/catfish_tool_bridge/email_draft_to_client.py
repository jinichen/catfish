"""catfish_email_create_draft —— 把定稿放进邮件客户端**草稿箱** (8/21)。

# 这个工具补的是哪一截

对话里处理邮件, 之前到「初稿」就断了: catfish_draft_email_reply 落
`~/.catfish/outputs/reply-*.md` —— 一个 .md 文件, 员工要发还得自己复制、
切邮件页、粘贴。链路最后一截是断的。

本工具把定稿落进 **Mail.app 草稿箱** (真草稿, CLI `draft` → AppleScript
create_draft): 员工在 Mail.app 或鲶鱼邮件页看一眼、点发送。

# 红线 (CATFISH-ADVISOR-DESIGN.md:55「任何级别都不代行: 不替发邮件」)

**本工具只建草稿, 没有任何发送路径。** 发送动作永远是员工在客户端里点 ——
这不是 prompt 约束, 是能力边界: tool-bridge 根本没有暴露发送工具给模型,
CLI 的 send 子命令不在任何 schema 里。就算模型想发, 也没有那个工具可调。

分工:
  catfish_draft_email_reply    写文案 (可多口径、可两阶段问员工) → .md 给员工看
  catfish_email_create_draft   文案**员工点头之后**落草稿箱 → 员工去点发送

# 正文必须走 --body-file, 不走 argv

正文是任意文本 (可能几 KB、含引号/换行/shell 敏感字符)。放 argv 有两个问题:
转义坑 (CLI 自己都提醒 "--body 长时用 --body-file"), 以及 **argv 对本机所有
进程可见** (ps 就能看到) —— 正文可能含业务敏感内容。临时文件 0600 用完即删。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.email_draft_to_client")

_SUBPROCESS_TIMEOUT = 20.0  # AppleScript 建草稿要跟 Mail.app 打交道, 给宽点


def _find_catfish_email() -> str | None:
    """跟 email_read 一样的 CLI 定位逻辑."""
    p = shutil.which("catfish-email")
    if p:
        return p
    cand = os.path.expanduser("~/.local/bin/catfish-email")
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    return None


def tool_email_create_draft(args: dict[str, Any]) -> dict[str, Any]:
    """把定稿放进邮件客户端草稿箱 (不发送)。args:
        to (str, 必填): 收件人, 多人逗号分隔
        subject (str, 必填)
        body (str, 必填): 正文定稿
        cc (str, 可选): 抄送, 多人逗号
        in_reply_to (str, 可选): 原邮件 id (回复场景传, 客户端才能串 thread)
        account (str, 可选): 从哪个账号起草

    Returns:
        {ok, draft_id, summary} / {ok: False, error}
    """
    to = str(args.get("to") or "").strip()
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or "")
    if not to or not subject or not body.strip():
        return {
            "ok": False,
            "error": "to / subject / body 都必填 —— 草稿箱里不该出现空壳草稿",
        }

    bin_path = _find_catfish_email()
    if not bin_path:
        return {
            "ok": False,
            "error": "邮件组件 (catfish-email) 未安装 — 重启鲶鱼 Companion 会自动补装",
        }

    # 正文走临时文件 (0600), 理由见文件头。NamedTemporaryFile delete=False +
    # finally unlink: CLI 是子进程, 文件必须在它读完之前活着。
    tmp = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".txt", delete=False,
    )
    try:
        tmp.write(body)
        tmp.close()
        os.chmod(tmp.name, 0o600)

        cmd = [
            bin_path, "draft", "--json",
            "--to", to,
            "--subject", subject,
            "--body-file", tmp.name,
        ]
        cc = str(args.get("cc") or "").strip()
        if cc:
            cmd += ["--cc", cc]
        in_reply_to = str(args.get("in_reply_to") or "").strip()
        if in_reply_to:
            cmd += ["--in-reply-to", in_reply_to]
        account = str(args.get("account") or "").strip()
        if account:
            cmd += ["--account", account]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"CLI 超时 ({_SUBPROCESS_TIMEOUT}s) — Mail.app 卡住了?",
            }
        except OSError as e:
            return {"ok": False, "error": f"catfish-email 调用失败: {e}"}

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:500]
            return {
                "ok": False,
                "error": f"建草稿失败 (退出码 {result.returncode}): {stderr}",
            }

        draft_id = ""
        try:
            parsed = json.loads(result.stdout or "{}")
            draft_id = str(parsed.get("id") or parsed.get("draft_id") or "")
        except (json.JSONDecodeError, AttributeError):
            pass  # id 拿不到不算失败 — 草稿已建, 员工在草稿箱里看得见

        return {
            "ok": True,
            "draft_id": draft_id,
            # summary 是模型转述给员工的底稿 —— 把"发送在你"说死
            "summary": (
                "草稿已放进邮件客户端的草稿箱 (没有发送)。"
                "请打开 Mail.app 草稿箱 (或鲶鱼邮件页) 核对内容, 确认无误后自己点发送。"
            ),
        }
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
