"""邮件本地索引 —— list/search 的读侧真源 (8/21 治本)。

# 为什么要有这一层

8/21 之前, EmailTab 每次挂载 (切 tab 就卸载重挂, App.tsx:268 条件渲染) 都要:

    Rust spawn `catfish-email list`      ← Python 解释器冷启动
      └ Mail.app 在跑 → AppleScript      ← 每封 8 个字段 = 8 次 Apple Event IPC
        500 封 × 8 字段 × 4 账号 ≈ 1.6 万次 IPC, Sent 200 封再来一遍

切一次 tab = 把全部邮件从头重抽一遍。这就是「切回邮件页要等很久」的真因。
同一个根源还烧过配额: 8/15 评级 effect 自触发, 83 分钟 2470 万 token ——
一切都是现抓现算, 没有任何东西落地。

# 设计

**一张 SQLite 表, 按 (source_path, mtime, size) 对账, 只解析新增/变更的文件。**

    对账:  rglob 枚举 .emlx + stat           ← 只有 readdir 成本, 无解析
    增量:  (path, mtime, size) 没变 → 跳过    ← 解析成本只在文件首次/变更时发生
    查询:  SELECT ... ORDER BY date DESC      ← 毫秒级

emlx 是 Mail.app 的磁盘真源格式, 纯文件 IO 可读 (adapters/apple_mail_emlx.py
已有全套解析)。AppleScript 保留给**写侧** (发送/草稿/标已读) —— DESIGN.md 里
AS-first 的理由全是写侧的 ("dictionary 完整"), 读列表没有非 AS 不可的理由。

# 已知限制 (写清楚, 别让下一个人重新踩)

1. **已读状态可能滞后**: emlx trailer plist 的 read flag 由 Mail.app 惰性回写。
   员工在 Mail.app 里读了一封, 鲶鱼里可能短时间仍显示未读。真源在 Mail.app 的
   Envelope Index (私有 schema, 故意不碰)。mtime 变化时会重解析拿到新 flag。
2. **单账号单文件夹一张表**: folder 是索引键的一部分, Inbox/Sent 分开对账。
3. **删除**: Mail.app 删邮件 → emlx 文件消失 → 对账时索引行同步删除。

# 索引文件

~/.catfish/email_index.db (CATFISH_HOME 优先, 跟 picker_state 等同一套约定)。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from .adapters.base import Message

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# 9/20: 从「镜子」改成「档案馆」
# ══════════════════════════════════════════════════════════════════
#
# 上面那段设计写于 8/21, 前提是"索引是纯缓存, 丢了重扫一遍就有"。对 emlx
# 成立 —— 真源是 Mail.app 的文件, 我们只是建目录。对 IMAP **不成立**:
#
#   · 正文从来没落过地, 点开一封信就得连服务器 FETCH
#   · reconcile 的规则是"没出现在 items 里的 key 就删掉", 于是服务器上一删,
#     本地连那 300 字 snippet 都跟着没
#   · 公司邮箱有容量上限、会自动清理 —— 而越老的信越可能已经不在服务器上,
#     偏偏越老的信越是要沉淀的那些
#
# 定位改成档案馆之后, 真源换了个地方:
#
#     ~/.catfish/mail_archive/<account>/<YYYY-MM>/<id>.eml   ← 真源
#     ~/.catfish/email_index.db                              ← 仍然是纯缓存
#
# **这个分工是刻意的, 别合并。** 下面 open_index 遇到 schema 不匹配会 DROP
# 重建, 那句话之所以还能成立, 全靠 .eml 是自描述的 —— 原始 RFC822 里有
# Message-ID、日期、收发件人、正文、附件, 索引里的每一列都能从它重新算出来。
# 一旦哪天往索引里塞了"只有索引里有"的东西 (比如员工打的标签), DROP 重建
# 就变成数据丢失, 那时候必须先写迁移再改 schema。
_SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    -- 9/18: 从 (source_path, mtime, size) 泛化成 (source_key, fingerprint)。
    --
    -- 原来写死了"来源是文件"这个假设: 主键是 .emlx 绝对路径, 变更判据是
    -- stat 的 mtime+size。IMAP 两样都没有 —— 它的身份是 UID, 变更判据是 FLAGS。
    --
    -- source_key  文件型: 绝对路径
    --             IMAP:  imap:<folder>:<uidvalidity>:<uid>
    --                    (必须带 UIDVALIDITY —— 服务器重建邮箱后旧 UID 指向
    --                     完全不相干的邮件)
    -- fingerprint 文件型: "mtime:size"
    --             IMAP:  flags 串 (UID 不变, 只有已读/标记会变)
    source_key    TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    account       TEXT NOT NULL,
    folder        TEXT NOT NULL,
    msg_id        TEXT NOT NULL,      -- adapter 的稳定 id ('account|emlx:path')
    subject       TEXT NOT NULL DEFAULT '',
    sender        TEXT NOT NULL DEFAULT '',
    recipients    TEXT NOT NULL DEFAULT '[]',   -- JSON array
    date          TEXT NOT NULL DEFAULT '',     -- ISO-8601 UTC
    is_read       INTEGER NOT NULL DEFAULT 0,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    snippet       TEXT NOT NULL DEFAULT '',
    message_id    TEXT,               -- RFC822 Message-ID (thread 三件套)
    in_reply_to   TEXT,
    refs          TEXT,               -- RFC822 References (refs: references 是保留字)
    indexed_at    REAL NOT NULL,

    -- ── 档案馆 (9/20) ──────────────────────────────────────
    -- archive_path   相对 archive_root 的路径; NULL = 这封还没归档
    -- archive_bytes  .eml 字节数, 校验用
    -- archive_sha256 .eml 内容哈希, 校验用
    -- archived_at    写完 .eml 的时刻
    -- verified_at    **独立回读**校验通过的时刻。NULL 表示没校验过 ——
    --                服务器端清理只认这一列, 绝不认 archived_at。
    --                写入返回 OK 不等于盘上那份是对的: 截断、编码错、
    --                写一半断电, 每一种都会让 archived_at 有值而内容是坏的。
    -- on_server      0 = 服务器上已经没有这封了 (被清理/被员工删)。
    --                注意这跟"删索引行"是两回事: 档案馆里行永远留着。
    archive_path   TEXT,
    archive_bytes  INTEGER,
    archive_sha256 TEXT,
    archived_at    REAL,
    verified_at    REAL,
    on_server      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_messages_list
    ON messages (account, folder, date DESC);
CREATE INDEX IF NOT EXISTS idx_messages_unread
    ON messages (account, folder, is_read, date DESC);
-- 待归档队列: 后台渐进归档每轮问"还有哪些没落地"
CREATE INDEX IF NOT EXISTS idx_messages_unarchived
    ON messages (account, archived_at) WHERE archive_path IS NULL;
-- 服务器端清理的候选集: 已校验 且 服务器上还在
CREATE INDEX IF NOT EXISTS idx_messages_purgeable
    ON messages (verified_at) WHERE verified_at IS NOT NULL AND on_server = 1;
"""


