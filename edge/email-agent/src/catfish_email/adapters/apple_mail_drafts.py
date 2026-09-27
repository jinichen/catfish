"""Apple Mail · 草稿: 存进草稿箱、改草稿、发草稿 (9/27)。

从 apple_mail.py 挪出来 (那边已经 795 行), AS 模板和它们的 Python 逻辑放一起。
函数都收一个 `run` —— 调用方传 apple_mail 模块里的 `_run_osascript`, 测试
monkeypatch(am, "_run_osascript") 照样生效。

# 以前的问题 (都在这台 Mac 的对话记录里有实据)

  · `make new outgoing message` 返回的是**撰写窗口**的编号 (0、1、2 ……),
    不是草稿箱里那封信的编号。拿它去读 / 改 / 跳转都报「找不到这条消息」
    (9/11、9/16、9/20 小鲶读回 `apple_mail|Google|9` 等全部失败)。
  · 草稿根本没指定账号: 脚本收了 {ACCOUNT} 却没用上, 返回的 id 又把默认账号名
    (Google) 拼进去 —— 小鲶记下的原话是「draft_id 报 apple_mail|Google|N 是假
    成功, 草稿实际可能不在目标账号」。
  · 草稿靠 Mail 自己定时存盘 (visible:true 开着撰写窗口), 存没存、存到哪不确定。

# 现在的做法

存: 撰写窗口不显示 (visible:false), `save newMsg in <该账号的草稿箱>` 显式存进去
—— 这是小鲶 8/31 反复试出来、9/16 起一直在用的唯一可靠写法 (把 account 写进
properties 报 -10024, 事后改 account 报 -10006)。存之前记下草稿箱里已有的 id,
存之后找新多出来的那封, 返回**它的**编号和它所在的账号。

发: 草稿箱里的信在 Mail 的脚本接口里是 message, `send` 只收 outgoing message
(撰写中的信)。所以
  1. 我们存的草稿, 撰写窗口还在 Mail 里 → 就发那个窗口 (等同于在窗口里点发送)。
     窗口编号 Mail 重启会重排, 发之前核对主题一致, 对不上就不用它。
  2. 否则照草稿内容重新拼一封 (纯文本正文 + 原收件人 + 原发件人) 发出, 再删草稿。
     带附件的草稿不走这条 —— 附件带不过去, 宁可让员工去「邮件」里发。
     发件人设不上 (Mail 不认) 就不发, 绝不从别的账号发出去。

改: 新版本存好、确认拿到编号之后, 才去掉旧草稿; 而且只在**草稿箱**里按编号找旧稿,
编号对不上草稿箱里的信就什么也不删 (旧的撰写窗口编号可能恰好等于某封老邮件的
编号, 不能因此误删收件箱里的信)。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING, Callable, Sequence

from .apple_mail_as_handlers import AS_FIND_MESSAGE, AS_RESOLVE_INBOX
from .apple_mail_osascript import FS, _escape_as_string
from .base import DataNotFoundError, EmailAdapterError, NotSupportedError

if TYPE_CHECKING:
    from .apple_mail import AppleMailAdapter

logger = logging.getLogger("catfish_email.adapters.apple_mail")

#: 存草稿没能确认它在草稿箱里的编号时, 先用撰写窗口的编号, 带这个前缀以示区别
#: (不是数字, 不会被当成草稿箱里某封信的编号去找)。
PENDING_PREFIX = "outgoing-"

# 草稿箱里的信按编号找: 先用 Mail 自己的引用形式, 再按列表的枚举方式找。
# 故意不用 whose —— 8/31 实测草稿箱里 whose 过滤会漏。
_AS_DRAFT_HANDLERS = """
on draftById(mbx, targetIdStr)
    try
        set n to targetIdStr as integer
        tell application "Mail"
            set m to «class mssg» id n of mbx
            if (id of m) is n then return m
        end tell
    end try
    try
        return my scanById(mbx, targetIdStr)
    end try
    return missing value
