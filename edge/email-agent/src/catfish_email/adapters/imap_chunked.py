"""大邮件分段取 (10/14 Windows: 带大附件的「Fw: 业务外包框架协议」两封都读不出来)。

原来读一封信、归档一批信, 都是一条 ``FETCH (BODY.PEEK[])`` 把整封 (连附件) 一口气要回来。
现场两封带大附件的信都在这一步被服务器中途断开 (``imaplib.IMAP4.abort: socket error: EOF``),
小信正常。归档也一样: 一批 20 封里只要夹一封大的, 整批断掉, 那封永远进不了本地档案 ——
于是"服务器读不到就看档案"的兜底对它也没用。

现在先问大小 (``RFC822.SIZE``, 只回一个数字), 不超过 CHUNK 的照旧一次取; 超过的按
``BODY.PEEK[]<起点.长度>`` 一段段取再拼。每条命令只传 2MB, 不会再因为一次传太多被掐。
仍然是 PEEK —— 不会把员工没读过的信标成已读。拼出来的是服务器原样的字节 (归档要核哈希)。
"""
from __future__ import annotations

import imaplib
import logging
import re

logger = logging.getLogger(__name__)

#: 一段多大。2MB: 正文 + 小附件的信一次就取完, 几十 MB 的附件拆成十几次往返
CHUNK = 2 * 1024 * 1024
#: 归档一批里小信合起来最多取多少再发下一条命令
BATCH_BYTES = 8 * 1024 * 1024

_UID = re.compile(rb"UID (\d+)")
_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")


def sizes(conn: imaplib.IMAP4_SSL, uids: list[bytes]) -> dict[str, tuple[int, bytes]]:
    """{uid: (字节数, flags)}。服务器没给大小的不在结果里 (调用方按"小信"处理)。"""
    if not uids:
        return {}
    typ, data = conn.uid("fetch", b",".join(uids), "(UID FLAGS RFC822.SIZE)")
    if typ != "OK":
        return {}
    out: dict[str, tuple[int, bytes]] = {}
    for item in data or []:
        line = item[0] if isinstance(item, tuple) else item
        if not isinstance(line, bytes):
            continue
        u, s = _UID.search(line), _SIZE.search(line)
        if u and s:
            f = _FLAGS.search(line)
            out[u.group(1).decode("ascii")] = (int(s.group(1)), f.group(1) if f else b"")
    return out


def fetch_partial(conn: imaplib.IMAP4_SSL, uid: str, size: int) -> bytes | None:
    """一段段取整封原文。哪一段失败返回 None (调用方当作这次没取到)。"""
    buf = bytearray()
    while len(buf) < size:
        typ, data = conn.uid("fetch", uid, f"(UID BODY.PEEK[]<{len(buf)}.{CHUNK}>)")
        if typ != "OK":
            return None
        piece = next(
            (bytes(it[1]) for it in data or [] if isinstance(it, tuple) and len(it) > 1
             and isinstance(it[1], (bytes, bytearray))),
            None,
        )
        if not piece:
            break
        buf += piece
        if len(piece) < CHUNK:
            break
    if len(buf) < size:
        logger.warning("分段取邮件 uid=%s 只拿到 %d/%d 字节", uid, len(buf), size)
        return None
    return bytes(buf)


def fetch_whole(conn: imaplib.IMAP4_SSL, uid: str) -> list:
    """读一封信: 返回跟 ``conn.uid("fetch", uid, "(UID FLAGS BODY.PEEK[])")[1]`` 同形状的
    data, 直接喂给 ImapAdapter._parse_fetch。大信走分段。"""
    info = sizes(conn, [uid.encode()]).get(uid)
    if info is None or info[0] <= CHUNK:
        typ, data = conn.uid("fetch", uid, "(UID FLAGS BODY.PEEK[])")
        return data if typ == "OK" else []
    size, flags = info
    logger.info("邮件 uid=%s 有 %.1fMB, 分段取", uid, size / 1024 / 1024)
    raw = fetch_partial(conn, uid, size)
    if raw is None:
        return []
    prefix = b"1 (UID %s FLAGS (%s) BODY[] {%d}" % (uid.encode(), flags, len(raw))
    return [(prefix, raw), b")"]


def fetch_raw_many(conn: imaplib.IMAP4_SSL, uids: list[bytes]) -> dict[str, bytes]:
    """归档用: 成批取原文 {uid: raw}。小信凑批 (每批不超过 BATCH_BYTES), 大信单独分段。"""
    if not uids:
        return {}
    info = sizes(conn, uids)
    out: dict[str, bytes] = {}
    batch: list[bytes] = []
    batch_bytes = 0

    def flush() -> None:
        nonlocal batch, batch_bytes
        if not batch:
            return
        typ, data = conn.uid("fetch", b",".join(batch), "(UID BODY.PEEK[])")
        batch, batch_bytes = [], 0
        if typ != "OK":
            logger.warning("归档取原文失败, 这批跳过 (下一轮会重试)")
            return
        for item in data or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            prefix, raw = item[0], item[1]
            if not isinstance(prefix, bytes) or not isinstance(raw, (bytes, bytearray)):
                continue
            m = _UID.search(prefix)
            if m:
                out[m.group(1).decode("ascii")] = bytes(raw)

    for uid in uids:
        size = info.get(uid.decode("ascii"), (0, b""))[0]
        if size > CHUNK:
            raw = fetch_partial(conn, uid.decode("ascii"), size)
            if raw is not None:
                out[uid.decode("ascii")] = raw
            continue
        if batch and batch_bytes + size > BATCH_BYTES:
            flush()
        batch.append(uid)
        batch_bytes += size
    flush()
    return out