def index_db_path() -> Path:
    """~/.catfish/email_index.db。CATFISH_HOME 优先 (对齐 picker_state 那套)。"""
    env = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(env).expanduser() if env else Path.home() / ".catfish"
    return base / "email_index.db"


def open_index(db_path: Path | None = None) -> sqlite3.Connection:
    p = db_path or index_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(_SCHEMA)
    # schema 版本钉住: 变了就重建 (索引是纯缓存, 重建无代价, 别写迁移)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    elif row[0] != str(_SCHEMA_VERSION):
        logger.info("email_index schema %s → %s, 重建", row[0], _SCHEMA_VERSION)
        conn.executescript("DROP TABLE messages; DROP TABLE meta;")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    return conn


@dataclass
class ReconcileStats:
    """一次对账的账目 —— 测试和日志都靠它, 别省。"""

    scanned: int = 0      # 磁盘上枚举到的文件数
    unchanged: int = 0    # (path,mtime,size) 没变, 跳过解析
    parsed: int = 0       # 真正解析了的 (新增 + 变更)
    # 来源里没了的条数。**注意这不一定等于"删了几行"** —— on_missing="keep"
    # (IMAP 档案馆) 下它们只是被标成 on_server=0, 行和 .eml 一个没少。
    # 名字保留是因为调用方和测试都在用, 但日志别再写成"删 N 封"。
    removed: int = 0
    errors: int = 0       # 解析失败 (跳过, 不进索引)
    elapsed_ms: int = 0