end draftById

on outgoingById(idStr)
    tell application "Mail"
        repeat with om in (every outgoing message)
            try
                if ((id of om) as string) is idStr then return (contents of om)
            end try
        end repeat
    end tell
    return missing value
end outgoingById

-- send returns a boolean on current Mail; tolerate versions that return nothing
on sendOutgoing(om)
    tell application "Mail" to set sentOk to send om
    set failed to false
    try
        if sentOk is false then set failed to true
    end try
    if failed then error "SEND_FAILED" number 8004
end sendOutgoing

on addressOf(s)
    set s to s as string
    if s contains "<" and s contains ">" then
        set AppleScript's text item delimiters to "<"
        set tailPart to text item -1 of s
        set AppleScript's text item delimiters to ">"
        set addr to text item 1 of tailPart
        set AppleScript's text item delimiters to ""
        return addr
    end if
    return s
end addressOf

on splitText(s, delim)
    set AppleScript's text item delimiters to delim
    set out to text items of s
    set AppleScript's text item delimiters to ""
    return out
end splitText

on trimText(s)
    set t to s
    repeat while t starts with " "
        set t to text 2 thru -1 of t
    end repeat
    repeat while t ends with " "
        set t to text 1 thru -2 of t
    end repeat
    return t
end trimText
"""

# 输出: 账号名 FS 草稿箱里的编号 (没确认到就空) FS 撰写窗口编号
_AS_CREATE_DRAFT = """
tell application "Mail"
    set FS to (character id 31)
    set accName to "{ACCOUNT}"
    set subj to "{SUBJECT}"
    set bodyPath to "{BODY_PATH}"
    set toList to "{TO}"
    set ccList to "{CC}"
    set bccList to "{BCC}"

    set fileRef to open for access POSIX file bodyPath
    set bodyText to (read fileRef as «class utf8»)
    close access fileRef

    set newMsg to make new outgoing message with properties {visible:false, subject:subj, content:bodyText}

    -- account: the one asked for, else whichever account Mail picked as sender
    set acc to missing value
    if accName is not "" then
        set acc to first account whose name of it is accName
        try
            set sender of newMsg to (item 1 of (email addresses of acc))
        end try
    else
        try
            set defaultSender to (sender of newMsg) as string
            repeat with a in every account
                repeat with addr in (email addresses of a)
                    if defaultSender contains (addr as string) then
                        set acc to contents of a
                        exit repeat
                    end if
                end repeat
                if acc is not missing value then exit repeat
            end repeat
        end try
        if acc is missing value then set acc to first account
    end if

    tell newMsg
        set toItems to my splitText(toList, ",")
        repeat with addr in toItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new to recipient at end of to recipients with properties {address:cleanAddr}
            end if
        end repeat
        set ccItems to my splitText(ccList, ",")
        repeat with addr in ccItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new cc recipient at end of cc recipients with properties {address:cleanAddr}
            end if
        end repeat
        set bccItems to my splitText(bccList, ",")
        repeat with addr in bccItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new bcc recipient at end of bcc recipients with properties {address:cleanAddr}
            end if
        end repeat
        -- DO NOT send (red line): the draft only goes into the Drafts mailbox
    end tell

    set mbx to my resolveInbox(acc, "Drafts")
    set beforeIds to {}
    set haveBefore to false
    try
        set beforeIds to id of every message of mbx
        set haveBefore to true
    end try
    save newMsg in mbx
    set outId to (id of newMsg) as string

    -- the saved copy is the message that was not in the Drafts mailbox before
    -- (without a before-snapshot an older draft with the same subject could be taken for it)
    set savedId to ""
    if not haveBefore then set savedId to "-"
    repeat 20 times
        if savedId is "-" then exit repeat
        try
            set nowIds to id of every message of mbx
            set newIds to {}
            set fallbackId to ""
            set k to 0
            repeat with x in nowIds
                set k to k + 1
                if beforeIds does not contain (contents of x) then
                    set fallbackId to (contents of x) as string
                    set end of newIds to fallbackId
                    set candSubj to ""
                    try
                        set candSubj to (subject of (message k of mbx)) as string
                    end try
                    if candSubj is subj then set savedId to fallbackId
                end if
            end repeat
            if savedId is "" and (count of newIds) is 1 then set savedId to fallbackId
        end try
        if savedId is not "" then exit repeat
        delay 0.25
    end repeat
    if savedId is "-" then set savedId to ""
    return ((name of acc) as string) & FS & savedId & FS & outId
