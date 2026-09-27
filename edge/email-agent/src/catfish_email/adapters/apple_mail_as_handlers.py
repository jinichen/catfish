"""Apple Mail · 多个 AppleScript 模板共用的 handler (9/27)。

osascript 每次跑一段独立脚本, handler 不能跨脚本共享, 以前只能在每个模板里各抄
一份 (resolveInbox 就抄了两份, 按 id 找信的循环抄了四份)。这里每段只写一次,
模板末尾拼上。AS 注释一律英文 (中文注释会让 osascript 报错的行列号错位)。

# 这次改了什么, 依据是什么

**按 id 找信不再只信一种办法** (findMessage)。原来只有一条路: 在账号的每个邮箱里
`first message of mb whose id is N`。这台机器上 whose 过滤漏过信 —— 小鲶 8/31
实测: 草稿箱里明明有两封主题以 "tst" 开头的信, `whose subject starts with "tst"`
仍然报 -1728; `[Gmail]/草稿` 里有一封主题完全相同的信,
`whose subject = "<那个主题>"` 也报 -1728。

更正 (9/27 诊断之后): 当初把 Google 收件箱那封点开报「找不到」归到 whose 头上,
诊断不支持 —— 同一套老代码读 Google 的 2753 是好的 (whose 在「重要」里就命中了)。
那封信诊断时已不在收件箱 (收件箱只剩 3 封), 更可能是列表过时: 信已在别处
移走 / 删掉, Companion 还显示着。这个由前端处理 (读到「邮件不存在」就把它
从列表拿掉并刷新)。下面两条兜底仍然留着, 防的是 8/31 那种 whose 漏信:

  1. 按编号直接取 —— Mail 自己给出的邮件引用就是这个形式
     (报错信息里的 `message id 2292 of mailbox "Drafts" of account id ...`)。
     ⚠ 脚本里**不能**照着写成 `message id N`: Mail 的词典里 `message id` 是
     一个属性 (RFC 的 Message-ID), 编译器把它当属性读, 后面的 N 就成了语法错
     (9/28 真机 -2741, 1.0.44–1.0.48 整段读信脚本编译不过)。写成原始类码
     `«class mssg» id N`, 编译结果跟 Mail 自己的引用一样, 但不经过词典里的词。
  2. 列表用什么方式枚举, 就用什么方式找: 取 `id of every message of mb`,
     在本地列表里找到位置 k, 再取 `message k of mb`, 取回来再核一次 id。
     列表正是这样拿到这个 id 的, 所以只要信还在列表那个文件夹里就一定找得到。

**文件夹名找不到时往下一层找** (resolveInbox)。Gmail 的已发送/已删除/垃圾邮件
在 `[Gmail]` 下面 (8/31 实测草稿箱是 `mailbox "[Gmail]/草稿"`)。
"Drafts" 这个名字 Mail 会自动映射到 Gmail 的草稿箱 (8/31 实测
`mailbox "Drafts" of account "Google"` 取到的就是 `[Gmail]/草稿`),
其余几个没有实测, 所以顶层按名字找不到时, 再按名字在全部邮箱 (含子邮箱) 里找。

**日期取不到不再让整个文件夹失败** (msgDate)。列表原来对每封信都直接取
`date received`, 任何一封取不到, 整个账号这个文件夹就报错 —— 而 CLI 在别的
来源成功时只把错误写进 stderr, 界面上看不出来。先取 date received, 取不到退到
date sent, 都没有就留空。
更正 (9/27 诊断之后): 当初以为「草稿箱为空」是这个原因, 诊断显示草稿箱确实是
空的 (统一草稿箱 0 封)。草稿是否真的没有 date received 没有验证过, 这条是防御。
"""
from __future__ import annotations