def _known_fingerprints(
    conn: sqlite3.Connection, *, account: str, folder: str
) -> dict[str, str]:
    return dict(
        conn.execute(
            "SELECT source_key, fingerprint FROM messages WHERE account=? AND folder=?",
            (account, folder),
        )
    )


def changed_keys(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    items: Iterable[tuple[str, str]],
) -> list[str]:
    """哪些 key 需要重新解析。**纯 SQLite, 不碰网络、不碰磁盘。**

    reconcile 自己也会算一遍同样的东西 (两者共用 _known_fingerprints), 这里
    单独暴露出来是给**远程来源批量取**用的:

        文件型来源 parse 一次 = 读一个本地文件, 一封一次无所谓。
        IMAP parse 一次 = 一个网络往返。首次同步两千封就是两千个往返,
        跨广域网按 100ms 算要三分多钟, 而这两千封本可以十次 FETCH 取完。

    所以远程 adapter 的用法是: 先 changed_keys 问"要取哪些" → 批量取进内存
    → 再 reconcile, parse 从内存里拿。多一次 SELECT, 省掉 N-1 个往返。
    """
    known = _known_fingerprints(conn, account=account, folder=folder)
    return [key for key, fingerprint in items if known.get(key) != fingerprint]


def reconcile(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    items: Iterable[tuple[str, str]],
    parse: Callable[[str], Message],
    on_missing: str = "delete",
) -> ReconcileStats:
    """把一组 (source_key, fingerprint) 对账进索引。**只解析新增/变更的。**

    Args:
        items: 这个 account+folder 下当前存在的全部条目。
               fingerprint 变了就重新解析, 没变就跳过 —— 增量的全部秘密。
               **没出现在这里的 key 会被当成"没了"删掉**, 所以调用方必须给全,
               不能只给一页。
        parse: 按 source_key 取一封邮件。生产传偏函数, 测试传假的 ——
               解析次数是增量正确性的**直接判据**。

    9/18: 从"文件路径 + mtime/size"泛化过来。原来的形状把"来源是文件"焊死在
    表结构里, IMAP 接不上 —— 它的身份是 UID, 变更判据是 FLAGS。
    """
    t0 = time.monotonic()
    stats = ReconcileStats()

    known = _known_fingerprints(conn, account=account, folder=folder)
    seen: set[str] = set()

    for key, fingerprint in items:
        stats.scanned += 1
        seen.add(key)
        if known.get(key) == fingerprint:
            stats.unchanged += 1
            continue
        try:
            m = parse(key)
        except Exception as e:  # noqa: BLE001 — 单条坏不拖垮整次对账
            stats.errors += 1
            logger.debug("email_index: 解析失败跳过 %s: %s", key, e)
            continue
        conn.execute(
            """INSERT INTO messages (source_key, fingerprint, account, folder,
                   msg_id, subject, sender, recipients, date, is_read,
                   has_attachments, snippet, message_id, in_reply_to, refs, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET
                   fingerprint=excluded.fingerprint,
                   subject=excluded.subject, sender=excluded.sender,
                   recipients=excluded.recipients, date=excluded.date,
                   is_read=excluded.is_read,
                   has_attachments=excluded.has_attachments,
                   snippet=excluded.snippet, message_id=excluded.message_id,
                   in_reply_to=excluded.in_reply_to, refs=excluded.refs,
                   indexed_at=excluded.indexed_at""",
            (
                key, fingerprint, account, folder,
                m.id, m.subject, m.sender, json.dumps(list(m.recipients)),
                m.date, int(m.is_read), int(m.has_attachments),
                (m.body_text or "")[:300], m.message_id, m.in_reply_to,
                m.references, time.time(),
            ),
        )
        stats.parsed += 1

    # ── 来源里没了的怎么办 —— 这一步决定这张表是镜子还是档案馆 ──────
    #
    # on_missing="delete"  (emlx / eml-dir): 删索引行。
    #     那些 .eml/.emlx 文件是**员工自己管的**, 他删掉或挪走了文件, 意思
    #     就是不想要了。我们是那个目录的镜子, 跟着走是对的。
    #
    # on_missing="keep"    (IMAP 档案馆): 只标 on_server=0, 行和 .eml 都留着。
    #     服务器上没了**不等于**这封信不存在了 —— 公司邮箱有容量上限、会
    #     自动清理, 而越老的信越可能已经被清掉, 偏偏越老的信越是要沉淀的
    #     那些。档案馆的全部意义就在于活过这次清理。
    #
    # ⚠ 必须是显式参数, 不能在这里按 adapter 名字猜。猜错任何一边都是
    #   静默的数据损失: 对 IMAP 猜成 delete = 档案被服务器的清理策略同步
    #   删光; 对 emlx 猜成 keep = 员工删了邮件鲶鱼里还在, 列表永远在涨。
    #   这两个方向的错都不会报错, 只会在几个月后被发现。
    if on_missing not in ("delete", "keep"):
        raise ValueError(f"on_missing 只能是 'delete' 或 'keep', 收到 {on_missing!r}")

    gone = [k for k in known if k not in seen]
    if gone:
        if on_missing == "delete":
            conn.executemany(
                "DELETE FROM messages WHERE source_key=?", [(g,) for g in gone]
            )
        else:
            conn.executemany(
                "UPDATE messages SET on_server=0 WHERE source_key=?",
                [(g,) for g in gone],
            )
        stats.removed = len(gone)

    # ── 邮箱重建之后的去重 ────────────────────────────────────────
    #
    # UIDVALIDITY 一变, 同一封信会拿到全新的 source_key, 而旧那行在
    # on_missing="keep" 下**不会被删**, 只是标了 on_server=0。于是列表里
    # 同一封信出现两次。
    #
    # 这不是边角情况: "邮箱容量满了 → 清理/重建" 正是最常触发 UIDVALIDITY
    # 变化的场景, 而那恰恰也是这套档案馆存在的理由。不处理的话, 档案馆第一次
    # 真正派上用场的那天就开始出重复。
    #
    # 判据用 RFC822 Message-ID —— 它是这封信跨服务器、跨 UIDVALIDITY 唯一
    # 稳定的身份。空的不参与去重 (少数客户端不发 Message-ID), 那种情况留重复
    # 也好过误删。
    #
    # ⚠ 只删**还没归档**的旧行 (archive_path IS NULL)。已经落地 .eml 的旧行
    #   删掉等于扔掉档案本体 —— 那时候正确做法是把 archive 指针挪到新行上,
    #   而不是删。归档写完之前这里恒为 NULL, 所以现在这条是安全的; 归档上线
    #   时必须回来处理 (见 task「主键从 UID 换成 Message-ID」)。
    if on_missing == "keep":
        conn.execute(
            """DELETE FROM messages
                WHERE account = ?
                  AND folder = ?
                  AND on_server = 0
                  AND archive_path IS NULL
                  AND message_id IS NOT NULL AND message_id != ''
                  AND message_id IN (
                      SELECT message_id FROM messages
                       WHERE account = ? AND folder = ? AND on_server = 1
                         AND message_id IS NOT NULL AND message_id != ''
                  )""",
            (account, folder, account, folder),
        )

    conn.commit()
    stats.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return stats


