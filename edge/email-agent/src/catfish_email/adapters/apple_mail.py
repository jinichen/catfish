"""Apple Mail (macOS Mail.app) · AppleScript-first adapter (BL-EMAIL-APPLEMAIL 5/17).

# 为啥选 Apple Mail 而不是 Outlook for Mac

Mail.app 是 macOS 系统自带, 跟 Exchange / IMAP / iCloud / Gmail 全兼容. 国内员工
不用买 Microsoft 365 订阅就能用. Outlook for Mac 在国内 enterprise 渗透 <20%,
Mail.app 默认装机 100%.

# 数据访问

走 **AppleScript** 主路径 (`osascript` subprocess), Mail.app AS dictionary 完整
+ Apple 维护. 比 Outlook for Mac AS 历史失修可靠.

EMLX 文件解析 fallback 留 P1 (员工不给 Automation 权限时降级). 当前 MVP 仅 AS.

# 协议: AS stdout 用 ASCII control chars 分隔

- FS (\\x1f) field separator — 邮件正文里不可能出现
- RS (\\x1e) record separator — 同上

Python `split(RS).split(FS)` 解析. Body content (含换行 / tab / 特殊字符) 经
temp 文件传 (AS 写, Python 读), 避免 escape 噩梦.

# 错误映射

- osascript exit !=0 + stderr 含 "MESSAGE_NOT_FOUND" → DataNotFoundError
- 含 "not allowed" / "not authorized" → ClientNotRunningError (Automation 权限缺)
- "Application isn't running" / "-600" → ClientNotRunningError (Mail 没开)
- timeout → EmailAdapterError
- 其他 → EmailAdapterError + stderr 前 200 字

# 红线

create_draft 用 AS `make new outgoing message`, **不直接 send**. 草稿放 Drafts
mailbox, 员工开 Mail.app 点 Send (0.5s 认知 checkpoint, 跟 DESIGN.md 1.3 一致).
"""
from __future__ import annotations

import email
import email.parser
import email.policy
import logging
import os
import time  # P3.5.57 Phase 3 (6/22 鸿波 catch send 失败): create→send race window 缓解
import plistlib
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Sequence

from ..html_strip import strip_html  # P3.3.60 (6/12 鸿波): HTML-only fallback
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

logger = logging.getLogger("catfish_email.adapters.apple_mail")

# 8/13: osascript 执行层 + stdout 解析抽到 apple_mail_osascript.py (265 行)。
# 判据是"碰不碰 self" —— 那些函数一个都不碰, 属于协议层不属于 adapter。
# 重新 import 回本模块的命名空间: 类方法按 apple_mail 的 globals 查名字, 测试里
# monkeypatch(am, ...) 才继续有效 (跟下面 apple_mail_emlx 那批同一个套路)。
from .apple_mail_osascript import (  # noqa: F401
    FS,
    RS,
    _escape_as_string,
    _is_mail_running,
    _parse_records,
    _parse_thread_headers,
    _read_thread_headers_from_source_file,
    _resolve_osascript_timeout,
    _run_osascript,
    _OSASCRIPT_TIMEOUT_SECS,
)



# ── AppleScript templates (字符串模板, 用 .replace 注入) ──────────



# 5/20 BL-AM-SPLIT: 8 个 _AS_* osascript templates 抽到 apple_mail_scripts.py
from .apple_mail_scripts import (
    _AS_CREATE_DRAFT,
    _AS_DELETE_MESSAGE,
    _AS_GET_MESSAGE,
    _AS_LIST_ACCOUNTS,
    _AS_LIST_MESSAGES,
    _AS_MARK_READ,
    _AS_PING,
    _AS_CHECK_NEW_MAIL,
    _AS_SEARCH,
    _AS_SEND_MESSAGE,
)




# 5/20 BL-AM-SPLIT: _parse_applescript_date 移到 apple_mail_emlx.py
# (EMLX 跟 AS 路径都用, 放 emlx 避免循环 import)





