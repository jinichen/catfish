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
import re
import logging
import os
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.email_draft_to_client")

_SUBPROCESS_TIMEOUT = 20.0  # AppleScript 建草稿要跟 Mail.app 打交道, 给宽点


def _find_catfish_email() -> str | None:
    from .email_runtime import find_email_cli
    return find_email_cli()


# 11/5: 附件 (同事任务要交回文件时, 小鲶把文件放进发给发起人的邮件草稿)。草稿员工看得见、发不发他定,
# 但模型给的路径还是要拦: 只认主目录下的普通文件, 路径里不能有隐藏目录 (~/.ssh、~/.catfish/oauth 这类),
# 小鲶自己的产出目录 ~/.catfish/outputs 除外; 单个 / 合计都有上限 (邮箱服务器一般 20~25MB)。
ATTACH_MAX_FILES = 10
ATTACH_MAX_BYTES = 20 * 1024 * 1024


def check_attachments(raw: Any) -> list[str]:
    """附件路径 → 规范化后的绝对路径列表。不合规的直接报人话 (ValueError)。"""
    from pathlib import Path  # noqa: PLC0415
    if not raw:
        return []
    items = [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, (list, tuple)) else None
    if items is None:
        raise ValueError("attachments 要是文件路径的列表")
    if len(items) > ATTACH_MAX_FILES:
        raise ValueError(f"一封最多带 {ATTACH_MAX_FILES} 个附件")
    home = Path.home().resolve()
    outputs = (home / ".catfish" / "outputs").resolve()
    out, total = [], 0
    for x in items:
        p = Path(os.path.expanduser(str(x).strip())).resolve()
        if not p.is_file():
            raise ValueError(f"附件找不到 (或不是文件): {x}")
        inside_outputs = p.is_relative_to(outputs)
        if not inside_outputs:
            if not p.is_relative_to(home):
                raise ValueError(f"只能带你主目录下的文件: {x}")
            if any(part.startswith(".") for part in p.relative_to(home).parts):
                raise ValueError(f"隐藏目录里的文件不能当附件 (可能是密钥 / 配置): {x}")
        size = p.stat().st_size
        total += size
        if size > ATTACH_MAX_BYTES or total > ATTACH_MAX_BYTES:
            raise ValueError(f"附件太大 (合计最多 {ATTACH_MAX_BYTES // 1024 // 1024}MB): {p.name} —— 大文件用网盘链接")
        out.append(str(p))
    return out


_FORWARD_PREFIX = re.compile(r"^\s*(转发|轉發|fwd?|fw)\s*[:：]", re.IGNORECASE)


def _is_forward_subject(subject: str) -> bool:
    return bool(_FORWARD_PREFIX.match(subject or ""))


def tool_email_create_draft(args: dict[str, Any]) -> dict[str, Any]:
    """把定稿放进邮件客户端草稿箱 (不发送)。args:
        to (str, 必填): 收件人, 多人逗号分隔
        subject (str, 必填)
        body (str, 必填): 正文定稿
        cc (str, 可选): 抄送, 多人逗号
        in_reply_to (str, 可选): 原邮件 id (回复场景传, 客户端才能串 thread)
        forward_of (str, 可选): 转发的原邮件 id —— 原文 + 原附件由 CLI 接上 (11/4)
        account (str, 可选): 从哪个账号起草
        replaces (str, 可选): 改已有草稿时传旧草稿 id, 存好新的后旧的去掉
        attachments (list[str], 可选): 附件的本地路径 (11/5; 只认主目录下、不在隐藏目录里的普通文件, 合计 20MB)

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

    try:
        attachments = check_attachments(args.get("attachments"))
    except ValueError as e:
        return {"ok": False, "error": str(e)}

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
        forward_of = str(args.get("forward_of") or "").strip()
        # 11/4 真机: 小鲶转发时把原邮件 id 填进了 in_reply_to (当回复串线程), 没填 forward_of,
        # 草稿里只有「原文见下方」。主题是转发 (转发: / Fwd: / FW:) 就按转发办 —— 转发本来也不串线程。
        if not forward_of and in_reply_to and _is_forward_subject(subject):
            forward_of, in_reply_to = in_reply_to, ""
        if in_reply_to:
            cmd += ["--in-reply-to", in_reply_to]
        if forward_of:
            cmd += ["--forward", forward_of]
        account = str(args.get("account") or "").strip()
        if account:
            cmd += ["--account", account]
        # 9/27: 改草稿箱里已有的那封 —— 新版本存好后旧的自动去掉, 不留重复。
        # 以前只能再建一封, 员工改几次草稿箱里就堆几封一样的。
        replaces = str(args.get("replaces") or "").strip()
        if replaces:
            cmd += ["--replace", replaces]
        for a in attachments:
            cmd += ["--attach", a]

        # 转发要先读原信、导出原附件, 比单建草稿慢得多
        timeout = _SUBPROCESS_TIMEOUT * 4 if forward_of or attachments else _SUBPROCESS_TIMEOUT
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"CLI 超时 ({timeout:.0f}s) — Mail.app 卡住了?",
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
                ("草稿已改好, 草稿箱里只留这一版 (没有发送)。" if replaces
                 else "草稿已放进草稿箱 (没有发送)。")
                + "鲶鱼会打开邮件页的草稿箱并选中它, 核对内容后由你自己点发送。"
            ),
        }
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
