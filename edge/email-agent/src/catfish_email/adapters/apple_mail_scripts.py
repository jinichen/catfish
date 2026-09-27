"""Apple Mail adapter — osascript templates.

8 个 AppleScript 字符串常量, 给 apple_mail.py 的 _run_osascript 调.
内嵌的 ASCII 0x1f/0x1e 控制字符跟 character id 31 解决 macOS Sequoia 兼容
(BL-EMAIL-APPLEMAIL-AS-CTRLCHAR 5/18).

5/20: 1538 → ~1045 拆分, AS 模板抽这里方便单测 + 改一处不污染主文件.

9/27: 几个模板共用的 handler (找文件夹 / 按 id 找信 / 取日期) 挪到
apple_mail_as_handlers.py, 每段只写一次, 模板末尾拼上; 起草和发送两个模板
挪到 apple_mail_drafts.py (连同它们的 Python 逻辑)。
"""
from __future__ import annotations

from .apple_mail_as_handlers import AS_DATES, AS_FIND_MESSAGE, AS_RESOLVE_INBOX

_AS_PING = """
tell application "System Events"
    return (exists process "Mail")
end tell
"""

# P3.5.204.c (7/9 鸿波 catch "邮件客户端还没同步的邮件, 在鲶鱼里面无法激活客户端去同步"):
# tell Mail 主动 fetch new mail. Apple Mail 支持 `check for new mail` — 让 Mail
# 立即去邮箱服务器拉一次新邮件, 不等定时同步 (通常 5-15 min 一次). 员工在 Companion
# 里看不到新邮件时点"同步", 立即触发. 支持指定 account: 传 accountName 就只同步那个,
# 空字符串同步全部账号.
_AS_CHECK_NEW_MAIL = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    if accName is "" then
        check for new mail
    else
        try
            set acc to first account whose name of it is accName
            check for new mail for acc
        on error
            check for new mail
        end try
    end if
end tell
return "ok"
"""

# BL-EMAIL-APPLEMAIL-AS-CTRLCHAR (5/18):
#   - 老 f-string interpolate FS="\x1f"/RS="\x1e" 进 AS string literal → osascript -2741.
#   - AS 里用 `character id 31` (modern, Mac 10.5+ ASCII character 替代品) 重建分隔符.
#   - AS 注释里 *不* 写中文 — osascript 解析中文 comment 时 line/col 计算错位, 错误
#     位置难定位; 中文说明全挪到 Python 这边.
#   - `set accs to every account` 比 `set accs to accounts` 更明确, 部分 macOS 版本
#     `accounts` 单独出现会被解析成 class name (-2741 在 col 145 / 497 都踩这).
_AS_LIST_ACCOUNTS = """
tell application "Mail"
    set FS to (character id 31)
    set RS to (character id 30)
    set out to ""
    repeat with acc in every account
        set accName to (name of acc) as string
        set addrList to (email addresses of acc)
        set addr to ""
        if (count of addrList) > 0 then
            set addr to (item 1 of addrList) as string
        end if
        set out to out & accName & FS & addr & FS & "0" & RS
    end repeat
    return out