def file_items(paths: Iterable[Path]) -> Iterator[tuple[str, str]]:
    """文件型来源的 (source_key, fingerprint) 生成器。

    stat 不到的**直接不产出** —— 那样它既不会被解析, 也不会算进 seen,
    于是会被当成"没了"删掉。这正是想要的: 枚举到但 stat 不到, 说明刚被删。
    """
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        yield str(path), f"{st.st_mtime}:{st.st_size}"


def reconcile_files(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    files: Iterable[Path],
    parse: Callable[[Path], Message],
) -> ReconcileStats:
    """文件型来源的便利封装 —— 调用方不用自己拼 fingerprint。"""
    return reconcile(
        conn, account=account, folder=folder,
        items=file_items(files), parse=lambda key: parse(Path(key)),
    )


def query_messages(
    conn: sqlite3.Connection,
    *,
    account: str,
    folder: str,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Message]:
    """从索引出 list 结果, 形状与 adapter.list_messages 一致 (Message 序列)。"""
    sql = (
        "SELECT msg_id, account, folder, subject, sender, recipients, date, "
        "is_read, has_attachments, snippet, message_id, in_reply_to, refs, "
        # 档案状态跟着列表一起出来 —— 界面要靠它区分"只在本地档案"
        "verified_at, on_server "
        "FROM messages WHERE account=? AND folder=?"
    )
    args: list = [account, folder]
    if unread_only:
        sql += " AND is_read=0"
    sql += " ORDER BY date DESC LIMIT ?"
    args.append(max(1, limit))

    out: list[Message] = []
    for r in conn.execute(sql, args):
        out.append(Message(
            id=r[0], account=r[1], folder=r[2], subject=r[3], sender=r[4],
            recipients=tuple(json.loads(r[5] or "[]")), date=r[6],
            is_read=bool(r[7]), has_attachments=bool(r[8]), body_text=r[9],
            message_id=r[10], in_reply_to=r[11], references=r[12],
            # archived 用的是 verified_at 不是 archived_at —— 界面上说"已归档"
            # 意味着"服务器没了也还在", 而只有独立回读核对过才敢这么说。
            archived=r[13] is not None, on_server=bool(r[14]),
        ))
    return out


