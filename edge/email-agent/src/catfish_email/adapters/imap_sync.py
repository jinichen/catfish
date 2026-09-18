"""IMAP 增量同步 —— 列清单走本地索引, 只取变化的。

9/18 从 imap_mail.py 拆出来 (那个文件补完写操作之后 1018 行, 越过仓库的
800 行红线)。拆这一块是因为它是**索引层**, 跟下面那层"怎么跟服务器说话"
是两件事: 基类 ImapAdapter 每次都问服务器, 这里先对账再查本地。
"""
from __future__ import annotations

import logging

from ..adapters.base import ListFilter, Message
from .imap_folders import folder_matches, folder_role
from .imap_mail import _FLAGS_IN_FETCH, _UID_IN_FETCH, ImapAdapter

#: 索引每个文件夹最多留多少封 (取最新的)。索引服务的是"看收件箱", 不是全文
#: 归档 —— 五年的邮箱全收进来, 首次同步的代价用户等不起。
SYNC_INDEX_CAP = 2000
#: 一个 FETCH 里塞多少封。往返数 = 变更数 / 这个值。
SYNC_FETCH_BATCH = 200

logger = logging.getLogger("catfish_email.adapters.imap_sync")


#
# 服务器**没有** CONDSTORE / QRESYNC (真机 CAPABILITY 实测), 所以拿不到
# "自从上次以来变了什么"。但不需要 —— IMAP 的两个性质就够了:
#
#   · UID 在一个 UIDVALIDITY 周期内**永不改变、永不复用**
#   · `UID FETCH <range> (FLAGS)` 不带正文, 很便宜
#
# 于是: 先廉价地拉一遍全部 (uid, flags), 跟索引里的对账, 只对新增的和 flags
# 变了的去取邮件头。57 封的邮箱一次对账就是一个 FETCH FLAGS 往返。
#
# fingerprint 用 flags: UID 不变, 会变的只有已读/标记这些。


def sync_key(folder_raw: str, uidvalidity: str, uid: str) -> str:
    """索引里的主键。**必须带 UIDVALIDITY** —— 服务器重建邮箱后 UID 会从头
    发放, 不带的话新旧两封会撞在同一个 key 上, 而且内容完全不相干。"""
    return f"imap:{folder_raw}:{uidvalidity}:{uid}"


def _parse_sync_key(key: str) -> tuple[str, str, str]:
    """`imap:<folder>:<uidvalidity>:<uid>` → 三段。

    folder 名里可能有冒号 (IMAP 分隔符通常是 / 或 . , 但没规定不能是 :),
    所以从**右边**切两刀, 剩下的都算 folder。
    """
    body = key[len("imap:"):] if key.startswith("imap:") else key
    head, _, uid = body.rpartition(":")
    folder_raw, _, uidvalidity = head.rpartition(":")
    return folder_raw, uidvalidity, uid


