"""Apple Mail adapter — osascript templates.

8 个 AppleScript 字符串常量, 给 apple_mail.py 的 _run_osascript 调.
内嵌的 ASCII 0x1f/0x1e 控制字符跟 character id 31 解决 macOS Sequoia 兼容
(BL-EMAIL-APPLEMAIL-AS-CTRLCHAR 5/18).

5/20: 1538 → ~1045 拆分, AS 模板抽这里方便单测 + 改一处不污染主文件.
"""
from __future__ import annotations

_AS_PING = """
tell application "System Events"
    return (exists process "Mail")
end tell
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

# list_messages: messageId | subject | sender | date | isRead | folder
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
        set msgs to (messages of mb whose read status is false)
    else
        set msgs to (messages of mb)
    end if
    set total to count of msgs
    set out to ""
    set i to 0
    -- 从最后一封倒着遍历 (最新) — Mail.app 索引顺序通常是收到时间正序,
    -- 倒序取就是按时间逆序拿最新的 limitN 封.
    repeat with idx from total to 1 by -1
        if i >= limitN then exit repeat
        set m to item idx of msgs
        set msgId to (id of m) as string
        set subj to (subject of m) as string
        set sndr to (sender of m) as string
        set dt to my isoDate(date received of m)
        set readSt to "1"
        if (read status of m) is false then set readSt to "0"
        set out to out & msgId & FS & subj & FS & sndr & FS & dt & FS & readSt & FS & folderName & RS
        set i to i + 1
    end repeat
    return out
end tell

-- BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18): resolve canonical inbox across providers.
-- iCloud/Gmail/Exchange/Outlook all name their inbox differently; try the common
-- candidates one by one, fall back to literal name if not "Inbox".
on resolveInbox(acc, wantName)
    if wantName is "Inbox" then
        set candidates to {"INBOX", "Inbox", "收件箱", "受信箱"}
        repeat with cand in candidates
            tell application "Mail"
                try
                    return mailbox (cand as string) of acc
                end try
            end tell
        end repeat
    end if
    tell application "Mail"
        return mailbox wantName of acc
    end tell
end resolveInbox

-- BL-EMAIL-DATE-ISO (5/18): coerce AS date to ISO-8601 ourselves.
-- `(date received of m) as string` is locale-dependent (zh-CN gives '2026年...' which
-- Python's strptime can't parse without explicit locale). We assemble year-mo-dyTh:mn:sc
-- manually, in local TZ (no offset suffix). Python side just parses as naive ISO.
on isoDate(d)
    set yr to year of d as integer
    set mo to month of d as integer
    set dy to day of d as integer
    set hr to hours of d as integer
    set mn to minutes of d as integer
    set sc to seconds of d as integer
    return _pad4(yr) & "-" & _pad2(mo) & "-" & _pad2(dy) & "T" & _pad2(hr) & ":" & _pad2(mn) & ":" & _pad2(sc)
end isoDate

on _pad2(n)
    set s to n as string
    if (count of s) < 2 then set s to "0" & s
    return s
end _pad2

on _pad4(n)
    set s to n as string
    repeat while (count of s) < 4
        set s to "0" & s
    end repeat
    return s
end _pad4
"""

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
    -- BL-EMAIL-APPLEMAIL-READ-ID-STR-V2 (5/18): id 总作字符串塞 AS 防解析挂 -2741,
    -- 但 `whose` 子句里 `(id of it as string)` 不被 Mail.app 引擎认 (实盘"找不到").
    -- 解法: 优先把字符串 coerce 回 integer 走整数比较 (大多数 id 是数字);
    -- coerce 失败 (UUID / 字母) 才 fallback 字符串路径 (loop 比较).
    try
        set targetIdNum to (targetIdStr as integer)
    on error
        set targetIdNum to missing value
    end try

    set foundMsg to missing value
    repeat with mb in mailboxes of acc
        if targetIdNum is not missing value then
            -- 整数路径 (常见)
            try
                set m to (first message of mb whose id is targetIdNum)
                set foundMsg to m
                exit repeat
            end try
        else
            -- 字符串路径 (UUID 形 id), 逐封比较 (慢但兜底)
            try
                repeat with m in messages of mb
                    if (id of m as string) is targetIdStr then
                        set foundMsg to m
                        exit repeat
                    end if
                end repeat
                if foundMsg is not missing value then exit repeat
            end try
        end if
    end repeat
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
    set dt to (date received of foundMsg) as string
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
"""

# send_message: 5/18 BL-EMAIL-COMPOSE-SEND. AS `send <msg>` 真把草稿发出去.
# 红线: 上层 UI 必须人工 confirm 之后才调到这, adapter 不做"是不是人发的" 校验.
#
# P3.5.57 Phase 4 (6/22 鸿波 catch retry 3 次仍失败): 找 send 真因 — 不是 race,
# 是 AppleScript class 不匹配!
#   - create_draft 用 `make new outgoing message` 创 **outgoing message** class
#   - 老 send 在 `mailboxes of acc` 找 **message** class
#   - 这俩是 Mail.app AS 里两个不同 class:
#       * outgoing message — 撰写状态, 顶级在 Mail app 下, 不在 mailbox 里
#       * message — mailbox (INBOX/Drafts/Sent) 里已落档的邮件
#   - `make new outgoing message` 即使 visible:true 仍是 outgoing message,
#     永远不在 mailboxes of acc → `whose id is` 必失败, retry 救不了
#
# 真修法: 先在 outgoing messages 找 (这是 create_draft 真正放的地方),
# 找不到再 fallback mailboxes (兼容老路径或员工已手动从 Drafts 重发的场景).
# Phase 3 的 sleep + retry 保留作二次防御, 但根本性不再依赖它.
_AS_SEND_MESSAGE = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"

    try
        set targetIdNum to (targetIdStr as integer)
    on error
        set targetIdNum to missing value
    end try

    set foundMsg to missing value

    -- P3.5.57 Phase 4: 优先找 outgoing messages (create_draft `make new outgoing
    -- message` 真正放的地方; outgoing message 不在 mailbox 里)
    try
        if targetIdNum is not missing value then
            repeat with om in outgoing messages
                try
                    if (id of om) is targetIdNum then
                        set foundMsg to om
                        exit repeat
                    end if
                end try
            end repeat
        else
            repeat with om in outgoing messages
                try
                    if ((id of om) as string) is targetIdStr then
                        set foundMsg to om
                        exit repeat
                    end if
                end try
            end repeat
        end if
    end try

    -- fallback: mailboxes 里找 (员工可能从 Drafts 自己关掉撰写窗口让它落档,
    -- 这时 outgoing message 没了, draft 进了 Drafts mailbox)
    if foundMsg is missing value then
        try
            set acc to first account whose name of it is accName
            repeat with mb in mailboxes of acc
                if targetIdNum is not missing value then
                    try
                        set m to (first message of mb whose id is targetIdNum)
                        set foundMsg to m
                        exit repeat
                    end try
                else
                    try
                        repeat with m in messages of mb
                            if (id of m as string) is targetIdStr then
                                set foundMsg to m
                                exit repeat
                            end if
                        end repeat
                        if foundMsg is not missing value then exit repeat
                    end try
                end if
            end repeat
        end try
    end if

    if foundMsg is missing value then
        error "MESSAGE_NOT_FOUND" number 8001
    end if

    send foundMsg
    return "OK"
end tell
"""