# ═══════════════════════════════════════════════════════════════════
# 档案队列 (9/21)
# ═══════════════════════════════════════════════════════════════════
#
# 归档是**渐进**的: 每轮同步顺手落地一批, 没落完的留到下一轮。进度不另外
# 记状态 —— "archive_path IS NULL" 就是待办队列本身。
#
# 这样断点续传是免费的: 关掉 App、断网、装机重启, 下一轮接着从队列头取。
# 要是另起一张 progress 表, 它和 messages 就有了两份真相, 而它们迟早不一致。


def pending_archive(
    conn: sqlite3.Connection, *, account: str, folder: str, limit: int
) -> list[tuple[str, str, str]]:
    """还没落地的。返回 [(source_key, date, message_id)]，**最老的优先**。

    为什么最老优先: 归档的意义是抢在服务器清理之前。服务器的保留策略总是
    先清老的, 所以老的那头最危险。新邮件反正还在服务器上, 晚一轮无所谓。
    """
    rows = conn.execute(
        "SELECT source_key, date, COALESCE(message_id, '') FROM messages "
        "WHERE account=? AND folder=? AND archive_path IS NULL "
        "ORDER BY date ASC LIMIT ?",
        (account, folder, max(1, limit)),
    )
    return [(r[0], r[1], r[2]) for r in rows]


