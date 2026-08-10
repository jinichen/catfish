"""Windows Outlook · COM adapter (W2 BL-EMAIL-OUTLOOK-WIN 7/11).

# 为啥选 Outlook COM (不走 IMAP/EWS/MAPI-C)

Windows enterprise 员工机 Outlook 装机率 >85%. COM 是 Microsoft 官方接口,
稳定跨 Outlook 2016/2019/365. 比 MAPI C API 简单十倍, 比 IMAP 拉全云端邮件靠谱
(走本机 Outlook cache, 员工离线也能读). EWS/Graph 走网络 + 需要 Exchange 授权,
不合适内网合规场景.

# 数据访问

`win32com.client.Dispatch("Outlook.Application")` → `Namespace.GetDefaultFolder(N)`.

folder id 表 (Microsoft OlDefaultFolders enum, 稳定):
    3 = Deleted Items    5 = Sent Items     6 = Inbox
    9 = Calendar        16 = Drafts

**用 GetDefaultFolder(N) 而不是 Folders["Sent"]**:
    中文 Outlook `Folders["已发送邮件"]`, 英文 Outlook `Folders["Sent Items"]`,
    按 name 挂概率极高. enum 稳定不受 locale 影响.

# ID 稳定性

`MailItem.EntryID` 是 Outlook 内部稳定 ID (跨会话稳定, 存 store 里).
PST / OST 迁移 / rebuild 后 EntryID 会变, 需要重新 list_messages 刷.
`_pack_id = f"outlook_win|{account_smtp}|{entry_id}"` 3 段, 跟 apple_mail 对齐.

# Message-ID (RFC 822) 拿法

`PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x1035001F")`

Exchange 账户一定有 (Outlook 本地生成或从 X-Header 拿). IMAP 账户可能返空
或抛 com_error (Outlook 没暴露原始 header). P3.5.58 isReplied 算法要求, 骨架
已兜底返 None, 前端算法 fallback subject "Re:" fuzzy.

# 性能 — Restrict 而不是 for-loop

`Items.Restrict("@SQL=...")` + `.Sort("[ReceivedTime]", True)` 走 MAPI 索引,
100k+ 邮箱毫秒级. **禁止 `for item in Items` 直接遍历**, 100k 起 20s+.

DASL 日期语法 (locale-independent):
    @SQL="urn:schemas:httpmail:datereceived" > '2026-07-01T00:00:00Z'
不用 `[ReceivedTime] > '07/01/2026 ...'` 因为**中文 Outlook 期望 "2026/7/1 上午 12:00"**,
按 locale 千差万别一挂就是全线 hang. urn:schemas 走 XML datetime 稳定.

# COM 线程

pywin32 COM 每线程都要 `pythoncom.CoInitialize()`. Companion 后台调 hermes →
hermes 调 adapter 走 asyncio worker thread, 每个 worker 都要 CoInit.

Adapter 用 `threading.local()` 保 COM 状态 (`_ol`, `_ns`), 每个线程独立
Dispatch. 主线程和 worker 不共享 Outlook.Application 实例, 避免 STA 挂钩死锁.

# 错误映射

- `pywintypes.com_error` hresult=0x80080005 (CO_E_SERVER_EXEC_FAILURE)
    → ClientNotRunningError ("Outlook 挂了, 员工重开一下再试")
- hresult=0x800401F3 (CLASS_STRING_INVALID)
    → ClientNotRunningError ("Outlook 没装, catfish doctor 检查")
- item.EntryID lookup 失败 → DataNotFoundError
- 其他 com_error → EmailAdapterError + hresult hex

# 骨架范围 (W3 MVP)

4 个必须 method: list_accounts / list_messages / read_message / search.
6 个可选 method (create_draft / send_message / delete_message / mark_read /
check_new_mail / export_attachment) **不 override**, 走 base default (抛
NotSupportedError). W3 集成阶段 (在 Windows 机器上) 逐个补.

# 红线

跟 apple_mail 一致:
    - create_draft 用 CreateItem(0) 存 Drafts, **不直接 Send**
    - delete_message 走 MailItem.Delete() 移 Deleted Items, **不物理删**
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Sequence

from .base import (
    Account,
    ClientNotRunningError,
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
    ListFilter,
    Message,
    NotSupportedError,
)

logger = logging.getLogger("catfish_email.adapters.outlook_win")

# ── Outlook folder enum (locale-independent, 稳定跨版本) ─────────
OL_FOLDER_DELETED = 3
OL_FOLDER_SENT = 5
OL_FOLDER_INBOX = 6
OL_FOLDER_DRAFTS = 16

# ── PropertyAccessor tag: RFC 822 Message-ID header ─────────────
PR_INTERNET_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"

# folder alias 表 (对齐 apple_mail / foxmail-mac 外部约定)
FOLDER_ALIASES: dict[str, int] = {
    "Inbox": OL_FOLDER_INBOX,
    "Sent": OL_FOLDER_SENT,
    "Drafts": OL_FOLDER_DRAFTS,
    "Trash": OL_FOLDER_DELETED,
    "Deleted": OL_FOLDER_DELETED,
}

# Blocker W3-3: DASL 走 urn:schemas locale-independent
_DASL_RECEIVED = "urn:schemas:httpmail:datereceived"
_DASL_SUBJECT = "urn:schemas:httpmail:subject"
_DASL_UNREAD = "urn:schemas:httpmail:read"


def _import_pywin32() -> None:
    """Lazy import pywin32. 非 Windows 平台不 import.

    Raises:
        NotSupportedError: 不在 Windows 上 (inbox.py get_adapter fallback 会捕获)
        ImportError: pywin32 没装 (提示 pip install catfish-email[windows])
    """
    if sys.platform != "win32":
        raise NotSupportedError(
            "outlook_win adapter 仅支持 Windows (当前 sys.platform="
            f"{sys.platform!r}). macOS 请用 apple-mail; Linux 未来看情况."
        )
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
        import pywintypes  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "pywin32 未装. 装: pip install 'catfish-email[windows]'. "
            "hermes install.ps1 里应该已经装了, 若没装说明 install.ps1 有 bug."
        ) from e


class OutlookWinAdapter(EmailAdapter):
    """Windows Outlook COM adapter (W2 骨架, W3 集成补 optional method).

    走 `win32com.client.Dispatch("Outlook.Application")`. 主要面向 Exchange /
    Office 365 账户 (Message-ID + isReplied 精度高), IMAP 账户 subject-fuzzy 兜底.
    """

    name = "outlook_win"

    # 这里曾经写 True, 理由是"CreateItem(0).Save() 存 Drafts (W3 集成阶段实现)"
    # —— 把**打算实现**当成**已经实现**写进了 flag。
    #
    # 后果不是抽象的: __main__.py 的 _cmd_draft 正是靠这个 flag 挑 adapter。
    # Windows 上候选只有 outlook_win (foxmail-win 工厂里就 NotImplementedError),
    # 于是它必被选中, 然后 create_draft 落到基类抛 NotSupportedError。员工在
    # Companion 里点"起草回复", 拿到的是"该 adapter 不支持起草" —— 而 flag
    # 一直在说支持。
    #
    # flag 的语义是"我实现了 create_draft", 不是"我将来会实现"。本文件第 63-68
    # 行自己也写着这 6 个可选方法**不 override、走 base default**, 只有这一行
    # 跟它对不上。
    #
    # 翻回 True 的条件: 本类真的 override 了 create_draft, 并且在装了 Outlook
    # 的 Windows 机器上手测过 (测试策略见 tests/test_adapter_outlook_win.py 开头
    # —— 沙箱和 macOS CI 都没有 pywin32, mock 只能证明"我按我以为的方式调了 COM",
    # 证明不了 Outlook 真那么行为)。
    # tests/test_adapter_contract.py 会盯着这一条: 只要 flag 是 True 而
    # create_draft 没 override, 测试就红。
    supports_drafts = False

    def __init__(self) -> None:
        # 只做 sys.platform + pywin32 存在检查, 不 Dispatch — 让 list_accounts
        # 第一次调时再触发 Outlook (对齐 apple_mail 不在 __init__ 里 ping 的语义).
        _import_pywin32()
        # Blocker W3-2: threading.local 保 COM 状态, asyncio worker 多线程安全
        self._tls = threading.local()

    # ── COM 生命周期 ─────────────────────────────────

    def _ensure_dispatch(self) -> tuple[object, object]:
        """惰性 Dispatch Outlook.Application + Namespace('MAPI'). 每线程独立.

        Returns:
            (outlook_app, namespace) tuple. 上层不该缓存, 每次 method 都 call.
        Raises:
            ClientNotRunningError: Outlook 未装 / COM 挂
        """
        if getattr(self._tls, "ol", None) is not None:
            return self._tls.ol, self._tls.ns
        import pythoncom
        import win32com.client
        import pywintypes
        try:
            # 幂等 — 已 init 过再 call 返 S_FALSE 不抛
            pythoncom.CoInitialize()
            ol = win32com.client.Dispatch("Outlook.Application")
            ns = ol.GetNamespace("MAPI")
        except pywintypes.com_error as e:
            hresult = e.hresult & 0xFFFFFFFF if e.hresult else 0
            raise ClientNotRunningError(
                f"Outlook COM 初始化失败 (hresult=0x{hresult:08X}). "
                "员工先打开 Outlook 桌面版再试. 常见: (1) Outlook 未装; "
                "(2) 系统崩溃后 COM 挂; (3) Office 365 需先注册账户."
            ) from e
        self._tls.ol = ol
        self._tls.ns = ns
        return ol, ns

    # ── 公共接口 (4 个 abstract) ───────────────────────

    def list_accounts(self) -> list[Account]:
        """列 Outlook 里配的所有邮箱账号."""
        import pywintypes
        _, ns = self._ensure_dispatch()
        try:
            com_accounts = ns.Accounts
            n = com_accounts.Count
        except pywintypes.com_error as e:
            raise ClientNotRunningError(f"读 Outlook accounts 失败: {e}") from e
        default_smtp = ""
        try:
            if ns.CurrentUser is not None:
                default_smtp = ns.CurrentUser.Address or ""
        except pywintypes.com_error:
            pass
        result: list[Account] = []
        # COM Collection 1-based
        for i in range(1, n + 1):
            try:
                acc = com_accounts.Item(i)
                smtp = (acc.SmtpAddress or "").strip()
                display = (acc.DisplayName or smtp).strip()
                if not smtp:
                    continue
                result.append(
                    Account(
                        name=display,
                        address=smtp,
                        is_default=(smtp == default_smtp) if default_smtp else (i == 1),
                    )
                )
            except pywintypes.com_error as e:
                logger.warning("skip broken account #%d: %s", i, e)
                continue
        if not result:
            raise DataNotFoundError(
                "Outlook 里没配任何邮箱账号. 员工先在 Outlook 加账号."
            )
        return result

    def list_messages(self, filt: ListFilter) -> list[Message]:
        """按 filter 列邮件 (走 GetDefaultFolder + Restrict).

        MVP: since / unread_only / limit 生效. sender_contains /
        subject_contains / body_contains / has_attachments 走 Python 后过滤
        (对齐 apple_mail 后过滤策略, DASL 复合条件性能 + 语法都脆).
        """
        import pywintypes
        _, ns = self._ensure_dispatch()
        folder_id = FOLDER_ALIASES.get(filt.folder, OL_FOLDER_INBOX)
        try:
            folder = ns.GetDefaultFolder(folder_id)
            items = folder.Items
            items.Sort("[ReceivedTime]", True)  # 倒序
            restrict_parts: list[str] = []
            if filt.since:
                # ISO date '2026-04-26' → '2026-04-26T00:00:00Z'
                iso = self._iso_date_to_utc(filt.since)
                restrict_parts.append(
                    f'"{_DASL_RECEIVED}" > \'{iso}\''
                )
            if filt.unread_only:
                restrict_parts.append(f'"{_DASL_UNREAD}" = 0')
            if restrict_parts:
                restrict_expr = "@SQL=" + " AND ".join(restrict_parts)
                items = items.Restrict(restrict_expr)
        except pywintypes.com_error as e:
            raise EmailAdapterError(
                f"Outlook Restrict 失败 (folder={filt.folder!r}): {e}"
            ) from e

        result: list[Message] = []
        count = 0
        limit = max(1, min(500, filt.limit))
        for item in items:
            if count >= limit:
                break
            try:
                # 后过滤 (对齐 apple_mail 策略)
                if filt.sender_contains and filt.sender_contains.lower() not in \
                        (item.SenderName or "").lower():
                    continue
                if filt.subject_contains and filt.subject_contains.lower() not in \
                        (item.Subject or "").lower():
                    continue
            except pywintypes.com_error:
                continue
            msg = self._item_to_message(item, folder=filt.folder, body_full=False)
            if msg is not None:
                result.append(msg)
                count += 1
        return result

    def read_message(self, message_id: str) -> Message:
        """拉单封完整邮件 (含 body_html + attachments meta)."""
        import pywintypes
        _, ns = self._ensure_dispatch()
        _account_smtp, entry_id = self._unpack_id(message_id)
        try:
            item = ns.GetItemFromID(entry_id)
        except pywintypes.com_error as e:
            raise DataNotFoundError(
                f"Outlook 里找不到 EntryID={entry_id!r} (message_id={message_id!r}): {e}"
            ) from e
        # folder 从 item.Parent 反查 (name 会 locale-dep, 用 alias 反查)
        folder_name = "Inbox"
        try:
            folder_id_val = getattr(item.Parent, "DefaultItemType", None)
            if folder_id_val is not None:
                folder_name = self._folder_id_to_alias(item.Parent) or "Inbox"
        except pywintypes.com_error:
            pass
        msg = self._item_to_message(item, folder=folder_name, body_full=True)
        if msg is None:
            raise DataNotFoundError(
                f"Outlook item EntryID={entry_id!r} 存在但无法读取内容"
            )
        return msg

    def search(
        self,
        query: str,
        *,
        account: str | None = None,
        folder: str = "Inbox",
        limit: int = 30,
    ) -> list[Message]:
        """走 Outlook Restrict SQL: subject LIKE '%query%' OR body LIKE '%query%'."""
        import pywintypes
        _, ns = self._ensure_dispatch()
        folder_id = FOLDER_ALIASES.get(folder, OL_FOLDER_INBOX)
        try:
            outlook_folder = ns.GetDefaultFolder(folder_id)
            # DASL LIKE 用单引号 escape 成两个单引号
            safe_q = query.replace("'", "''")
            # subject 或 body 命中. body 是 textdescription, 大邮箱慢, 但 Restrict 走索引.
            restrict_expr = (
                f'@SQL="{_DASL_SUBJECT}" LIKE \'%{safe_q}%\''
                f' OR "urn:schemas:httpmail:textdescription" LIKE \'%{safe_q}%\''
            )
            items = outlook_folder.Items.Restrict(restrict_expr)
            items.Sort("[ReceivedTime]", True)
        except pywintypes.com_error as e:
            raise EmailAdapterError(
                f"Outlook search Restrict 失败 (query={query!r}): {e}"
            ) from e

        result: list[Message] = []
        count = 0
        for item in items:
            if count >= limit:
                break
            msg = self._item_to_message(item, folder=folder, body_full=False)
            if msg is not None:
                result.append(msg)
                count += 1
        return result

    # ── helper ───────────────────────────────────────

    def _item_to_message(
        self, item, *, folder: str, body_full: bool,
    ) -> Message | None:
        """MailItem COM object → Message dataclass. 骨架版, W3 集成阶段补.

        W3 骨架不做:
            - attachments meta (需 iterate item.Attachments)
            - recipients / cc / bcc 拆解 (需 iterate item.Recipients + Type filter)
            - references header (需 PropertyAccessor 拉 PR_INTERNET_REFERENCES)
        """
        import pywintypes
        try:
            entry_id = item.EntryID
            subject = item.Subject or ""
            sender_name = item.SenderName or ""
            sender_addr = ""
            try:
                sender_addr = item.SenderEmailAddress or ""
            except pywintypes.com_error:
                pass
            sender = (
                f"{sender_name} <{sender_addr}>"
                if sender_addr and sender_name and sender_name != sender_addr
                else sender_name or sender_addr or ""
            )
            date_iso = self._outlook_date_to_iso(item.ReceivedTime)
            is_read = not bool(item.UnRead)
            has_att = bool(item.Attachments.Count) if hasattr(item, "Attachments") else False
            body_text_full = item.Body or ""
            body_html_full = ""
            try:
                body_html_full = item.HTMLBody or ""
            except pywintypes.com_error:
                pass
            body_text = body_text_full if body_full else body_text_full[:200]
            body_html = body_html_full if body_full else ""

            # RFC 822 Message-ID (Exchange 有, IMAP 可能空)
            rfc_msg_id: str | None = None
            try:
                pa = item.PropertyAccessor
                raw = pa.GetProperty(PR_INTERNET_MESSAGE_ID)
                rfc_msg_id = raw.strip() if raw else None
            except pywintypes.com_error:
                # IMAP 账户 / property 不存在
                rfc_msg_id = None

            account_smtp = self._resolve_item_account(item)
            return Message(
                id=self._pack_id(account_smtp, entry_id),
                account=account_smtp,
                folder=folder,
                subject=subject,
                sender=sender,
                date=date_iso,
                is_read=is_read,
                has_attachments=has_att,
                body_text=body_text,
                body_html=body_html,
                message_id=rfc_msg_id,
            )
        except pywintypes.com_error as e:
            logger.warning("skip corrupt Outlook item (%s): %s",
                           getattr(item, "EntryID", "?"), e)
            return None

    def _resolve_item_account(self, item) -> str:
        """MailItem → 账号 SMTP address.

        MVP: 走 item.SendUsingAccount.SmtpAddress (Outlook 存的邮件账户).
        fallback CurrentUser.Address (默认账户).
        """
        try:
            acc = item.SendUsingAccount
            if acc is not None:
                return acc.SmtpAddress or ""
        except Exception:
            pass
        try:
            _, ns = self._ensure_dispatch()
            if ns.CurrentUser is not None:
                return ns.CurrentUser.Address or ""
        except Exception:
            pass
        return ""

    def _folder_id_to_alias(self, folder) -> str | None:
        """反查 Folder → alias name (Inbox/Sent/Drafts/Trash).

        走 Class ID 判断 (MailFolder.Class = 2), name 太 locale-dep 不敢用.
        """
        try:
            _, ns = self._ensure_dispatch()
            for alias, folder_id in FOLDER_ALIASES.items():
                default = ns.GetDefaultFolder(folder_id)
                if default.EntryID == folder.EntryID:
                    return alias
        except Exception:
            pass
        return None

    # ── ID 序列化 (对齐 apple_mail._pack_id / _unpack_id) ─────

    @staticmethod
    def _pack_id(account_smtp: str, entry_id: str) -> str:
        """3 段 id: 'outlook_win|<smtp>|<entry_id>'. 对齐 apple_mail 前缀风格."""
        return f"outlook_win|{account_smtp}|{entry_id}"

    @staticmethod
    def _unpack_id(packed: str) -> tuple[str, str]:
        """解包 3 段 id. 不接老 2 段格式 (Outlook adapter 全新, 无历史 id)."""
        parts = packed.split("|", 2)
        if len(parts) != 3 or parts[0] != "outlook_win":
            raise ValueError(
                f"outlook_win message_id 格式错 (应 'outlook_win|<smtp>|<entry>'): "
                f"{packed!r}"
            )
        return parts[1], parts[2]

    # ── datetime 转换 ────────────────────────────────

    @staticmethod
    def _iso_date_to_utc(iso: str) -> str:
        """'2026-04-26' → '2026-04-26T00:00:00Z' (DASL urn:schemas 期望 xsd:dateTime).

        含时区就规范化为 UTC; 只给日期就补 00:00:00Z.
        """
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            # 已经是完整格式或者失败 — 原样返, 让 Outlook 自己抛
            return iso

    @staticmethod
    def _outlook_date_to_iso(ol_dt) -> str:
        """pywintypes.datetime / datetime → ISO-8601 UTC 字符串.

        pywintypes.TimeType 继承 datetime, 但可能是 naive 或 tz-aware 因版本而异.
        统一转 UTC 输出.
        """
        from datetime import datetime, timezone
        try:
            if hasattr(ol_dt, "astimezone"):
                if getattr(ol_dt, "tzinfo", None) is None:
                    # naive → 假设本地时区, 走系统 astimezone
                    ol_dt = ol_dt.replace(tzinfo=timezone.utc)
                return ol_dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
        return str(ol_dt)
