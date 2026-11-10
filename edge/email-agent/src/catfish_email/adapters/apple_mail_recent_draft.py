"""草稿箱里最近存的那封 (11/11) —— 给「小鲶存了草稿 → 跳到草稿箱」兜底。

# 为什么要有

跳转靠 last_draft.py 记的那一笔: 草稿经 `catfish-email draft` 存, 存成功就记。11/10 鸿波:
「邮件落草稿箱后没有跳到草稿邮件」。查那一回合的日志: 小鲶没有调起草工具, 而是按它自己 9 月
记下的一条技能 (那时起草工具还不支持附件), 用 execute_code 跑 AppleScript 直接在 Mail 里建了
草稿 —— 不经过这里, 那一笔就没有, Companion 不知道要跳。

小鲶走哪条路我们管不住 (别的员工机上它也可能学出类似的绕路)。所以回合结束时没有那一笔,
Companion 就来问一句: Mail 的草稿箱里, 刚才这几分钟有没有新存的?

# 怎么判「刚存的」

在 Mail 里用 `(current date) - (date sent of m)` 算出「多少秒之前」再拿出来。9/27 那版是把
Date 头拿到 Python 里比, 时区 / 格式一点偏差就判错; 两个时间都在 Mail 自己手里相减就没有这个
问题。实测草稿的 date sent / date received 都有, 就是存的那一刻。
"""
from __future__ import annotations

from typing import Callable

from .apple_mail_as_handlers import AS_RESOLVE_INBOX
from .base import EmailAdapterError

_AS_RECENT_DRAFT = """
tell application "Mail"
    set maxAge to {MAX_AGE}
    set FS to (character id 31)
    set nowD to current date
    set bestAge to -1
    set bestOut to ""
    repeat with acc in accounts
        try
            set mbx to my resolveInbox(acc, "Drafts")
            repeat with m in (messages of mbx)
                set age to -1000000
                try
                    set age to (nowD - (date sent of m)) as integer
                end try
                if age is -1000000 then
                    try
                        set age to (nowD - (date received of m)) as integer
                    end try
                end if
                -- a few seconds in the "future" is clock rounding, still counts
                if age > -60 and age <= maxAge then
                    if age < 0 then set age to 0
                    if bestAge < 0 or age < bestAge then
                        set bestAge to age
                        set bestOut to (name of acc) & FS & ((id of m) as string) & FS & (age as string)
                    end if
                end if
            end repeat
        end try
    end repeat
    return bestOut
end tell
""" + AS_RESOLVE_INBOX


def newest_draft(run: Callable[[str], str], max_age_seconds: int) -> tuple[str, str, int] | None:
    """各账号草稿箱里 max_age_seconds 秒内存的、最新的一封 → (账号名, Mail 里的编号, 多少秒之前)。没有 → None。"""
    out = run(_AS_RECENT_DRAFT.replace("{MAX_AGE}", str(max(1, int(max_age_seconds))))).strip("\r\n")
    if not out:
        return None
    parts = out.split("\x1f")
    if len(parts) != 3 or not parts[1].strip():
        raise EmailAdapterError(f"recent_draft: 意外的返回 ({out[:80]!r})")
    try:
        age = int(parts[2])
    except ValueError:
        age = 0
    return parts[0], parts[1].strip(), age