# delete_message: 5/18 BL-EMAIL-DELETE. 同 _AS_GET_MESSAGE id-lookup pattern.
# AS `delete <msg>` 在 Mail.app 默认行为 = "移到 Trash 文件夹" (跟用户按 ⌫
# 键同效果). **不是物理删** — Trash 30 天内能找回. 红线对齐主流邮件客户端 UX.
_AS_DELETE_MESSAGE = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"

    set acc to first account whose name of it is accName
    try
        set targetIdNum to (targetIdStr as integer)
    on error
        set targetIdNum to missing value
    end try

    set foundMsg to missing value
    repeat with mb in mailboxes of acc
        if targetIdNum is not missing value then
            try
                set m to (first message of mb whose id is targetIdNum)
                set foundMsg to m
                exit repeat
            end try
        else
            try
                repeat with m in messages of mb
                    if (id of m as string) is targetIdStr then
                        set foundMsg to m
                        exit repeat
                    end if
                end repeat
                if foundMsg is not missing value then exit repeat
            end try
        end if
    end repeat
    if foundMsg is missing value then
        error "MESSAGE_NOT_FOUND" number 8001
    end if

    delete foundMsg
    return "OK"
end tell
"""

# mark_read: 5/18 BL-EMAIL-MARK-READ. 同 _AS_GET_MESSAGE 的 id-lookup pattern,
# 找到 message 后 `set read status of m to READ_FLAG`. 不返字段, 只返 "OK" / 异常.
_AS_MARK_READ = """
tell application "Mail"
    set accName to "{ACCOUNT}"
    set targetIdStr to "{MSG_ID}"
    set readFlag to {READ_FLAG}

    set acc to first account whose name of it is accName
    try
        set targetIdNum to (targetIdStr as integer)
    on error
        set targetIdNum to missing value
    end try

    set foundMsg to missing value
    repeat with mb in mailboxes of acc
        if targetIdNum is not missing value then
            try
                set m to (first message of mb whose id is targetIdNum)
                set foundMsg to m
                exit repeat
            end try
        else
            try
                repeat with m in messages of mb
                    if (id of m as string) is targetIdStr then
                        set foundMsg to m
                        exit repeat
                    end if
                end repeat
                if foundMsg is not missing value then exit repeat
            end try
        end if
    end repeat
    if foundMsg is missing value then
        error "MESSAGE_NOT_FOUND" number 8001
    end if

    set read status of foundMsg to readFlag
    return "OK"