class ImapSyncAdapter(ImapAdapter):
    """带本地索引的 IMAP。列清单走索引, 只有新增/变更的才上服务器取。

    跟基类的分工: 基类是"直连, 每次都问服务器", 这个是"先对账再查本地"。
    ``--client imap`` 走的是这个 (inbox.py), 基类现在只作为它的实现基础 ——
    读单封、搜索、文件夹列表这些非热路径原样继承, 不经过索引。

    **正文不进索引**: 索引里只有列表要显示的那些字段 + 一段 snippet。读一封
    完整邮件仍旧直连服务器。理由是员工邮件正文落盘的面越小越好, 而列表页
    本来也不显示正文。
    """

    name = "imap_sync"

    def sync_folder(self, folder_raw: str, role: str) -> "object":
        """把一个文件夹对账进索引, 返回 ReconcileStats。

        三步, 每步的成本都要算清楚:

          ① 一个往返, 问全部 (uid, flags)  —— 不带正文, 便宜
          ② 一个往返, 问"要取哪些"          —— 纯本地 SQLite
          ③ ⌈变更数/SYNC_FETCH_BATCH⌉ 个往返 —— 只有这步贵

        稳定期 ③ 是 0 个往返 (什么都没变)。**绝不能退化成"一封一个往返"** ——
        首次同步 SYNC_INDEX_CAP 封, 跨广域网按 100ms 算那是三分多钟。
        """
        from .. import index_store  # noqa: PLC0415 — 避免 adapter 装载时连 DB

        conn = self._connect()
        uidvalidity = self._select(conn, folder_raw)

        # ① 廉价地问一遍"现在有哪些, 各自什么状态"
        typ, data = conn.uid("search", None, "ALL")
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        # UID 递增 → 尾部就是最新的。索引只留最近这批: 它服务的是"看收件箱",
        # 不是全文归档。五年的邮箱全收进来, 首次同步的代价用户等不起。
        uids = uids[-SYNC_INDEX_CAP:]
        pairs: list[tuple[str, str]] = []
        if uids:
            typ, flag_data = conn.uid("fetch", b",".join(uids), "(UID FLAGS)")
            if typ == "OK":
                for line in flag_data or []:
                    raw = line if isinstance(line, bytes) else (line[0] if isinstance(line, tuple) else b"")
                    uid_match = _UID_IN_FETCH.search(raw)
                    if uid_match is None:
                        continue
                    flags_match = _FLAGS_IN_FETCH.search(raw)
                    flags = flags_match.group(1).decode("ascii", "replace") if flags_match else ""
                    # 打了 \Deleted 的当成"没了" —— 不进 pairs, 于是 reconcile
                    # 会把索引里的那行删掉。我们删邮件时故意不 EXPUNGE, 原件
                    # 还留在文件夹里 (见 delete_message), 这里不滤就删不掉。
                    if r"\Deleted" in flags:
                        continue
                    pairs.append(
                        (sync_key(folder_raw, uidvalidity, uid_match.group(1).decode()), flags)
                    )

        assert self.config is not None
        db = index_store.open_index()
        try:
            # ② 先问清楚要取哪些, 再**成批**取 —— 别让 reconcile 的 parse
            #    回调变成一封一个往返。
            wanted = index_store.changed_keys(
                db, account=self.config.user, folder=role, items=pairs
            )
            fetched: dict[str, Message] = {}
            for i in range(0, len(wanted), SYNC_FETCH_BATCH):
                batch = wanted[i : i + SYNC_FETCH_BATCH]
                uid_bytes = [_parse_sync_key(k)[2].encode() for k in batch]
                for remote in self._fetch_uids(
                    conn, folder_raw, role, uidvalidity, uid_bytes, True
                ):
                    key = sync_key(folder_raw, remote.uidvalidity, remote.uid)
                    fetched[key] = self._to_message(remote, full=False)

            # ③ 取不到的那些 (SEARCH 之后、FETCH 之前被删了) 让 reconcile 记
            #    成 error 跳过 —— 不进索引, 下一轮自然就不在 pairs 里了。
            stats = index_store.reconcile(
                db, account=self.config.user, folder=role,
                items=pairs, parse=lambda key: fetched[key],
            )
        finally:
            db.close()
        logger.info(
            "imap_sync[%s/%s]: 服务器 %d · 没变 %d · 取 %d · 删 %d · %dms",
            self.config.user, role, stats.scanned, stats.unchanged,
            stats.parsed, stats.removed, stats.elapsed_ms,
        )
        return stats

    def sync_all(self) -> dict[str, object]:
        """所有认识的文件夹各对账一次。返回 {角色: ReconcileStats}。"""
        out: dict[str, object] = {}
        for raw, decoded in self.folders():
            role = folder_role(decoded)
            try:
                out[role] = self.sync_folder(raw, role)
            except Exception as error:  # noqa: BLE001 — 一个文件夹坏不拖垮其余
                logger.warning("imap_sync: 文件夹 %s 同步失败: %s", decoded, error)
        return out

    def check_new_mail(self, *, account: str | None = None) -> None:
        """界面上的「收信」: 立刻把所有文件夹对一遍账。

        基类那个是空操作 (直连, 列表本来就是现问服务器的)。带索引之后就有了
        实质内容 —— 列表读的是索引, 「收信」就该是"现在就去把索引刷新了",
        而且是**全部文件夹**, 不只当前在看的那个。
        """
        self.sync_all()

    def list_messages(self, filt: ListFilter) -> list[Message]:
        """先对账, 再从本地索引出结果。

        对账失败**不让列表挂掉** —— 索引里还有上一轮的数据, 给旧数据远好过
        给一个空收件箱 (今天在 Foxmail 上看够了空收件箱有多难排查)。
        """
        from .. import index_store  # noqa: PLC0415

        try:
            for raw, decoded in self.folders():
                role = folder_role(decoded)
                if folder_matches(role, filt.folder):
                    self.sync_folder(raw, role)
        except Exception as error:  # noqa: BLE001
            logger.warning("imap_sync: 对账失败, 用索引里的旧数据: %s", error)

        assert self.config is not None
        db = index_store.open_index()
        try:
            rows: list[Message] = []
            for raw, decoded in self.folders() if filt.folder == "*" else [(None, filt.folder)]:
                role = folder_role(decoded) if raw is not None else filt.folder
                rows.extend(index_store.query_messages(
                    db, account=self.config.user, folder=role,
                    unread_only=filt.unread_only, limit=filt.limit,
                ))
        finally:
            db.close()
        rows.sort(key=lambda m: m.date, reverse=True)
        return rows[: max(filt.limit, 0)]