# resolveInbox(acc, wantName): wantName 是 Companion 的文件夹角色名
# (Inbox / Drafts / Sent / Trash / Junk) 或员工给的字面名。
AS_RESOLVE_INBOX = """
on resolveInbox(acc, wantName)
    set candidates to {wantName}
    if wantName is "Inbox" then
        set candidates to {"INBOX", "Inbox", "收件箱", "受信箱"}
    else if wantName is "Sent" then
        set candidates to {"Sent", "Sent Messages", "Sent Mail", "已发送", "已发送邮件", "已发邮件", "已发送信件", "送信済み"}
    else if wantName is "Drafts" then
        set candidates to {"Drafts", "草稿", "草稿箱", "下書き"}
    else if wantName is "Trash" then
        set candidates to {"Trash", "Deleted Messages", "Bin", "已删除", "已删除邮件", "废纸篓", "ゴミ箱"}
    else if wantName is "Junk" then
        set candidates to {"Junk", "Junk Mail", "Spam", "垃圾邮件", "迷惑メール"}
    end if
    -- 1. top level, by name
    repeat with cand in candidates
        tell application "Mail"
            try
                return mailbox (cand as string) of acc
            end try
        end tell
    end repeat
    -- 2. any mailbox of the account whose own name matches (Gmail nests Sent/Trash under [Gmail])
    tell application "Mail"
        try
            set allBoxes to mailboxes of acc
            set boxNames to name of mailboxes of acc
            repeat with cand in candidates
                set k to 0
                repeat with nm in boxNames
                    set k to k + 1
                    if (nm as string) is (cand as string) then return item k of allBoxes
                end repeat
            end repeat
        end try
        -- 3. one level down, by name
        try
            repeat with parentBox in (mailboxes of acc)
                repeat with cand in candidates
                    try
                        set childBox to mailbox (cand as string) of parentBox
                        get name of childBox
                        return childBox
                    end try
                end repeat
            end repeat
        end try
    end tell
    -- 4. literal name (old behaviour: raises the usual error when nothing matches)
    tell application "Mail"
        return mailbox wantName of acc
    end tell
end resolveInbox
"""

# findMessage(acc, targetIdStr) → message 或 missing value。三条路见模块说明。
# scanById 只扫列表会显示的那几个文件夹: Gmail 的「所有邮件」动辄几万封,
# 不在兜底里整个拉一遍 id。
AS_FIND_MESSAGE = """
on findMessage(acc, targetIdStr)
    try
        set targetIdNum to (targetIdStr as integer)
    on error
        set targetIdNum to missing value
    end try
    tell application "Mail"
        set boxes to mailboxes of acc
    end tell
    if targetIdNum is not missing value then
        -- 1. whose filter (historical fast path)
        repeat with mb in boxes
            try
                tell application "Mail" to set m to (first message of mb whose id is targetIdNum)
                return m
            end try
        end repeat
        -- 2. by-id specifier, the form Mail uses in its own message references
        repeat with mb in boxes
            try
                tell application "Mail"
                    set m to «class mssg» id targetIdNum of mb
                    if (id of m) is targetIdNum then return m
                end tell
            end try
        end repeat
    end if
    -- 3. the folders Companion lists, enumerated exactly the way the list does
    repeat with folderName in {"Inbox", "Drafts", "Sent", "Trash", "Junk"}
        try
            set m to my scanById(my resolveInbox(acc, folderName as string), targetIdStr)
            if m is not missing value then return m
        end try
    end repeat
    return missing value
end findMessage

on scanById(mb, targetIdStr)
    tell application "Mail" to set idList to id of every message of mb
    set k to 0
    repeat with x in idList
        set k to k + 1
        if ((contents of x) as string) is targetIdStr then
            tell application "Mail"
                set m to message k of mb
                if ((id of m) as string) is targetIdStr then return m
            end tell
            return missing value
        end if
    end repeat
    return missing value
end scanById
"""

# msgDate(m): date received → date sent → ""。isoDate 自己拼 ISO-8601 (本地时区,
# 不带偏移), 因为 `date as string` 随系统语言变 (中文系统给 "2026年...")。
AS_DATES = """
on msgDate(m)
    tell application "Mail"
        try
            set d to date received of m
            if d is not missing value then return my isoDate(d)
        end try
        try
            set d to date sent of m
            if d is not missing value then return my isoDate(d)
        end try
    end tell
    return ""
end msgDate

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