end tell
"""

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
    set mb to my resolveInbox(acc, folderName)
    set msgs to (messages of mb whose subject contains q or sender contains q)
    set out to ""
    set i to 0
    repeat with m in msgs
        if i >= limitN then exit repeat
        set msgId to (id of m) as string
        set subj to (subject of m) as string
        set sndr to (sender of m) as string
        set dt to my isoDate(date received of m)
        set readSt to "1"
        if (read status of m) is false then set readSt to "0"
        set out to out & msgId & FS & subj & FS & sndr & FS & dt & FS & readSt & FS & folderName & RS
        set i to i + 1
    end repeat
    return out
end tell

-- BL-EMAIL-APPLEMAIL-INBOX-NAMES (5/18): same handler as _AS_LIST_MESSAGES; AS doesn't
-- share handlers across osascript invocations so we repeat it.
on resolveInbox(acc, wantName)
    if wantName is "Inbox" then
        set candidates to {"INBOX", "Inbox", "收件箱", "受信箱"}
        repeat with cand in candidates
            tell application "Mail"
                try
                    return mailbox (cand as string) of acc
                end try
            end tell
        end repeat
    end if
    tell application "Mail"
        return mailbox wantName of acc
    end tell
end resolveInbox

-- BL-EMAIL-DATE-ISO (5/18): same as _AS_LIST_MESSAGES, repeat handlers.
on isoDate(d)
    set yr to year of d as integer
    set mo to month of d as integer
    set dy to day of d as integer
    set hr to hours of d as integer
    set mn to minutes of d as integer
    set sc to seconds of d as integer
    return _pad4(yr) & "-" & _pad2(mo) & "-" & _pad2(dy) & "T" & _pad2(hr) & ":" & _pad2(mn) & ":" & _pad2(sc)
end isoDate

on _pad2(n)
    set s to n as string
    if (count of s) < 2 then set s to "0" & s
    return s
end _pad2

on _pad4(n)
    set s to n as string
    repeat while (count of s) < 4
        set s to "0" & s
    end repeat
    return s
end _pad4
"""

# create_draft: body 从 temp 文件读; 支持 to/cc/bcc
#
# P3.3.63 (6/13 hb): 顶部加 activate. visible:true 让草稿窗口本来就弹,
# 但 Mail.app 不在前台时窗口藏背景, 员工还得 cmd+tab 找. activate 把
# Mail.app 切前台, 员工放完草稿直接看见草稿窗口, 审 / 改 / cmd+shift+D
# 发送一步到位 (catfish 不替员工按 send, 红线还在)
_AS_CREATE_DRAFT = """
tell application "Mail"
    activate
    set accName to "{ACCOUNT}"
    set subj to "{SUBJECT}"
    set bodyPath to "{BODY_PATH}"
    set toList to "{TO}"
    set ccList to "{CC}"
    set bccList to "{BCC}"

    set fileRef to open for access POSIX file bodyPath
    set bodyText to (read fileRef as «class utf8»)
    close access fileRef

    set newMsg to make new outgoing message with properties {visible:true, subject:subj, content:bodyText}
    tell newMsg
        -- to recipients
        set toItems to my splitText(toList, ",")
        repeat with addr in toItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new to recipient at end of to recipients with properties {address:cleanAddr}
            end if
        end repeat
        -- cc recipients
        set ccItems to my splitText(ccList, ",")
        repeat with addr in ccItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new cc recipient at end of cc recipients with properties {address:cleanAddr}
            end if
        end repeat
        -- bcc recipients (BL-EMAIL-APPLEMAIL-FULL 5/18)
        set bccItems to my splitText(bccList, ",")
        repeat with addr in bccItems
            set cleanAddr to my trimText(addr as string)
            if cleanAddr is not "" then
                make new bcc recipient at end of bcc recipients with properties {address:cleanAddr}
            end if
        end repeat
        -- DO NOT call send; stays in Drafts (red line)
    end tell

    return id of newMsg as string
end tell

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


# ── 工具函数 ────────────────────────────────────────────