end tell
""" + _AS_DRAFT_HANDLERS + AS_RESOLVE_INBOX + AS_FIND_MESSAGE

_AS_SEND_MESSAGE = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"
    set outIdStr to "{OUT_ID}"
    set acc to first account whose name of it is accName

    -- saved without a confirmed Drafts id: only the compose window exists
    if targetIdStr starts with "outgoing-" then
        set om to my outgoingById(text 10 thru -1 of targetIdStr)
        if om is missing value then error "MESSAGE_NOT_FOUND" number 8001
        my sendOutgoing(om)
        return "OK"
    end if

    set mbx to my resolveInbox(acc, "Drafts")
    set d to my draftById(mbx, targetIdStr)
    if d is missing value then
        -- ids handed out before 9/27 were compose-window ids
        set om to my outgoingById(targetIdStr)
        if om is missing value then error "MESSAGE_NOT_FOUND" number 8001
        my sendOutgoing(om)
        return "OK"
    end if
    set draftSubj to ""
    try
        set s to subject of d
        if s is not missing value then set draftSubj to s as string
    end try

    -- 1. the compose window this draft was saved from (same as pressing Send there)
    if outIdStr is not "" then
        set om to my outgoingById(outIdStr)
        if om is not missing value then
            set omSubj to ""
            try
                set omSubj to (subject of om) as string
            end try
            if omSubj is draftSubj then
                my sendOutgoing(om)
                delay 1
                try
                    set leftover to my draftById(mbx, targetIdStr)
                    if leftover is not missing value then delete leftover
                end try
                return "OK"
            end if
        end if
    end if

    -- 2. rebuild the draft as a new message, from the draft's own sender
    if (count of mail attachments of d) > 0 then error "DRAFT_HAS_ATTACHMENTS" number 8002
    set bodyText to content of d
    try
        set bodyText to bodyText as text
    end try
    set draftSender to ""
    try
        set draftSender to (sender of d) as string
    end try
    if draftSender is "" or draftSender is "missing value" then error "SENDER_MISMATCH" number 8003
    set toAddrs to {}
    repeat with r in (to recipients of d)
        set end of toAddrs to (address of r) as string
    end repeat
    set ccAddrs to {}
    repeat with r in (cc recipients of d)
        set end of ccAddrs to (address of r) as string
    end repeat
    set bccAddrs to {}
    repeat with r in (bcc recipients of d)
        set end of bccAddrs to (address of r) as string
    end repeat
    if (count of toAddrs) is 0 then error "DRAFT_NO_RECIPIENT" number 8005

    set newMsg to make new outgoing message with properties {visible:false, subject:draftSubj, content:bodyText}
    try
        set sender of newMsg to draftSender
        set senderNow to (sender of newMsg) as string
    on error
        error "SENDER_MISMATCH" number 8003
    end try
    if senderNow does not contain (my addressOf(draftSender)) then error "SENDER_MISMATCH" number 8003
    tell newMsg
        repeat with a in toAddrs
            make new to recipient at end of to recipients with properties {address:(a as string)}
        end repeat
        repeat with a in ccAddrs
            make new cc recipient at end of cc recipients with properties {address:(a as string)}
        end repeat
        repeat with a in bccAddrs
            make new bcc recipient at end of bcc recipients with properties {address:(a as string)}
        end repeat
    end tell
    my sendOutgoing(newMsg)
    try
        delete d
    end try
    return "OK"
end tell
""" + _AS_DRAFT_HANDLERS + AS_RESOLVE_INBOX + AS_FIND_MESSAGE

