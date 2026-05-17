"""Apple Mail (macOS Mail.app) · AppleScript-first adapter (BL-EMAIL-APPLEMAIL 5/17).

# 为啥选 Apple Mail 而不是 Outlook for Mac

Mail.app 是 macOS 系统自带, 跟 Exchange / IMAP / iCloud / Gmail 全兼容. 国内员工
不用买 Microsoft 365 订阅就能用. Outlook for Mac 在国内 enterprise 渗透度 < 20%,
Mail.app 默认装机 100%.

跟 Foxmail Mac 关系: 两个是常见 macOS 邮件客户端的双子. SKILL.md 检测哪个有数据
就用哪个 (员工通常只用 1 个).

# 数据访问方式 (3 条路, 主+辅)

1. **AppleScript** (主) — Apple 官方 Mail.app dictionary 全套, 比 Outlook for Mac
   AS 完整 + 稳定. 支持: list / read / search / draft / mark-read / move.
   走 `osascript`. 跨进程, 不需要装 pyobjc.

2. **EMLX 文件解析** (辅) — Mail 本地缓存路径
   `~/Library/Mail/V*/<Account>/<Mailbox>.mbox/<UUID>/Messages/*.emlx`.
   优势: 用户没给 Automation 权限时仍能读 (只要 Mail.app 同步过).
   劣势: 写不了草稿 (写入 emlx 后 Mail.app 不会重新索引).

3. **MailKit Extension** (P3, 不在 MVP) — Apple 11+ 新 framework, 需 Swift app.

# 红线

- create_draft 走 AppleScript (`make new outgoing message`), **不直接发**. 同 DESIGN
  里 1.3 红线 — 草稿放进 drafts mailbox, 员工开 Mail.app 自己点发.
- 没 Automation 权限时 supports_drafts → False, 降级到 EMLX 只读.

# AppleScript 教学 (员工 onboarding)

第一次调 adapter, macOS 弹窗 "Catfish wants to control Mail" — 员工要点允许.
我们装的脚本会先做 capability check: 先 `tell application "Mail" to count`,
失败则把 onboarding URL 发员工 (System Settings → Privacy → Automation).

# 实现状态

⚠️ **MVP scaffold only** — class 框 + 公共接口签到, 实方法 NotImplemented.
真上线时 (BL-EMAIL-APPLEMAIL-IMPL 跟踪): 用 `subprocess.run(['osascript', '-e', ...])`
实现 list / read / draft 3 条主路径. emlx fallback 是 follow-up.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from .base import (
    Account,
    DataNotFoundError,
    EmailAdapter,
    ListFilter,
    Message,
    NotSupportedError,
)

logger = logging.getLogger("catfish_email.adapters.apple_mail")


class AppleMailAdapter(EmailAdapter):
    """Apple Mail (macOS) AppleScript-first adapter.

    MVP scaffold. 实方法待 BL-EMAIL-APPLEMAIL-IMPL ticket 实现.
    """

    name = "apple_mail"
    # supports_drafts 由 capability check 动态决定 — 没 Automation 权限就 False.
    # 默认 True (Apple Mail 支持创建草稿), runtime 检测覆盖.
    supports_drafts = True

    def __init__(self, mail_data_dir: Path | None = None) -> None:
        """
        Args:
            mail_data_dir: Apple Mail 本地缓存目录绝对路径; None 则自动探测.
                           默认 ~/Library/Mail/V10 (macOS 14+) / V9 (Sonoma 之前).
                           只在 EMLX fallback 路径用到, AppleScript 主路径无需.
        """
        self.mail_data_dir = mail_data_dir or _detect_mail_data_dir()
        # 不在 __init__ 里抛 — adapter 可能只走 AppleScript 不读文件
        if self.mail_data_dir is None:
            logger.debug(
                "apple_mail: 找不到 Mail.app 本地缓存目录, "
                "EMLX fallback 不可用 (AppleScript 主路径仍可工作)"
            )

    # ── EmailAdapter ABC 实方法 (待 BL-EMAIL-APPLEMAIL-IMPL 填) ──

    def list_accounts(self) -> Sequence[Account]:
        """通过 AppleScript `tell application "Mail" to get accounts`."""
        raise NotImplementedError(
            "BL-EMAIL-APPLEMAIL-IMPL: AppleScript list_accounts 待实现"
        )

    def list_messages(self, account_id: str, filter: ListFilter) -> Sequence[Message]:
        """通过 AppleScript: `get messages of inbox of account id N`."""
        raise NotImplementedError(
            "BL-EMAIL-APPLEMAIL-IMPL: AppleScript list_messages 待实现"
        )

    def get_message(self, account_id: str, message_id: str) -> Message:
        """通过 AppleScript: `get content of message id ... of account ...`.
        Fallback: 读 EMLX 文件 (用 mail_data_dir).
        """
        raise NotImplementedError(
            "BL-EMAIL-APPLEMAIL-IMPL: AppleScript get_message 待实现"
        )

    def create_draft(
        self,
        account_id: str,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Sequence[str] = (),
        in_reply_to: str | None = None,
    ) -> str:
        """通过 AppleScript: `make new outgoing message with properties ...`.

        红线 (DESIGN.md 1.3): **不直接发**. 草稿写进 Drafts mailbox, 员工开
        Mail.app 自己审核 + 点 "Send". 0.5s 认知 checkpoint 不可绕.
        """
        if not self.supports_drafts:
            raise NotSupportedError(
                "Apple Mail 没拿到 Automation 权限, 不能写草稿. "
                "员工去 System Settings → Privacy & Security → Automation, "
                "勾选 Catfish → Mail."
            )
        raise NotImplementedError(
            "BL-EMAIL-APPLEMAIL-IMPL: AppleScript create_draft 待实现"
        )


# ── 路径探测 helper ──────────────────────────────────────


def _detect_mail_data_dir() -> Path | None:
    """探测 Apple Mail 本地缓存目录.

    macOS 14+: ~/Library/Mail/V10
    macOS 12-13: ~/Library/Mail/V9
    历史: V2-V8 (不再支持新版本写, 但旧邮件还能读)

    返优先级最高的现存版本, 没有则 None.
    """
    base = Path.home() / "Library" / "Mail"
    if not base.is_dir():
        return None
    candidates = sorted(
        (p for p in base.iterdir() if p.name.startswith("V") and p.is_dir()),
        key=lambda p: -int(p.name[1:]) if p.name[1:].isdigit() else 0,
    )
    return candidates[0] if candidates else None
