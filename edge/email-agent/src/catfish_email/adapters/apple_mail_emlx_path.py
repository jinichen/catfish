"""Apple Mail · EMLX 只读兜底路径 (mixin)。

员工不给 Automation 权限时 AppleScript 全线不通, 这时降级去直接读磁盘上的
`.emlx` 文件。整条兜底路径的方法集中在这里, 从 apple_mail.py 抽出 (8/13)。

# 为什么是 mixin 而不是自由函数

自由函数 (`list_accounts(mail_dir)` 这种) 参数显式、更好测, 一开始是首选。但
换成自由函数意味着**每个方法体都要改** —— `self._list_accounts_emlx()` 变成
`list_accounts(mail_dir)`。用 mixin 则方法体一个字节都不用动, 搬运正确性可以
机械验证。今天已经在三个"看着显然"的判断上翻过车, 这次选能验的那条路。

想换成自由函数是好事, 但那是一次**带行为风险**的重构, 该单独做、单独验, 不该
搭在一次"拆文件"里蹭过去。

# 对宿主类的要求 (隐式契约, 由 test_emlx_mixin_contract 钉住)

    self._emlx_mail_dir     Path | None   EMLX 数据根目录
    self._use_emlx_fallback bool          是否已切到兜底
    self.supports_drafts    bool          激活兜底时会被置 False (只读)
    self._unpack_id()       staticmethod  拆 'account|msgid'
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from .apple_mail_emlx import (
    _detect_mail_data_dir,
    _find_emlx_files,
    _parse_emlx_full,
    _parse_emlx_summary,
    _parse_email_from_dir_name,
    _read_emlx_raw,
    _save_attachment_payload_from_source,
)
from .base import Account, DataNotFoundError, ListFilter, Message

logger = logging.getLogger("catfish_email.adapters.apple_mail")


class EmlxFallbackMixin:
    """EMLX 只读兜底。必须混进一个满足上面契约的 EmailAdapter 子类。"""

    def _export_attachment_emlx(self, message_id: str, filename: str) -> Path:
        """EMLX 路径: 直接拿 .emlx 文件 (本身就是 RFC822 + plist trailer) 解析."""
        # 复用 _read_message_emlx 的 id → emlx_path 解析逻辑
        _, msg_id = self._unpack_id(message_id)
        emlx_path = Path(msg_id.removeprefix("emlx:"))
        if not emlx_path.exists():
            raise DataNotFoundError(f"emlx 文件不存在: {emlx_path}")
        raw, _plist = _read_emlx_raw(emlx_path)
        # 写 raw 到临时 RFC822 文件, 复用 _save_attachment_payload_from_source
        with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tf:
            tf.write(raw)
            source_path = tf.name
        try:
            out_path = _save_attachment_payload_from_source(source_path, filename)
            if out_path is None:
                raise DataNotFoundError(
                    f"附件 {filename!r} 在邮件 {message_id!r} (emlx) 里找不到"
                )
            return out_path
        finally:
            try:
                os.unlink(source_path)
            except OSError:
                pass

    def _enable_emlx_fallback_if_available(self) -> bool:
        """探测本机 EMLX 数据目录. 有 → 切 fallback 返 True; 没 → False.

        副作用: 设 _use_emlx_fallback=True + supports_drafts=False.
        """
        if self._use_emlx_fallback:
            return True
        mail_dir = _detect_mail_data_dir()
        if mail_dir is None:
            return False
        self._emlx_mail_dir = mail_dir
        self._use_emlx_fallback = True
        self.supports_drafts = False  # EMLX 只读, 不能写草稿
        logger.info(
            "apple_mail EMLX fallback 激活: %s (只读, create_draft 会抛 "
            "NotSupportedError)", mail_dir,
        )
        return True

    def _list_accounts_emlx(self) -> list[Account]:
        """扫 V* 目录里子目录, 名字 parse 出 email."""
        mail_dir = self._emlx_mail_dir
        if mail_dir is None or not mail_dir.is_dir():
            raise DataNotFoundError(
                f"EMLX fallback: 找不到 Mail 数据目录 {mail_dir}",
            )
        accounts: list[Account] = []
        for child in sorted(mail_dir.iterdir()):
            if not child.is_dir():
                continue
            # 跳过 MailData / Mailboxes 等系统目录
            if child.name in ("MailData", "Mailboxes"):
                continue
            email_addr = _parse_email_from_dir_name(child.name)
            display_name = email_addr or child.name
            accounts.append(
                Account(
                    name=display_name,
                    address=email_addr or display_name,
                    is_default=(len(accounts) == 0),  # 第一个标 default
                ),
            )
        if not accounts:
            raise DataNotFoundError(
                f"EMLX fallback: {mail_dir} 下没找到账号目录",
            )
        return accounts

    def _account_dir_emlx(self, account_name: str) -> Path:
        """resolve 账号目录. account_name 可能是显示名 / email 地址."""
        mail_dir = self._emlx_mail_dir
        if mail_dir is None:
            raise DataNotFoundError("EMLX mail_dir 未初始化")
        for child in mail_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name in ("MailData", "Mailboxes"):
                continue
            if (
                account_name in child.name
                or _parse_email_from_dir_name(child.name) == account_name
            ):
                return child
        raise DataNotFoundError(
            f"EMLX fallback: 找不到账号 {account_name!r} 的目录",
        )

    def _list_messages_emlx(self, filt: ListFilter) -> list[Message]:
        """扫账号目录下指定 folder (默认 INBOX), 解 .emlx 文件头."""
        accounts = self._list_accounts_emlx()
        if filt.account:
            account = next(
                (a for a in accounts if a.address == filt.account or a.name == filt.account),
                None,
            )
            if account is None:
                raise DataNotFoundError(
                    f"EMLX fallback: 账号 {filt.account!r} 不在",
                )
        else:
            account = next((a for a in accounts if a.is_default), accounts[0])
        account_dir = self._account_dir_emlx(account.name)
        emlx_files = _find_emlx_files(account_dir, folder=filt.folder)
        # 按修改时间倒序 (最近的在前), 然后 limit
        emlx_files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        result: list[Message] = []
        for emlx_path in emlx_files:
            if len(result) >= filt.limit:
                break
            try:
                msg = _parse_emlx_summary(emlx_path, account.name, filt.folder)
            except Exception as e:  # noqa: BLE001
                logger.debug("跳过损坏的 emlx %s: %s", emlx_path, e)
                continue
            # Python 端 filter (跟 AS 路径同套规则)
            if filt.since and msg.date and msg.date < filt.since:
                continue
            if filt.until and msg.date and msg.date >= filt.until:
                continue
            if (
                filt.sender_contains
                and filt.sender_contains.lower() not in msg.sender.lower()
            ):
                continue
            if (
                filt.subject_contains
                and filt.subject_contains.lower() not in msg.subject.lower()
            ):
                continue
            if filt.unread_only and msg.is_read:
                continue
            result.append(msg)
        return result

    def _read_message_emlx(self, message_id: str) -> Message:
        """ID 格式 'account|emlx:/path/to/file.emlx', 全文解析."""
        if "|emlx:" not in message_id:
            raise ValueError(
                f"_read_message_emlx: id 格式错 (应 'account|emlx:path'): {message_id!r}",
            )
        account_name, rest = message_id.split("|", 1)
        emlx_path = Path(rest[len("emlx:"):])
        if not emlx_path.exists():
            raise DataNotFoundError(f"EMLX 文件不在: {emlx_path}")
        return _parse_emlx_full(emlx_path, account_name)

    def _search_emlx(
        self,
        query: str,
        *,
        account: str | None,
        folder: str,
        limit: int,
    ) -> list[Message]:
        """全扫所有 emlx, subject/sender contains. 慢但够 fallback 用."""
        accounts = self._list_accounts_emlx()
        if account:
            target = next(
                (a for a in accounts if a.address == account or a.name == account),
                None,
            )
            if target is None:
                return []
            account_dirs = [self._account_dir_emlx(target.name)]
            account_names = [target.name]
        else:
            account_dirs = [self._account_dir_emlx(a.name) for a in accounts]
            account_names = [a.name for a in accounts]
        q_lower = query.lower()
        result: list[Message] = []
        for acc_name, acc_dir in zip(account_names, account_dirs):
            for emlx_path in _find_emlx_files(acc_dir, folder=folder):
                if len(result) >= limit:
                    return result
                try:
                    msg = _parse_emlx_summary(emlx_path, acc_name, folder)
                except Exception:
                    continue
                if (
                    q_lower in msg.subject.lower()
                    or q_lower in msg.sender.lower()
                ):
                    result.append(msg)
        return result