end tell
"""

# list_messages: messageId | subject | sender | date | isRead | folder | rfcMsgId | rawHeaders
# P3.5.58 (6/22 鸿波 catch): 加 rfcMsgId (RFC 822 Message-ID) + rawHeaders 让
# Python 端 parse In-Reply-To / References, 供前端 isReplied() thread 算法用.
# 老 6 字段输出现升 8. try 兜底 — 某些 Mail.app 版本可能不暴露 headers / 老邮件
# header 段坏掉, 取不到返空串, Python 端 _parse_thread_headers 返 None 静默.
# BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18 鸿波实盘):
#   Mail.app 在不同 IMAP provider 下 inbox 物理名不一样:
#     - iCloud:   "INBOX" / "Inbox"
#     - Gmail:    "INBOX" / "[Gmail]/All Mail" / 本地化"收件箱"
#     - Exchange: "Inbox" / 本地化"收件箱"
#   老硬编码 `mailbox "Inbox" of acc` 在 Gmail 5 个账号挂 "不能获得 mailbox Inbox of account id...".
#   改成: 当 folderName="Inbox" 时, AS 端按候选列表逐个 try 找第一个能拿到的;
#   非 "Inbox" 时按字面名 (员工自己指定 subfolder 不该兜底).
_AS_LIST_MESSAGES = """
tell application "Mail"
    set FS to (character id 31)
    set RS to (character id 30)
    set accName to "{ACCOUNT}"
    set folderName to "{FOLDER}"
    set limitN to {LIMIT}
    set unreadOnly to {UNREAD_ONLY}
    set acc to first account whose name of it is accName
    set mb to my resolveInbox(acc, folderName)
    -- 5/18 BL-EMAIL-APPLEMAIL-UNREAD-OLDESTFIRST: Mail.app `messages of mb` 默认按
    -- 索引返 (oldest-first 通常 = 按收到时间正序). 老逻辑 loop 内手判 `unreadOnly`
    -- + early exit by limitN, 限制低时 (--limit 1) 可能把 100 封老 read 全扫了
    -- 才碰到第一封 unread, 但 limit 已经 0 → 返空. 改成 AS 端 `whose` 提前过
    -- 滤; 加 `(date received desc)` 排序拿最新.
    if unreadOnly then
        try
            set msgs to (messages of mb whose read status is false)
        on error
            -- whose is unreliable on some Mail versions; filter below instead
            set msgs to (messages of mb)
        end try
    else
        set msgs to (messages of mb)
    end if
    set total to count of msgs
    set out to ""
    set i to 0
    -- newest last in Mail's order: walk backwards to get the newest limitN.
    -- One unreadable message must not fail the whole folder (9/27: drafts
    -- have no date received; the old code lost the entire Drafts list).
    repeat with idx from total to 1 by -1
        if i >= limitN then exit repeat
        try
            set m to item idx of msgs
            set msgId to (id of m) as string
            set readSt to "1"
            try
                if (read status of m) is false then set readSt to "0"
            end try
            if unreadOnly and readSt is "1" then error "skip read message" number 8100
            set subj to ""
            try
                set s to subject of m
                if s is not missing value then set subj to s as string
            end try
            set sndr to ""
            try
                set s to sender of m
                if s is not missing value then set sndr to s as string
            end try
            set dt to my msgDate(m)
            -- P3.5.58: RFC 822 Message-ID + raw headers for thread detection
            set rfcMsgId to ""
            try
                set rfcMsgId to (message id of m) as string
            end try
            set rawHdrs to ""
            try
                set rawHdrs to (all headers of m) as string
            end try
            set out to out & msgId & FS & subj & FS & sndr & FS & dt & FS & readSt & FS & folderName & FS & rfcMsgId & FS & rawHdrs & RS
            set i to i + 1
        end try
    end repeat
    return out
end tell
""" + AS_RESOLVE_INBOX + AS_DATES

# read_message: AS 写 body (text) + source (完整 RFC822) 到 2 个 temp 文件,
# Python 解析 source 提 HTML part. BL-EMAIL-APPLEMAIL-FULL (5/18).
_AS_GET_MESSAGE = """
tell application "Mail"
    set FS to (character id 31)
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"
    set bodyPath to "{BODY_PATH}"
    set sourcePath to "{SOURCE_PATH}"

    set acc to first account whose name of it is accName
    -- 9/27: lookup lives in findMessage (whose -> by-id -> id-list scan)
    set foundMsg to my findMessage(acc, targetIdStr)
    if foundMsg is missing value then
        error "MESSAGE_NOT_FOUND" number 8001
    end if

    set bodyText to content of foundMsg
    set fileRef to open for access POSIX file bodyPath with write permission
    set eof fileRef to 0
    write bodyText to fileRef as «class utf8»
    close access fileRef

    -- Write full RFC822 source (with HTML part); Python parses body_html via email.parser
    try
        set rawSource to source of foundMsg
        set srcRef to open for access POSIX file sourcePath with write permission
        set eof srcRef to 0
        write rawSource to srcRef as «class utf8»
        close access srcRef
    on error
        -- source unavailable (old Mail version / network fetch fail) - leave HTML blank
    end try

    set subj to subject of foundMsg
    set sndr to sender of foundMsg
    set dt to my msgDate(foundMsg)
    set toStr to ""
    try
        repeat with r in to recipients of foundMsg
            if toStr = "" then
                set toStr to address of r
            else
                set toStr to toStr & ", " & address of r
            end if
        end repeat
    end try
    set ccStr to ""
    try
        repeat with r in cc recipients of foundMsg
            if ccStr = "" then
                set ccStr to address of r
            else
                set ccStr to ccStr & ", " & address of r
            end if
        end repeat
    end try
    set folderName to name of mailbox of foundMsg

    return subj & FS & sndr & FS & dt & FS & toStr & FS & ccStr & FS & folderName