# 5/20 BL-AM-SPLIT: EMLX file 处理抽到 apple_mail_emlx.py (240 行)
from .apple_mail_emlx import (  # noqa: F401
    _detect_mail_data_dir,
    _emlx_is_read,
    _extract_attachments_from_source_file,  # P3.5.100 (6/24): 治附件看不到
    _extract_html_from_source_file,
    _find_emlx_files,
    _parse_applescript_date,
    _parse_email_from_dir_name,
    _parse_emlx_full,
    _parse_emlx_summary,
    _read_emlx_raw,
    _safe_header,
    _save_attachment_payload_from_source,  # P3.5.103 (6/24): 治附件不能点
)

# 8/13: EMLX 只读兜底路径 (7 个方法, 235 行) 抽到 apple_mail_emlx_path.py。
# 用 mixin 是为了让方法体逐字节不变 —— 搬运的正确性能机械验证。MRO 里放在
# EmailAdapter 前面: 兜底方法是本类的实现细节, 不该被基类的同名默认值盖掉。
from .apple_mail_emlx_path import EmlxFallbackMixin


class AppleMailAdapter(EmlxFallbackMixin, EmailAdapter):
    """Apple Mail.app AppleScript-first adapter + EMLX fallback (只读).

    BL-EMAIL-APPLEMAIL-FULL (5/18):
      没拿到 Automation 权限 / Mail 没开 → 自动降级 EMLX 文件解析模式 (只读).
      `supports_drafts` 切 False, create_draft 抛 NotSupportedError.
    """

    name = "apple_mail"
    supports_drafts = True  # AS 支持 make new outgoing message (需 Automation 权限)

    def __init__(self) -> None:
        # __init__ 不主动 ping — 让 list_accounts 第一次调时再触发, 避免 import 时
        # 就弹 Automation 权限窗.
        self._use_emlx_fallback = False
        self._emlx_mail_dir: Path | None = None  # lazy: 第一次 fallback 时探测

    # ── 公共接口 ──

    def list_accounts(self) -> list[Account]:
        if self._use_emlx_fallback:
            return self._list_accounts_emlx()
        if not _is_mail_running():
            # AS 不行 → 试 EMLX
            if self._enable_emlx_fallback_if_available():
                return self._list_accounts_emlx()
            raise ClientNotRunningError(
                "Mail.app 没在跑且没本地 EMLX 缓存. 先打开 Mail 再调.",
            )
        try:
            out = _run_osascript(_AS_LIST_ACCOUNTS)
        except ClientNotRunningError:
            if self._enable_emlx_fallback_if_available():
                logger.info("apple_mail: AS 不可用, 切 EMLX 只读 fallback")
                return self._list_accounts_emlx()
            raise
        records = _parse_records(out, n_fields=3)
        if not records:
            raise DataNotFoundError(
                "Mail.app 里没配过任何邮箱账号. 员工先在 Mail 里加邮箱.",
            )
        return [
            Account(name=r[0], address=r[1], is_default=(r[2] == "1"))
            for r in records
        ]

    def list_messages(self, filt: ListFilter) -> list[Message]:
        if self._use_emlx_fallback:
            return self._list_messages_emlx(filt)
        try:
            return self._list_messages_as(filt)
        except ClientNotRunningError:
            if self._enable_emlx_fallback_if_available():
                return self._list_messages_emlx(filt)
            raise

    def _list_messages_as(self, filt: ListFilter) -> list[Message]:
        account_name = self._resolve_account_name(filt.account)
        script = (
            _AS_LIST_MESSAGES
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{FOLDER}", _escape_as_string(filt.folder))
            .replace("{LIMIT}", str(max(1, min(500, filt.limit))))
            .replace("{UNREAD_ONLY}", "true" if filt.unread_only else "false")
        )
        out = _run_osascript(script)
        # P3.5.58: AS list 升 6→8 字段 (加 rfcMsgId + rawHeaders) 给 thread 检测.
        # 老 Mail.app 版本不暴露 `all headers` 时 rawHdrs 为空, _parse_thread_headers
        # 返 None, 算法 fallback 用 rfcMsgId-only (能算 reply chain 但不能算 References).
        records = _parse_records(out, n_fields=8)
        # since/until/sender_contains/subject_contains 用 Python 后过滤
        # (AS 里塞复杂 where 太脆 — `messages whose ... and ... and ...` 性能差 + locale 坑多)
        result: list[Message] = []
        for r in records:
            msg_id, subj, sndr, dt_str, read_st, folder, rfc_msg_id, raw_hdrs = r
            date_iso = _parse_applescript_date(dt_str)
            if filt.since and date_iso and date_iso < filt.since:
                continue
            if filt.until and date_iso and date_iso >= filt.until:
                continue
            if (
                filt.sender_contains
                and filt.sender_contains.lower() not in sndr.lower()
            ):
                continue
            if (
                filt.subject_contains
                and filt.subject_contains.lower() not in subj.lower()
            ):
                continue
            # P3.5.58: parse thread 三件套. 优先 raw headers (含 Message-ID/
            # In-Reply-To/References), 缺时 fallback rfcMsgId-only.
            parsed_mid, parsed_in_reply, parsed_refs = _parse_thread_headers(raw_hdrs)
            final_msg_id = parsed_mid or (rfc_msg_id.strip() if rfc_msg_id else None)
            result.append(
                Message(
                    id=self._pack_id(account_name, msg_id),
                    account=account_name,
                    folder=folder,
                    subject=subj,
                    sender=sndr,
                    date=date_iso,
                    is_read=(read_st == "1"),
                    body_text="",  # list 场景不带 body
                    message_id=final_msg_id,
                    in_reply_to=parsed_in_reply,
                    references=parsed_refs,
                ),
            )
        return result

    def check_new_mail(self, *, account: str | None = None) -> None:
        """P3.5.204.c (7/9 鸿波): Apple Mail 触发立即从服务器 fetch new mail.

        AppleScript `check for new mail` 让 Mail 立即去 IMAP/POP 服务器拉一次.
        比等 Mail 定时同步 (5-15 min) 快. account 空 = 全账号同步; 指定就单账号.
        """
        if not _is_mail_running():
            raise ClientNotRunningError(
                "Apple Mail 没跑; 无法触发 check for new mail. "
                "员工需要先启动 Mail 或让 Companion 里的 Mail 存在."
            )
        acct = self._resolve_account_name(account) if account else ""
        script = _AS_CHECK_NEW_MAIL.replace("{ACCOUNT}", _escape_as_string(acct))
        try:
            _run_osascript(script, timeout=15)
        except EmailAdapterError as e:
            raise EmailAdapterError(
                f"Apple Mail check for new mail 失败: {e}"
            ) from e

    def read_message(self, message_id: str) -> Message:
        # BL-EMAIL-APPLEMAIL-FULL (5/18): EMLX id 优先走文件解析路径
        if message_id.startswith("emlx:") or "|emlx:" in message_id:
            return self._read_message_emlx(message_id)
        if self._use_emlx_fallback:
            return self._read_message_emlx(message_id)

        try:
            return self._read_message_as(message_id)
        except ClientNotRunningError:
            # AS 路径不可用 → 切 EMLX
            if self._enable_emlx_fallback_if_available():
                return self._read_message_emlx(message_id)
            raise

    def _read_message_as(self, message_id: str) -> Message:
        """AS 主路径: AS 写 body + source RFC822 到 2 个 tmp 文件, Python 解析."""
        account_name, msg_id = self._unpack_id(message_id)
        # 2 个 tmp 文件: body (纯文本) + source (完整 RFC822, 含 HTML part)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            body_path = tf.name
        with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tf:
            source_path = tf.name
        try:
            script = (
                _AS_GET_MESSAGE
                .replace("{ACCOUNT}", _escape_as_string(account_name))
                # 5/18 BL-EMAIL-APPLEMAIL-READ-ID-STR: msg_id 总作字符串塞 AS,
                # 不假设纯数字 (新版 Mail.app id 可能含 - / UUID 字母). escape 防 `"`.
                .replace("{MSG_ID}", _escape_as_string(msg_id))
                .replace("{BODY_PATH}", body_path)
                .replace("{SOURCE_PATH}", source_path)
            )
            out = _run_osascript(script)
            fields = out.split(FS)
            if len(fields) < 6:
                raise EmailAdapterError(
                    f"read_message: AS 返字段不全 ({len(fields)}/6): {out[:100]}",
                )
            subj, sndr, dt_str, to_str, cc_str, folder = fields[:6]
            body_text = ""
            try:
                with open(body_path, encoding="utf-8") as f:
                    body_text = f.read()
            except OSError as e:
                logger.warning("body tmp 文件读失败: %s", e)
            # BL-EMAIL-APPLEMAIL-FULL (5/18): 从 source RFC822 抽 body_html
            body_html = _extract_html_from_source_file(source_path)
            # P3.3.60 (6/12 鸿波): HTML-only 邮件 (HeyGen newsletter / Google Calendar
            # invite 等) AS 返 body 为空但 RFC822 含 body_html. fallback strip HTML
            # 让 detail pane 不再显 (无正文).
            if not body_text.strip() and body_html:
                body_text = strip_html(body_html)
                logger.debug(
                    "apple_mail read_message: body_text 空, 从 body_html strip 出 %d 字 fallback",
                    len(body_text),
                )
            # P3.5.58 (6/22 鸿波 catch): 从 source RFC822 parse thread 三件套
            # (Message-ID / In-Reply-To / References) 给前端 isReplied 算法用
            rfc_msg_id, in_reply_to, references = (
                _read_thread_headers_from_source_file(source_path)
            )
            # P3.5.100 (6/24 鸿波 catch '附件看不到'): 跟 thread headers 同套路,
            # 从 source RFC822 抽附件元 (filename / size / content_type).
            # 老 AS 路径 / EMLX 路径都不填这字段 → 前端永远 has_attachments=False
            # → DetailPane.tsx:560 渲染条件不满足 → UI 永远不显附件 row.
            attachments = _extract_attachments_from_source_file(source_path)
            return Message(
                id=message_id,
                account=account_name,
                folder=folder.strip(),
                subject=subj,
                sender=sndr,
                recipients=tuple(
                    a.strip() for a in to_str.split(",") if a.strip()
                ),
                cc=tuple(a.strip() for a in cc_str.split(",") if a.strip()),
                date=_parse_applescript_date(dt_str),
                has_attachments=len(attachments) > 0,
                attachments=tuple(attachments),
                body_text=body_text,
                body_html=body_html,
                message_id=rfc_msg_id,
                in_reply_to=in_reply_to,
                references=references,
            )
        finally:
            for p in (body_path, source_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    # ── P3.5.103 (6/24 鸿波): 附件导出能点 ──
    #
    # AS 主路径: 重新 dump RFC822 source (临时), walk MIME 找匹配 filename
    # 的 attachment part, decode payload 写 tmp 文件返 Path. 跟
    # _read_message_as 同模式 (AS 必须 dump 一次, 因为 source_path 是临时
    # 不能 cache — Companion 不写邮件内容到长期路径, BL-CENTRAL-EDGE-BOUNDARY).
    # EMLX fallback: 直接读 .emlx 文件本身就是 RFC822, 不需要 AS.

    def export_attachment(self, message_id: str, filename: str) -> Path:
        if message_id.startswith("emlx:") or "|emlx:" in message_id:
            return self._export_attachment_emlx(message_id, filename)
        if self._use_emlx_fallback:
            return self._export_attachment_emlx(message_id, filename)
        try:
            return self._export_attachment_as(message_id, filename)
        except ClientNotRunningError:
            if self._enable_emlx_fallback_if_available():
                return self._export_attachment_emlx(message_id, filename)
            raise

    def _export_attachment_as(self, message_id: str, filename: str) -> Path:
        """AS 路径: 重 dump source RFC822, walk 找附件 part 写 tmp."""
        account_name, msg_id = self._unpack_id(message_id)
        # AS 强制要 body_path + source_path 两个文件 (脚本里都写). 我们只用 source.
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            body_path = tf.name
        with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tf:
            source_path = tf.name
        try:
            script = (
                _AS_GET_MESSAGE
                .replace("{ACCOUNT}", _escape_as_string(account_name))
                .replace("{MSG_ID}", _escape_as_string(msg_id))
                .replace("{BODY_PATH}", body_path)
                .replace("{SOURCE_PATH}", source_path)
            )
            _run_osascript(script)
            out_path = _save_attachment_payload_from_source(source_path, filename)
            if out_path is None:
                raise DataNotFoundError(
                    f"附件 {filename!r} 在邮件 {message_id!r} 里找不到 "
                    f"(MIME walk 0 命中 Content-Disposition: attachment)"
                )
            return out_path
        finally:
            for p in (body_path, source_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass


    def send_message(self, message_id: str) -> None:
        """5/18 BL-EMAIL-COMPOSE-SEND: AS `send <msg>` 真发草稿.

        红线: caller (Companion compose panel) **必须人工 confirm 才调**,
        adapter 不做"是不是人发的" 校验. EMLX fallback 模式拒.

        P3.5.57 Phase 3 (6/22 鸿波 catch "草稿不存在" send 失败 race):
        Mail.app 创草稿后 200-500ms 内可能 IMAP sync / Drafts 重新索引 / WAL
        checkpoint 延迟, 让 rowid `whose id is` 暂时找不到. create_draft 已 sleep
        200ms 等 sqlite flush, 这里再 retry 3 次 × 300ms 双保险. 仅在
        DataNotFoundError (8001 MESSAGE_NOT_FOUND) 才 retry, 别的 error
        (权限 / Mail 没开 / 账号错) 立即抛, 不浪费时间.
        """
        if self._use_emlx_fallback:
            from .base import NotSupportedError
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式不支持 send_message — "
                "Mail.app 必须开着才能发邮件"
            )

        account_name, msg_id = self._unpack_id(message_id)
        script = (
            _AS_SEND_MESSAGE
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{MSG_ID}", _escape_as_string(msg_id))
        )

        # P3.5.57 Phase 3: race retry — 仅 MESSAGE_NOT_FOUND 重试
        max_retries = 3
        retry_delay_s = 0.3
        last_not_found: DataNotFoundError | None = None
        for attempt in range(max_retries):
            try:
                out = _run_osascript(script)
                if out.strip() != "OK":
                    raise EmailAdapterError(
                        f"send_message: AS 返非 OK ({out[:120]!r})"
                    )
                return  # 成功
            except DataNotFoundError as e:
                last_not_found = e
                if attempt < max_retries - 1:
                    logger.info(
                        "send_message MESSAGE_NOT_FOUND, %dms 后重试 (%d/%d): %s",
                        int(retry_delay_s * 1000), attempt + 1, max_retries, e,
                    )
                    time.sleep(retry_delay_s)
                    continue
                break
            # 别的 error (ClientNotRunningError 权限 / EmailAdapterError 等) 不
            # retry, 直接往上抛 — 那些不是 race, retry 也救不了
        # 3 次都 MESSAGE_NOT_FOUND, 改善错误提示
        assert last_not_found is not None
        raise DataNotFoundError(
            "草稿在 Mail.app 里找不到, 已重试 3 次仍失败. "
            "可能原因: Mail.app 正在 IMAP 同步 / 草稿被你手动删了 / 该账号"
            "突然离线. 重启 Mail.app 后再试; 或先点 💾 仅保存草稿, "
            "去 Mail.app Drafts 文件夹自己发."
        ) from last_not_found

    def delete_message(self, message_id: str) -> None:
        """5/18 BL-EMAIL-DELETE: AS `delete <msg>` = 移到 Trash (软删).

        EMLX fallback 模式不支持 (直接删 emlx 文件 Mail.app 重启会重新生成,
        IMAP server 那边没改, 体验诡异). 显式拒.
        """
        if self._use_emlx_fallback:
            from .base import NotSupportedError
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式不支持 delete_message — "
                "Mail.app 必须开着才能持久化"
            )

        account_name, msg_id = self._unpack_id(message_id)
        script = (
            _AS_DELETE_MESSAGE
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{MSG_ID}", _escape_as_string(msg_id))
        )
        out = _run_osascript(script)
        if out.strip() != "OK":
            raise EmailAdapterError(
                f"delete_message: AS 返非 OK ({out[:120]!r})"
            )

    def mark_read(self, message_id: str, *, read: bool = True) -> None:
        """5/18 BL-EMAIL-MARK-READ: AS `set read status of m to true/false`.

        EMLX fallback 路径不实现 — emlx 文件状态由 Mail.app 维护, 直接改文件
        Mail.app 不刷新会出"看着改了重启又回去"假象. EMLX 模式下报 NotSupportedError.
        """
        if self._use_emlx_fallback:
            # 不啃 emlx 文件状态 (Mail.app 重启会覆盖)
            from .base import NotSupportedError
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式不支持 mark_read — "
                "Mail.app 必须开着才能持久化 read status"
            )

        account_name, msg_id = self._unpack_id(message_id)
        script = (
            _AS_MARK_READ
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{MSG_ID}", _escape_as_string(msg_id))
            # AS boolean 字面量: true / false (小写)
            .replace("{READ_FLAG}", "true" if read else "false")
        )
        out = _run_osascript(script)
        if out.strip() != "OK":
            raise EmailAdapterError(
                f"mark_read: AS 返非 OK ({out[:120]!r})"
            )

    def search(
        self,
        query: str,
        *,
        account: str | None = None,
        folder: str = "Inbox",
        limit: int = 30,
    ) -> list[Message]:
        """全文 / 字段搜索.

        P3.5.153 (6/30 鸿波 catch "为什么搜不到 chinatelecom.cn 邮件"):
        account=None 时**跨所有账号搜** (不是 fallback 到 _resolve_account_name 真
        first 一个 — 这老语义让 Apple Mail 多账号场景下永远只搜第一个 account,
        公司账号常排第 2/3 直接漏). 跟 folder="*" 对称 (跨所有 folder), 让
        LLM 在 chat 里调 catfish_email_search 不传 account 时能命中所有账号.

        account=str 路径不变 (单账号, 老调用方语义兼容).
        """
        if not query.strip():
            return []
        if self._use_emlx_fallback:
            return self._search_emlx(query, account=account, folder=folder, limit=limit)
        if account is None:
            return self._search_all_accounts(query, folder=folder, limit=limit)
        try:
            return self._search_as(query, account=account, folder=folder, limit=limit)
        except ClientNotRunningError:
            if self._enable_emlx_fallback_if_available():
                return self._search_emlx(
                    query, account=account, folder=folder, limit=limit,
                )
            raise

    def _search_all_accounts(
        self,
        query: str,
        *,
        folder: str,
        limit: int,
    ) -> list[Message]:
        """P3.5.153: 跨所有 Apple Mail 账号搜, 合并 + date 倒序 + 截 limit.

        单账号 osascript 失败 → log warning skip, 不挂全 search (跟 _cmd_search
        跨 adapter 容错思路一致 — 一个账号挂不该让其他账号的命中丢).

        优化: 直接复用 list_accounts() 拉到的 name (acc.name), 调 _do_as_search
        跳过 _search_as 内 _resolve_account_name 重复拉 accounts. 一次 search
        实际 osascript 调用 = 1 (list_accounts) + N (每账号 _AS_SEARCH).
        """
        try:
            all_accs = self.list_accounts()
        except (ClientNotRunningError, EmailAdapterError) as e:
            logger.warning("search 拉账号列表失败 (回退单账号 fallback): %s", e)
            return self._search_as(query, account=None, folder=folder, limit=limit)

        all_hits: list[Message] = []
        for acc in all_accs:
            if len(all_hits) >= limit:
                break
            remaining = limit - len(all_hits)
            try:
                hits = self._do_as_search(
                    query, account_name=acc.name, folder=folder, limit=remaining,
                )
                all_hits.extend(hits)
            except (ClientNotRunningError, EmailAdapterError) as e:
                logger.warning(
                    "search 跨账号 %s 失败 (skip): %s", acc.address, e,
                )
                continue
        # 按 date 倒序 (各账号 osascript 返序无保证)
        all_hits.sort(key=lambda m: m.date or "", reverse=True)
        return all_hits[:limit]

    def _search_as(
        self,
        query: str,
        *,
        account: str | None,
        folder: str,
        limit: int,
    ) -> list[Message]:
        account_name = self._resolve_account_name(account)
        return self._do_as_search(
            query, account_name=account_name, folder=folder, limit=limit,
        )

    def _do_as_search(
        self,
        query: str,
        *,
        account_name: str,
        folder: str,
        limit: int,
    ) -> list[Message]:
        """P3.5.153: 真正调 osascript 真核心. account_name 已是 Mail 真显示名,
        跳过 _resolve_account_name 重复 osascript. _search_all_accounts 复用
        list_accounts() 拿到的 acc.name 直调这里, 避免每个账号又拉一次 accounts.
        """
        script = (
            _AS_SEARCH
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{FOLDER}", _escape_as_string(folder))
            .replace("{QUERY}", _escape_as_string(query))
            .replace("{LIMIT}", str(max(1, min(500, limit))))
        )
        out = _run_osascript(script)
        records = _parse_records(out, n_fields=6)
        return [
            Message(
                id=self._pack_id(account_name, r[0]),
                account=account_name,
                folder=r[5],
                subject=r[1],
                sender=r[2],
                date=_parse_applescript_date(r[3]),
                is_read=(r[4] == "1"),
                body_text="",
            )
            for r in records
        ]

    def create_draft(
        self,
        *,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        in_reply_to: str | None = None,
        account: str | None = None,
    ) -> str:
        if not to:
            raise ValueError("create_draft: to 不能空")
        # EMLX fallback 模式只读 → 不支持起草
        if self._use_emlx_fallback:
            raise NotSupportedError(
                "Apple Mail EMLX fallback 模式只读, 不能写草稿. "
                "去 System Settings 给 catfish 'Mail' Automation 权限后重试.",
            )
        account_name = self._resolve_account_name(account)
        # body 写 tmp 文件传给 AS
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8",
        ) as tf:
            tf.write(body)
            body_path = tf.name
        try:
            to_str = ",".join(to)
            cc_str = ",".join(cc)
            bcc_str = ",".join(bcc)  # BL-EMAIL-APPLEMAIL-FULL (5/18): bcc 支持
            script = (
                _AS_CREATE_DRAFT
                .replace("{ACCOUNT}", _escape_as_string(account_name))
                .replace("{SUBJECT}", _escape_as_string(subject))
                .replace("{BODY_PATH}", body_path)
                .replace("{TO}", _escape_as_string(to_str))
                .replace("{CC}", _escape_as_string(cc_str))
                .replace("{BCC}", _escape_as_string(bcc_str))
            )
            out = _run_osascript(script)
            draft_id = out.strip()
            if not draft_id:
                raise EmailAdapterError("create_draft: AS 没返新草稿 id")
            # P3.5.57 Phase 3 (6/22 鸿波 catch "草稿不存在" send 失败):
            # Mail.app `make new outgoing message` 返 rowid 是当前一刻的快照,
            # 但内部 sqlite 走 WAL + IMAP sync 可能在 200-500ms 内重新分配 id.
            # 直接 Companion send 失败 "MESSAGE_NOT_FOUND". 等 200ms 让 Mail.app
            # sqlite checkpoint + Drafts 文件夹索引落定再返 id, 让 caller send 时
            # `whose id is` 还能命中. 配合 send_message retry 双保险.
            time.sleep(0.2)
            return self._pack_id(account_name, draft_id)
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass

    # ── EMLX fallback (只读) ────────────────────────────
    #
    # 触发条件: AS 不可用 (没 Automation 权限 / Mail.app 没开) 且本机有
    # ~/Library/Mail/V*/ 目录 (Mail 曾同步过本地).
    #
    # 数据布局 (macOS 14+):
    #   ~/Library/Mail/V10/
    #     ├ MailData/          ← 元数据 (账号 plist 等)
    #     ├ <UUID>-IMAP@imap.host/   ← 账号目录, name 含 email
    #     │   ├ INBOX.mbox/
    #     │   │   └ <UUID>-Data/Messages/<id>.emlx
    #     │   ├ Drafts.mbox/
    #     │   └ Sent.mbox/
    #     └ ...
    #
    # ID 格式 (EMLX 模式): "<account_name>|emlx:<emlx_file_path>"







    # ── 内部 helpers ──

    def _resolve_account_name(self, account: str | None) -> str:
        """把 account 参数 (None / email 地址 / 显示名) 解析成 Mail 里的 'name'.

        Mail.app AS 用 `account whose name of it is X` — 必须用显示名. 我们
        DESIGN.md 约定外部用 email 地址, 这里映射回显示名.
        """
        accounts = self.list_accounts()
        if account is None:
            default = next((a for a in accounts if a.is_default), accounts[0])
            return default.name
        # 先按地址匹配
        for a in accounts:
            if a.address == account:
                return a.name
        # 再按显示名匹配
        for a in accounts:
            if a.name == account:
                return a.name
        raise DataNotFoundError(
            f"Mail.app 里找不到账号 {account!r}. 现有: "
            f"{', '.join(f'{a.address} ({a.name})' for a in accounts)}",
        )

    @staticmethod
    def _pack_id(account_name: str, msg_id: str) -> str:
        """打包 Mail 里的 numeric msg id + account name 成稳定字符串 id.

        分隔用 `|`. account_name 不允许含 `|` (Mail 限制 + 显示名常理).
        5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX: 加 'apple_mail|' 前缀, 跟 Foxmail
        ('foxmail-mac|...') 同 3 段格式, _cmd_read 看前缀路由不走错 adapter.
        """
        return f"apple_mail|{account_name}|{msg_id}"

    @staticmethod
    def _unpack_id(packed: str) -> tuple[str, str]:
        """5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX: 兼容两种格式
            - 新 (3 段): 'apple_mail|account|msg_id' (5/18 起 _pack_id 用这个)
            - 老 (2 段): 'account|msg_id' (历史 list 输出的 id, 仍能 unpack)
        """
        if "|" not in packed:
            raise ValueError(
                f"Apple Mail message_id 格式错 (应 'apple_mail|account|id' 或 'account|id'): {packed!r}",
            )
        parts = packed.split("|", 2)
        if len(parts) == 3 and parts[0] == "apple_mail":
            return parts[1], parts[2]
        # 老 2 段格式: account|msg_id
        return parts[0], "|".join(parts[1:])