def mark_archived(
    conn: sqlite3.Connection, source_key: str, *,
    path: str, size: int, sha256: str,
) -> None:
    """落地了。**注意这里不置 verified_at** —— 那要等独立回读核对过。

    两者分开不是多此一举: 写入返回成功只说明 write() 没抛异常, 不说明盘上
    那份是对的。服务器端清理只认 verified_at, 所以这两列绝不能在同一步写。
    """
    conn.execute(
        "UPDATE messages SET archive_path=?, archive_bytes=?, archive_sha256=?, "
        "archived_at=?, verified_at=NULL WHERE source_key=?",
        (path, size, sha256, time.time(), source_key),
    )
    conn.commit()


def mark_verified(conn: sqlite3.Connection, source_key: str) -> None:
    """独立回读核对通过。这一列是服务器端删除的唯一依据。"""
    conn.execute(
        "UPDATE messages SET verified_at=? WHERE source_key=?",
        (time.time(), source_key),
    )
    conn.commit()


def clear_archive(conn: sqlite3.Connection, source_key: str) -> None:
    """校验没过 → 把档案登记整条抹掉, 让它回到待办队列。

    不能只清 verified_at 留着 archive_path: 那样它既不在队列里 (有 path),
    又永远不会被认为已校验, 于是**悄悄地谁也不管**了。盘上那个坏文件下一轮
    会被原子写覆盖掉。
    """
    conn.execute(
        "UPDATE messages SET archive_path=NULL, archive_bytes=NULL, "
        "archive_sha256=NULL, archived_at=NULL, verified_at=NULL "
        "WHERE source_key=?",
        (source_key,),
    )
    conn.commit()


def archive_stats(conn: sqlite3.Connection, *, account: str) -> dict[str, int]:
    """界面上要显示的进度。一次查询, 别在渲染时逐行算。"""
    row = conn.execute(
        "SELECT COUNT(*), "
        "       SUM(CASE WHEN archive_path IS NOT NULL THEN 1 ELSE 0 END), "
        "       SUM(CASE WHEN verified_at  IS NOT NULL THEN 1 ELSE 0 END), "
        "       SUM(CASE WHEN on_server = 0 THEN 1 ELSE 0 END), "
        "       COALESCE(SUM(archive_bytes), 0) "
        "  FROM messages WHERE account=?",
        (account,),
    ).fetchone()
    return {
        "total": row[0] or 0,
        "archived": row[1] or 0,
        "verified": row[2] or 0,
        "only_local": row[3] or 0,
        "bytes": row[4] or 0,
    }


def purgeable(
    conn: sqlite3.Connection, *, account: str, folder: str,
    older_than: float, limit: int,
) -> list[str]:
    """可以从服务器上删掉的 source_key。

    三个条件缺一不可:

      verified_at IS NOT NULL   **独立回读核对过**。绝不看 archived_at ——
                                写入返回成功只说明 write() 没抛异常。
      verified_at <= older_than 保留期满了
      on_server = 1             服务器上还在 (已经没了的不用再删)

    最老的先删 —— 跟归档同一个方向: 老邮件占的容量先释放, 而且它们最可能
    已经被员工忘掉了。
    """
    rows = conn.execute(
        "SELECT source_key FROM messages "
        "WHERE account=? AND folder=? AND on_server=1 "
        "  AND verified_at IS NOT NULL AND verified_at <= ? "
        "ORDER BY verified_at ASC LIMIT ?",
        (account, folder, older_than, max(1, limit)),
    )
    return [r[0] for r in rows]


def mark_off_server(conn: sqlite3.Connection, source_key: str) -> None:
    """服务器上那份没了, 本地档案照旧。"""
    conn.execute(
        "UPDATE messages SET on_server=0 WHERE source_key=?", (source_key,)
    )
    conn.commit()