end tell
""" + AS_RESOLVE_INBOX + AS_FIND_MESSAGE + AS_DATES

# delete_message: 5/18 BL-EMAIL-DELETE. 同 _AS_GET_MESSAGE id-lookup pattern.
# AS `delete <msg>` 在 Mail.app 默认行为 = "移到 Trash 文件夹" (跟用户按 ⌫
# 键同效果). **不是物理删** — Trash 30 天内能找回. 红线对齐主流邮件客户端 UX.
_AS_DELETE_MESSAGE = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"

    set acc to first account whose name of it is accName
    set foundMsg to my findMessage(acc, targetIdStr)
    if foundMsg is missing value then
        error "MESSAGE_NOT_FOUND" number 8001
    end if

    delete foundMsg
    return "OK"
end tell
""" + AS_RESOLVE_INBOX + AS_FIND_MESSAGE

# mark_read: 5/18 BL-EMAIL-MARK-READ. 同 _AS_GET_MESSAGE 的 id-lookup pattern,
# 找到 message 后 `set read status of m to READ_FLAG`. 不返字段, 只返 "OK" / 异常.
_AS_MARK_READ = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"
    set readFlag to {READ_FLAG}

    set acc to first account whose name of it is accName
    set foundMsg to my findMessage(acc, targetIdStr)
    if foundMsg is missing value then
        error "MESSAGE_NOT_FOUND" number 8001
    end if

    set read status of foundMsg to readFlag
    return "OK"
end tell
""" + AS_RESOLVE_INBOX + AS_FIND_MESSAGE

# search: AS messages whose subject contains q OR sender contains q
# BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18): 同样走 resolveInbox 兜底 cross-account inbox 名.
_AS_SEARCH = """
tell application "Mail"
    set FS to (character id 31)
    set RS to (character id 30)
    set accName to "{ACCOUNT}"
    set folderName to "{FOLDER}"
    set q to "{QUERY}"
    set limitN to {LIMIT}
    set acc to first account whose name of it is accName
    set out to ""
    set i to 0
    -- P3.5.152 (6/30 鸿波 catch "邮件搜不到"): folder="*" 跨所有 mailbox.
    -- AppleScript 不支持 `mailbox "*" of acc` (返 error -1728), 必须遍历
    -- `mailboxes of acc`. 老逻辑 fall 进 resolveInbox 走 wantName 字面值,
    -- "*" 时直接挂 → 三个公司账号邮件全搜不到. 这里分两条路径.
    if folderName is "*" then
        repeat with mb in mailboxes of acc
            if i >= limitN then exit repeat
            try
                set msgs to (messages of mb whose subject contains q or sender contains q)
                repeat with m in msgs
                    if i >= limitN then exit repeat
                    set msgId to (id of m) as string
                    set subj to (subject of m) as string
                    set sndr to (sender of m) as string
                    set dt to my msgDate(m)
                    set readSt to "1"
                    if (read status of m) is false then set readSt to "0"
                    set mbName to (name of mb) as string
                    set out to out & msgId & FS & subj & FS & sndr & FS & dt & FS & readSt & FS & mbName & RS
                    set i to i + 1
                end repeat
            end try
        end repeat
    else
        set mb to my resolveInbox(acc, folderName)
        set msgs to (messages of mb whose subject contains q or sender contains q)
        repeat with m in msgs
            if i >= limitN then exit repeat
            try
                set msgId to (id of m) as string
                set subj to (subject of m) as string
                set sndr to (sender of m) as string
                set dt to my msgDate(m)
                set readSt to "1"
                if (read status of m) is false then set readSt to "0"
                set out to out & msgId & FS & subj & FS & sndr & FS & dt & FS & readSt & FS & folderName & RS
                set i to i + 1
            end try
        end repeat
    end if
    return out
end tell
""" + AS_RESOLVE_INBOX + AS_DATES


# ── 工具函数 ────────────────────────────────────────────

