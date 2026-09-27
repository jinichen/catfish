r"""只从服务器上彻底删掉**指定的这几封** —— 服务器有没有 UIDPLUS 都能做 (9/27)。

# 为什么要单独一个文件

保留策略 (归档后多久从服务器删) 9/21 就写好了, 但删除这一步从来没被调用过 ——
界面上选「一周后删除」, 服务器上一封都不会删。卡住它的是一个真问题:

  电信邮箱 (imap.chinatelecom.cn) 没有 UIDPLUS, 只有裸 EXPUNGE。
  裸 EXPUNGE 清的是**整个文件夹里所有打了 \Deleted 的邮件** —— 包括员工在
  Foxmail / Outlook 里标了删除、还没清空的那些, 以及鲶鱼里删了的原件
  (delete_message 在没有 UIDPLUS 时只打标记)。直接 EXPUNGE 等于把别人
  标记待删的邮件一起永久清掉, 所以当初选了"只打标记不清", 容量因此腾不出来。

# 做法: 清之前把别人的标记先摘掉, 清完再挂回去

  1. 给要删的这几封打 \Deleted
  2. 没有 UIDPLUS: `UID SEARCH DELETED` 找出文件夹里其余带 \Deleted 的,
     先摘掉它们的标记 → EXPUNGE → (无论成败) 把标记原样挂回去
     有 UIDPLUS: `UID EXPUNGE` 只清这几封, 不用绕
  3. 再问一次服务器这几封还在不在; 还在的 (EXPUNGE 失败等) 把 \Deleted 摘掉 ——
     否则下一轮同步会把"打了删除标记"的当成员工删了, 连本地档案登记一起抹掉

不可逆的只有 EXPUNGE 那一下, 它清掉的是那一刻带 \Deleted 的邮件:
我们这几封, 加上摘标记和 EXPUNGE 之间那几毫秒里恰好被别的客户端标记删除的
(那本来就是要删的)。中途出错的最坏结果是别人的待删标记没挂回去 —— 那几封
信**还在**, 只是不再显示为待删, 不丢任何东西。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("catfish_email.adapters.imap_purge")

_DELETED = r"(\Deleted)"
_UID_NUM = re.compile(rb"\d+")


def _uid_set(uids) -> str:
    return ",".join(sorted(uids, key=int))


def _search(conn, *criteria) -> set[str]:
    typ, data = conn.uid("search", None, *criteria)
    if typ != "OK":
        raise RuntimeError(f"SEARCH {' '.join(criteria)} → {typ}")
    found: set[str] = set()
    for chunk in data or []:
        if isinstance(chunk, bytes):
            found.update(m.decode() for m in _UID_NUM.findall(chunk))
    return found


def _store(conn, uids, op: str) -> None:
    if not uids:
        return
    typ, _ = conn.uid("store", _uid_set(uids), op, _DELETED)
    if typ != "OK":
        raise RuntimeError(f"STORE {op} \\Deleted → {typ}")


def expunge_only(conn, uids: list[str], *, has_uidplus: bool) -> set[str]:
    """把这几封从当前 (可写 SELECT 的) 文件夹里彻底删掉。返回确认已不在的 UID。"""
    targets = {str(u) for u in uids}
    if not targets:
        return set()
    _store(conn, targets, "+FLAGS")
    try:
        if has_uidplus:
            conn.uid("expunge", _uid_set(targets))
        else:
            others = _search(conn, "DELETED") - targets
            _store(conn, others, "-FLAGS")
            try:
                conn.expunge()
            finally:
                try:
                    _store(conn, others, "+FLAGS")
                except Exception as error:  # noqa: BLE001
                    logger.error(
                        "别处标记待删的 %d 封没能挂回删除标记 (信都还在): %s",
                        len(others), error,
                    )
    finally:
        still = _search(conn, "UID", _uid_set(targets)) & targets
        if still:
            # 没删掉的撤回标记, 免得下一轮同步把它们当成"员工删了"
            _store(conn, still, "-FLAGS")
    return targets - still
