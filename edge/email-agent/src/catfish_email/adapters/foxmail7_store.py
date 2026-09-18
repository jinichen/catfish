"""Foxmail 7.2 存储布局 —— 目录约定 + LSTG 索引。

# 为什么单独一个模块 (9/18)
=============================
Foxmail 7.2 跟我们原先实现参照的 6.x **存储模型不一样**, 不是"格式变了"::

    6.x:  <folder>.box  = 数据文件, 邮件正文连续拼接, 14-byte header 分隔
    7.2:  Mails/<桶>/<桶>/<id>  = 单封邮件, 文件名纯数字, **没有扩展名**
          Boxes/<name>.box      = 只是 id 列表, magic 'LSTG', 不含任何正文
          Boxes/mId_bId.map     = mail id → box id 映射

后果是一天内五个同族 bug: Storage 路径认不出 (``FMStorage.list`` 不在配置后缀
白名单)、账号目录认不出 (只认 ``Mail`` 不认 ``Mails``)、``Boxes`` 被当成文件夹名、
``.box`` 里找不到 FOXM 于是逐字节试探刷了四千行日志、而真正的 5671 封邮件因为
没有 ``.box``/``.eml`` 后缀从头到尾没被扫到。

这个模块只管 7.x 的目录约定, 让 adapter 那边保持干净。

# 两条自校验原则
================
上面五个 bug 的共同成因是"把某一版的布局写死当判据"。这里反过来做:

  ① **邮件清单靠遍历, 不靠 id 算路径。** 实测 ``id → Mails/{id%32}/{id//32%32}/{id}``
     成立, 但桶数 32 是这一版的实现细节, 下一版改了我们就又瞎了。遍历目录拿到
     ``id → 路径`` 的真实映射, 天然跟着版本走。
  ② **索引解码要拿文件系统对答案。** LSTG 头长度不去逆向, 而是把几个候选偏移
     都解一遍 uint32 数组, 看哪个解出来的 id 命中真实文件最多; 命中率不达标就
     判定"读不懂这个索引", 全部退回收件箱 —— 宁可文件夹分得糙, 不能邮件不显示。

# 文件夹语义
============
``Boxes`` 下的名字不全是文件夹。实测一个账号有::

    all.box sent.box draft.box spam.box          ← 文件夹 (all = 全集)
    unread.box replied.box forward.box topmost.box headonly.box wait.box
    scrollTip.box                                 ← 标记位, 不是文件夹

**没有 inbox.box** —— 收件箱是"全集减去已发送/草稿/垃圾"。``unread.box`` 顺带
给了已读状态, 7.2 的邮件文件本身不带 flags。
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("catfish_email.adapters.foxmail7_store")

#: 7.x 账号目录下的邮件根目录名
MAILS_DIR = "Mails"
#: 7.x 账号目录下的索引目录名
BOXES_DIR = "Boxes"

#: 认定"这是个 Foxmail 7.x 账号目录"需要同时出现的子目录
_ACCOUNT_MARKERS = (MAILS_DIR, BOXES_DIR)

#: ``Boxes`` 里代表真实文件夹的 .box → 统一文件夹名
_FOLDER_BOXES = {
    "sent": "Sent",
    "draft": "Drafts",
    "drafts": "Drafts",
    "trash": "Trash",
    "deleted": "Trash",
    "spam": "Junk",
    "junk": "Junk",
}
#: 代表标记位而不是文件夹的 .box —— 不参与文件夹归属
_FLAG_BOXES = frozenset(
    {"all", "unread", "replied", "forward", "topmost", "headonly", "wait", "scrolltip"}
)
#: 没被任何文件夹 box 认领的邮件归这里
DEFAULT_FOLDER = "Inbox"

#: LSTG 头长度的候选值。0x20 是实测值 (头第 8-11 字节也写着 0x20),
#: 其余是常见变体; 最终选哪个由命中率决定, 不由这个顺序决定。
_LSTG_HEADER_CANDIDATES = (32, 16, 12, 8, 4)
#: 解出来的 id 至少这么高比例命中真实文件, 才采信这次解码
_MIN_INDEX_HIT_RATE = 0.5
#: 命中率再高, 至少也要有这么多条才算数 (避免 1/1 = 100% 的假象)
_MIN_INDEX_HITS = 3


def is_foxmail7_account(root: Path) -> bool:
    """``root`` 看起来是不是 Foxmail 7.x 的单账号目录。"""
    try:
        names = {child.name.casefold() for child in root.iterdir() if child.is_dir()}
    except OSError:
        return False
    return all(marker.casefold() in names for marker in _ACCOUNT_MARKERS)


def mail_files(account_dir: Path) -> dict[int, Path]:
    """遍历 ``Mails/`` 拿 ``mail id → 文件路径``。

    判据是"文件名是纯数字", 不是扩展名 —— 7.2 的邮件文件没有扩展名。
    """
    root = account_dir / MAILS_DIR
    if not root.is_dir():
        return {}
    found: dict[int, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.isdigit():
            found[int(name)] = path
    logger.info("foxmail7: %s 下找到 %d 封邮件文件", root, len(found))
    return found


def _decode_ids(data: bytes, header_size: int) -> list[int]:
    """从 ``header_size`` 起把剩余字节当 uint32 LE 数组解开。"""
    body = data[header_size:]
    count = len(body) // 4
    if count <= 0:
        return []
    return [i for i in struct.unpack(f"<{count}I", body[: count * 4]) if i]


def detect_header_size(path: Path, known_ids: set[int]) -> int | None:
    """用一份**大**索引定出 LSTG 头长度; 定不出返回 None。

    不逆向头部结构, 而是把候选头长度各解一遍, 用 ``known_ids`` (遍历
    ``Mails/`` 得到的真实 id) 当答案, 取命中最多的那个。

    只在大索引上做这件事, 因为小索引 (``sent.box`` 可能就一条) 无论怎么解都
    凑不够样本, 1/1 = 100% 也说明不了问题。定出来的头长度再套给同账号的其余
    索引 —— 同一个 Foxmail 写出来的文件, 头长度必然一致。
    """
    try:
        data = path.read_bytes()
    except OSError as error:
        logger.warning("foxmail7: 读不了索引 %s: %s", path, error)
        return None

    best: int | None = None
    best_hits = 0
    for header_size in _LSTG_HEADER_CANDIDATES:
        if len(data) <= header_size:
            continue
        ids = _decode_ids(data, header_size)
        hits = sum(1 for i in ids if i in known_ids)
        if hits > best_hits and ids and hits / len(ids) >= _MIN_INDEX_HIT_RATE:
            best, best_hits = header_size, hits

    if best is None or best_hits < _MIN_INDEX_HITS:
        logger.info(
            "foxmail7: 从 %s 定不出 LSTG 头长度 (最高命中 %d), 文件夹归属退回默认",
            path.name, best_hits,
        )
        return None
    logger.info("foxmail7: LSTG 头长度 = %d (由 %s 定出, 命中 %d)", best, path.name, best_hits)
    return best


def read_lstg_ids(path: Path, known_ids: set[int], header_size: int | None = None) -> list[int] | None:
    """按已定出的头长度读一份 LSTG 索引, 只返回命中真实文件的 id。

    ``header_size`` 省略时自己先定一次 —— 单元测试和单文件排查方便。
    """
    if header_size is None:
        header_size = detect_header_size(path, known_ids)
        if header_size is None:
            return None
    try:
        data = path.read_bytes()
    except OSError as error:
        logger.warning("foxmail7: 读不了索引 %s: %s", path, error)
        return None
    return [i for i in _decode_ids(data, header_size) if i in known_ids]


@dataclass
class Foxmail7Account:
    """一个 7.x 账号目录的解析结果。"""

    account_dir: Path
    files: dict[int, Path] = field(default_factory=dict)
    folder_of: dict[int, str] = field(default_factory=dict)
    unread: set[int] = field(default_factory=set)
    index_understood: bool = False

    def folder_for(self, path: Path) -> str:
        """某个邮件文件属于哪个文件夹。索引读不懂就一律收件箱。"""
        name = path.name
        if not name.isdigit():
            return DEFAULT_FOLDER
        return self.folder_of.get(int(name), DEFAULT_FOLDER)

    def is_read(self, path: Path) -> bool | None:
        """已读吗? 索引读不懂时返回 None 表示"不知道"。"""
        if not self.index_understood or not path.name.isdigit():
            return None
        return int(path.name) not in self.unread


def load_account(account_dir: Path) -> Foxmail7Account:
    """把一个 7.x 账号目录读成 :class:`Foxmail7Account`。"""
    acc = Foxmail7Account(account_dir=account_dir, files=mail_files(account_dir))
    boxes = account_dir / BOXES_DIR
    if not acc.files or not boxes.is_dir():
        return acc

    known = set(acc.files)
    all_boxes = sorted(boxes.glob("*.box"))
    if not all_boxes:
        return acc

    # 用最大的那份索引 (通常是 all.box) 定头长度, 再套给其余索引。
    probe = max(all_boxes, key=lambda path: path.stat().st_size if path.is_file() else 0)
    header_size = detect_header_size(probe, known)
    if header_size is None:
        logger.info("foxmail7: %s 索引读不懂, 全部邮件归 %s", account_dir.name, DEFAULT_FOLDER)
        return acc

    for box in all_boxes:
        stem = box.stem.casefold()
        folder = _FOLDER_BOXES.get(stem)
        if folder is None and stem != "unread":
            continue  # 标记位 (all/unread 之外) 或不认识的 box, 不参与归属
        ids = read_lstg_ids(box, known, header_size)
        if not ids:
            continue
        if stem == "unread":
            acc.unread.update(ids)
        else:
            for mail_id in ids:
                acc.folder_of[mail_id] = folder

    acc.index_understood = True
    understood = len(all_boxes)
    logger.info(
        "foxmail7: %s 索引解开 %d 份, 归属 %d 封, 未读 %d 封, 其余归 %s",
        account_dir.name, understood, len(acc.folder_of), len(acc.unread), DEFAULT_FOLDER,
    )
    return acc
