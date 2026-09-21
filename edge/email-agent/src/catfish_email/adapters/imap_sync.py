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

#: 每轮同步顺手归档多少封。
#:
#: 比 SYNC_FETCH_BATCH 小得多是**故意的**: 归档取的是整封带附件的原文,
#: 一封可能几 MB, 200 封一个 FETCH 会让服务器和内存都很难受, 而且这一批
#: 没传完中间断了就全白费。20 封一批, 断了最多重来 20 封。
#:
#: 归档不赶时间 —— 它是后台的、渐进的, 每轮同步推进一点。赶时间的是列表,
#: 那条路一个字节正文都不取。
ARCHIVE_BATCH = 20

#: 每轮从服务器清理多少封。比归档还小 —— 这是**不可逆**操作, 慢比快好。
#: 出了问题, 每轮 10 封给人留的反应时间比每轮 200 封多一个数量级。
PURGE_BATCH = 10

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

    # ⚠ 必须跟基类一样是 "imap" —— **id 前缀就是 adapter 名**, 各 CLI 命令
    # (read / delete / mark-read / send / attachment) 都按 `id.split("|")[0]`
    # 找 adapter。这里写 "imap_sync" 的话, `imap|INBOX|1|8418` 这条 id 永远
    # 匹配不上它, 会落到"逐个 try", 被递给 Apple Mail 去解 —— 删错邮件或者
    # 一句莫名其妙的报错。
    #
    # 9/18 我原本为了日志好认写成了 imap_sync, 当时没暴露是因为 Companion
    # 强制了单一 client 把它盖住了。见 test_adapter_contract 里那条不变量。
    name = "imap"

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
        # 索引只留最近这批: 它服务的是"看收件箱", 不是全文归档。五年的邮箱
        # 全收进来, 首次同步的代价用户等不起。
        #
        # ⚠ 先排序再切尾。原来直接 uids[-SYNC_INDEX_CAP:], 注释写的是
        #   "UID 递增 → 尾部就是最新的" —— 那是**服务器的惯例, 不是 RFC 的
        #   保证**。乱序时切到的是任意 2000 封, 而不是最新的 2000 封;
        #   症状是收件箱里缺最近的邮件, 极难往这儿想。
        #   (9/21 归档那边同款假设被测试夹具当场逮到, 顺手把这里也钉了。)
        uids = sorted(uids, key=int)[-SYNC_INDEX_CAP:]
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
            # on_missing="keep": 服务器上没了的**不删索引行**, 只标 on_server=0。
            #
            # 9/20 定位从镜子改成档案馆。公司邮箱有容量上限、会自动清理, 而
            # 越老的信越可能已经被清掉 —— 偏偏越老的信越是要沉淀的那些。
            # 这里如果沿用默认的 "delete", 档案就会跟着服务器的清理策略一起
            # 消失, 那这套东西就白做了。
            #
            # 反过来 emlx / eml-dir 仍然是 "delete": 那些文件是员工自己管的。
            stats = index_store.reconcile(
                db, account=self.config.user, folder=role,
                items=pairs, parse=lambda key: fetched[key],
                on_missing="keep",
            )
        finally:
            db.close()
        logger.info(
            "imap_sync[%s/%s]: 服务器 %d · 没变 %d · 取 %d · 服务器上已无 %d · %dms",
            self.config.user, role, stats.scanned, stats.unchanged,
            stats.parsed, stats.removed, stats.elapsed_ms,
        )
        return stats

    def archive_folder(self, folder_raw: str, role: str) -> dict[str, int]:
        """把这个文件夹里还没落地的邮件归档一批。返回这轮的账目。

        # 顺序: 先定起点, 再落地, 最后校验

        ① 这个文件夹没设过起点 → 拿当前最大 UID 当起点, 这轮**一封都不归档**。
           "从使用的第一天开始"就是这个意思: 启用之前已经在的不回填。
        ② 队列里取一批 (最老优先 —— 归档要抢在服务器清理之前, 而服务器
           总是先清老的)
        ③ 一个 FETCH 取原始字节 → 原子写盘 → 记 archive_path
        ④ **重新打开文件读一遍**核对 → 记 verified_at

        ③ 和 ④ 分开、而且 ④ 真的重读磁盘, 是整个档案馆最要紧的一条:
        服务器端清理只认 verified_at。写入返回成功只说明 write() 没抛异常。

        校验没过 → clear_archive 把登记整条抹掉, 让它回到队列。只清
        verified_at 而留着 archive_path 的话, 它既不在队列里又永远不算已校验,
        **于是悄悄地谁也不管了**。
        """
        from .. import archive_store, index_store  # noqa: PLC0415

        assert self.config is not None
        account = self.config.user
        root = archive_store.archive_root()
        conn = self._connect()
        uidvalidity = self._select(conn, folder_raw)

        # ① 起点
        marks = archive_store.read_cutoff(root, account)
        mark = marks.get(folder_raw)
        if not mark or mark.get("uidvalidity") != str(uidvalidity):
            typ, data = conn.uid("search", None, "ALL")
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            # ⚠ max() 而不是 uids[-1]。**RFC 3501 不保证 SEARCH 的结果有序** ——
            #   多数服务器返回升序, 但那是惯例不是规范, 而且测试夹具里的响应
            #   (照真机抄的) 就是 8418 在 8417 前面。
            #
            #   取错的后果不是差一个数: 起点被设成某个任意 UID, 比它大的历史
            #   邮件全部进队列 —— 也就是**回填历史**, 正是"从第一天开始"要防
            #   的那件事。而且一声不响。
            top = max((int(u) for u in uids), default=0)
            marks[folder_raw] = {"uidvalidity": str(uidvalidity), "uid": top}
            archive_store.write_cutoff(root, account, marks)
            logger.info(
                "归档起点已设 [%s/%s]: UIDVALIDITY=%s UID>%d 的才归档 "
                "(之前已有的 %d 封不回填)",
                account, role, uidvalidity, top, len(uids),
            )
            return {"archived": 0, "verified": 0, "failed": 0, "cutoff_set": 1}

        # ② 队列 —— 只要水位线之上的
        stats = {"archived": 0, "verified": 0, "failed": 0, "cutoff_set": 0}
        db = index_store.open_index()
        try:
            todo = [
                (key, date_iso, mid)
                for key, date_iso, mid in index_store.pending_archive(
                    db, account=account, folder=role, limit=ARCHIVE_BATCH * 4
                )
                if archive_store.above_cutoff(
                    marks, folder_raw, uidvalidity, _parse_sync_key(key)[2]
                )
            ][:ARCHIVE_BATCH]
            if not todo:
                return stats

            # ③ 一个往返取这一批的原文
            raws = self._fetch_raw_bytes(conn, [_parse_sync_key(k)[2].encode() for k, _, _ in todo])

            for key, date_iso, message_id in todo:
                uid = _parse_sync_key(key)[2]
                raw = raws.get(uid)
                if not raw:
                    # 取不到不算失败: 可能刚被别的客户端删了, 也可能这批没传全。
                    # 它还在队列里, 下一轮自然重试。
                    continue
                # Message-ID 缺失的用 source_key 兜底 —— 少数客户端不发这个头,
                # 而没有文件名就等于不归档, 那个代价大得多。
                ident = message_id or key
                rel = archive_store.relpath_for(
                    account=account, ident=ident, date_iso=date_iso
                )
                try:
                    size, digest = archive_store.write_message(root, rel, raw)
                except OSError as e:
                    logger.warning("归档写盘失败 %s: %s", rel, e)
                    stats["failed"] += 1
                    continue
                index_store.mark_archived(db, key, path=rel, size=size, sha256=digest)
                stats["archived"] += 1

                # ④ 独立回读
                problem = archive_store.verify_message(
                    root, rel, expect_bytes=size, expect_sha256=digest
                )
                if problem:
                    logger.error("归档校验没过, 已退回队列: %s", problem)
                    index_store.clear_archive(db, key)
                    stats["failed"] += 1
                    stats["archived"] -= 1
                    continue
                index_store.mark_verified(db, key)
                stats["verified"] += 1
        finally:
            db.close()

        if stats["archived"] or stats["failed"]:
            logger.info(
                "归档 [%s/%s]: 落地 %d · 校验通过 %d · 失败 %d",
                account, role, stats["archived"], stats["verified"], stats["failed"],
            )
        return stats

    def purge_folder(self, folder_raw: str, role: str) -> dict[str, int]:
        """保留期满的, 从服务器上删掉。返回这轮的账目。

        # 这跟员工手动删邮件是两条不同的路

        手动删 (delete_message): COPY 一份到「已删除」再打 \Deleted ——
        员工可能后悔, 要留退路。

        到期清理 (这里): **不 COPY**。副本同样占容量, 而腾容量正是这个功能
        存在的理由 —— 留一份等于删完更胖。本地档案就是那条退路, 而且它已经
        独立回读核对过了。

        # 判据只认 verified_at

        绝不看 archived_at。写入返回成功只说明 write() 没抛异常, 盘上那份
        到底对不对只有重读核对过才知道。这里判错一次, 邮件是真的没了。

        # 没有 UIDPLUS 时容量释放不了, 而且必须说出来

        裸 EXPUNGE 会清掉**整个文件夹里所有打了 \Deleted 的邮件** —— 包括
        员工在 Foxmail/Outlook 里标了删除还没压缩的那些, 不可恢复。宁可
        不释放容量也不能干这个。

        但"没释放"必须让员工知道: 他开了这个功能就是为了腾空间, 结果空间
        没腾出来而界面一切正常, 那是最坏的一种。返回值里带 needs_expunge,
        界面照着它说话。
        """
        from .. import index_store  # noqa: PLC0415

        assert self.config is not None
        stats = {"purged": 0, "flagged": 0, "needs_expunge": 0}
        window = self.config.retention_seconds()
        if window is None:
            return stats           # never —— 什么都不做

        import time  # noqa: PLC0415

        conn = self._connect()
        self._select(conn, folder_raw, writable=True)
        has_uidplus = self._has_capability("UIDPLUS")

        db = index_store.open_index()
        try:
            keys = index_store.purgeable(
                db, account=self.config.user, folder=role,
                older_than=time.time() - window, limit=PURGE_BATCH,
            )
            for key in keys:
                uid = _parse_sync_key(key)[2]
                typ, _ = conn.uid("store", uid, "+FLAGS", r"(\Deleted)")
                if typ != "OK":
                    logger.warning("标记删除失败 uid=%s, 跳过", uid)
                    continue
                if has_uidplus:
                    conn.uid("expunge", uid)      # 只清这一封
                    stats["purged"] += 1
                else:
                    stats["flagged"] += 1
                index_store.mark_off_server(db, key)
        finally:
            db.close()

        if stats["flagged"]:
            stats["needs_expunge"] = stats["flagged"]
            logger.warning(
                "[%s/%s] %d 封已标记删除, 但这台服务器没有 UIDPLUS —— "
                "不能只清这几封, 所以没有执行 EXPUNGE。**容量还没释放**, "
                "要等服务器自己压缩或员工在邮件客户端里清空已删除。",
                self.config.user, role, stats["flagged"],
            )
        if stats["purged"]:
            logger.info(
                "[%s/%s] 从服务器清理 %d 封 (本地档案已校验保留)",
                self.config.user, role, stats["purged"],
            )
        return stats

    def sync_all(self, *, archive: bool = True) -> dict[str, object]:
        """所有认识的文件夹各对账一次, 顺手归档一批。返回 {角色: ReconcileStats}。

        # 为什么归档挂在这儿, 不挂在 sync_folder 上

        sync_folder 是 list_messages 的热路径 —— 员工每次切到邮件页都会走。
        归档要取整封带附件的原文, 挂在那里等于**每次打开邮件页都卡一下**,
        而列表本来是这套索引的全部意义 (8/21 治的就是"切 tab 要等很久")。

        sync_all 只有两个调用方: 界面上的「收信」和后台轮询。两个都是
        "现在去跟服务器对一遍"的语义, 慢一点是预期之内的。每轮推进
        ARCHIVE_BATCH 封, 渐进地把档案补齐。

        archive=False 留给不想付这个代价的调用方 (比如只想刷新列表)。
        """
        out: dict[str, object] = {}
        for raw, decoded in self.folders():
            role = folder_role(decoded)
            try:
                out[role] = self.sync_folder(raw, role)
            except Exception as error:  # noqa: BLE001 — 一个文件夹坏不拖垮其余
                logger.warning("imap_sync: 文件夹 %s 同步失败: %s", decoded, error)
                continue
            if not archive:
                continue
            try:
                self.archive_folder(raw, role)
            except Exception as error:  # noqa: BLE001
                # 归档挂了**不能拖垮同步**。索引是员工马上要用的, 档案是
                # 后台慢慢补的 —— 前者的可用性优先级高得多。
                logger.warning("imap_sync: 文件夹 %s 归档失败: %s", decoded, error)
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