# 只在该账号草稿箱里按编号找, 找到才删 —— 改草稿去掉旧版本用
_AS_DELETE_DRAFT = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"
    set acc to first account whose name of it is accName
    set mbx to my resolveInbox(acc, "Drafts")
    set d to my draftById(mbx, targetIdStr)
    if d is missing value then error "MESSAGE_NOT_FOUND" number 8001
    delete d
    return "OK"
end tell
""" + _AS_DRAFT_HANDLERS + AS_RESOLVE_INBOX + AS_FIND_MESSAGE

_SEND_ERRORS = {
    "DRAFT_HAS_ATTACHMENTS": "这封草稿带附件, 鲶鱼没法原样带着附件发出。请在「邮件」App 的草稿箱里打开它发送。",
    "SENDER_MISMATCH": "「邮件」App 不接受用这封草稿原来的发件账号发送, 为免从别的账号发出去, 已停止。请在「邮件」App 里发送。",
    "DRAFT_NO_RECIPIENT": "这封草稿还没有收件人, 先点「修改草稿」填上收件人。",
    "SEND_FAILED": "「邮件」App 没能发出这封邮件 (账号离线或发件服务器拒收), 草稿还在草稿箱里。",
}


# ── 撰写窗口编号: 存草稿时记下, 发送时用 ─────────────────────


def _outgoing_path():
    from .. import index_store  # noqa: PLC0415 — 同目录 (~/.catfish), CATFISH_HOME 优先

    return index_store.index_db_path().parent / "apple_mail_outgoing.json"


def _load_outgoing() -> dict[str, str]:
    try:
        data = json.loads(_outgoing_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_outgoing(data: dict[str, str]) -> None:
    path = _outgoing_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = dict(list(data.items())[-100:])  # 只留最近 100 条
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as error:
        logger.warning("记不下撰写窗口编号 (不影响草稿, 发送时改为照草稿重建): %s", error)


def remember_outgoing(draft_id: str, out_id: str) -> None:
    data = _load_outgoing()
    data.pop(draft_id, None)
    data[draft_id] = out_id
    _save_outgoing(data)


def outgoing_for(draft_id: str) -> str:
    return _load_outgoing().get(draft_id, "")


def forget_outgoing(draft_id: str) -> None:
    data = _load_outgoing()
    if data.pop(draft_id, None) is not None:
        _save_outgoing(data)


# ── 三个动作 ─────────────────────────────────────────────


def _draft_account(adapter: "AppleMailAdapter", account: str | None,
                   source_id: str | None) -> str:
    """草稿落哪个账号: 指定了就用; 改草稿 / 回复跟着原信; 都没有交给 Mail 默认发件账号。"""
    if account:
        return adapter._resolve_account_name(account)
    if source_id and source_id.startswith("apple_mail|"):
        return adapter._unpack_id(source_id)[0]
    return ""


def create_draft(
    adapter: "AppleMailAdapter",
    run: Callable[[str], str],
    *,
    to: Sequence[str],
    subject: str,
    body: str,
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    in_reply_to: str | None = None,
    account: str | None = None,
    replaces: str | None = None,
) -> str:
    if not to:
        raise ValueError("create_draft: to 不能空")
    if adapter._use_emlx_fallback:
        raise NotSupportedError(
            "Apple Mail EMLX fallback 模式只读, 不能写草稿. "
            "去 System Settings 给 catfish 'Mail' Automation 权限后重试.",
        )
    account_name = _draft_account(adapter, account, replaces or in_reply_to)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8",
    ) as tf:
        tf.write(body)
        body_path = tf.name
    try:
        script = (
            _AS_CREATE_DRAFT
            .replace("{ACCOUNT}", _escape_as_string(account_name))
            .replace("{SUBJECT}", _escape_as_string(subject))
            .replace("{BODY_PATH}", body_path)
            .replace("{TO}", _escape_as_string(",".join(to)))
            .replace("{CC}", _escape_as_string(",".join(cc)))
            .replace("{BCC}", _escape_as_string(",".join(bcc)))
        )
        out = run(script)
    finally:
        try:
            os.unlink(body_path)
        except OSError:
            pass

    parts = out.strip("\r\n ").split(FS)  # 别用 strip(): Python 把 \x1f 也当空白
    if len(parts) != 3 or not parts[0] or not parts[2]:
        raise EmailAdapterError(f"create_draft: Mail 返回格式不对: {out[:120]!r}")
    saved_account, saved_id, out_id = parts
    if not saved_id:
        # 存了, 但没在草稿箱里认出它 —— 不删旧稿 (新稿没确认, 删了可能两头落空)
        logger.warning("草稿已交给 Mail 保存, 但没在 %s 的草稿箱里确认到它", saved_account)
        return adapter._pack_id(saved_account, PENDING_PREFIX + out_id)
    draft_id = adapter._pack_id(saved_account, saved_id)
    remember_outgoing(draft_id, out_id)
    if replaces and replaces != draft_id:
        try:
            delete_draft(adapter, run, replaces)
            forget_outgoing(replaces)
        except EmailAdapterError as error:
            # 多一份旧稿员工看得见、能手动删; 反过来丢了新稿才是真损失
            logger.warning("新草稿已保存, 但旧草稿没去掉 (%s): %s", replaces, error)
    return draft_id


def delete_draft(adapter: "AppleMailAdapter", run: Callable[[str], str], draft_id: str) -> None:
    account_name, msg_id = adapter._unpack_id(draft_id)
    out = run(
        _AS_DELETE_DRAFT
        .replace("{ACCOUNT}", _escape_as_string(account_name))
        .replace("{MSG_ID}", _escape_as_string(msg_id))
    )
    if out.strip() != "OK":
        raise EmailAdapterError(f"删旧草稿: Mail 返回 {out[:120]!r}")


def send_message(adapter: "AppleMailAdapter", run: Callable[[str], str], message_id: str) -> None:
    """红线: 调用方 (Companion 发送按钮) 必须人工确认过才调到这里。"""
    if adapter._use_emlx_fallback:
        raise NotSupportedError(
            "Apple Mail EMLX fallback 模式不支持 send_message — Mail.app 必须开着才能发邮件"
        )
    account_name, msg_id = adapter._unpack_id(message_id)
    script = (
        _AS_SEND_MESSAGE
        .replace("{ACCOUNT}", _escape_as_string(account_name))
        .replace("{MSG_ID}", _escape_as_string(msg_id))
        .replace("{OUT_ID}", _escape_as_string(outgoing_for(message_id)))
    )
    # 刚存的草稿 Mail 可能还没落定 (P3.5.57): 只有「找不到」才重试
    last: DataNotFoundError | None = None
    for attempt in range(3):
        try:
            out = run(script)
            break
        except DataNotFoundError as error:
            last = error
            if attempt < 2:
                time.sleep(0.3)
        except EmailAdapterError as error:
            for code, text in _SEND_ERRORS.items():
                if code in str(error):
                    raise EmailAdapterError(text) from error
            raise
    else:
        raise DataNotFoundError(
            "草稿箱里找不到这封草稿 (可能已在「邮件」App 里发出或删掉了)。刷新草稿箱再看。"
        ) from last
    if out.strip() != "OK":
        raise EmailAdapterError(f"send_message: Mail 返回 {out[:120]!r}")
    forget_outgoing(message_id)
