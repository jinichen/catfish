"""邮件档案 —— 原始 .eml 落地，这一层是**真源**。

# 跟 index_store 的分工

    ~/.catfish/mail_archive/<account>/<YYYY-MM>/<name>.eml   ← 真源, 不可重建
    ~/.catfish/email_index.db                                ← 纯缓存, 随时可重建

index_store 顶上那句"schema 变了就 DROP 重建"之所以还能成立, 全靠这边的
.eml 是**自描述**的: 原始 RFC822 里有 Message-ID、日期、收发件人、正文、
附件, 索引里每一列都能从它重新算出来。这个分工是刻意的, 别合并。

# 为什么是文件而不是 SQLite BLOB

  · 原始 RFC822 自描述, 附件天然在里面, 不用二次决定"附件存哪"
  · 几个 GB 的 BLOB 会拖慢索引的每一次查询, 文件不会
  · 现成的 eml-dir 适配器能直接读这个目录 —— 白送一条完全离线的浏览路径
  · 别的工具 (Thunderbird / 取证 / grep) 也能读, 不被我们的 schema 绑架

# 这个文件里每一处防的是什么

写邮件到磁盘看着简单, 但它是档案馆的**唯一**一层。下面每条都有具体的坏结果:

  原子写        写一半断电 → 留下半封 .eml。而索引那边 archived_at 已经有值,
                于是它被当成"已归档", 保留策略到期就把服务器上那封删了。
                半封 + 服务器已删 = 那封信没了。
  文件名消毒    Message-ID 是**远端可控**的字符串。`<../../../../etc/passwd>`
                这种进来就是路径穿越。邮件头里塞什么都不犯法。
  长度上限      Message-ID 没有长度限制, 但文件系统有 (255 字节)。超长直接
                OSError, 而且是在归档跑了两小时之后才炸。
  独立回读校验  写完立刻用内存里那份算哈希 = 验证它等于它自己, 什么都没验。
                必须**重新打开文件读一遍**才能发现截断、编码错、磁盘坏块。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

#: 起点文件名。放在 <account>/ 下面, 跟这个账号的 .eml 在一起。
#:
#: ⚠ **不能只存在索引里。** index_store 遇到 schema 不匹配会 DROP 重建 ——
#:   那条设计之所以成立, 前提是索引里每一列都能从 .eml 重算。起点不能,
#:   它是"我们什么时候开始管这个邮箱"这个事实, 磁盘上任何一封邮件里都没有。
#:
#:   丢了之后两条路都很难看:
#:     当成"从今天开始" → 中间那段永远没人归档, 而且没有任何迹象
#:     当成"没设过"     → 整个邮箱重灌一遍, 用户莫名其妙等一夜
#:
#:   所以跟 .eml 放在一起。档案目录自描述, 索引照样随时可重建。
_CUTOFF_NAME = ".archive-since.json"

#: 单个文件名最长多少字符 (留余量给 .eml 后缀和哈希后缀)。
#: ext4/APFS/NTFS 都是 255 **字节**, 中文一个字 3 字节, 所以按字节算。
_NAME_MAX_BYTES = 120

#: 文件名里允许原样保留的字符。其余一律换成 '-'。
#: 刻意收得很紧 —— 这是远端可控的字符串, 白名单比黑名单安全。
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def archive_root() -> Path:
    """~/.catfish/mail_archive (CATFISH_HOME 优先, 跟 index_store 同一套约定)。"""
    env = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(env).expanduser() if env else Path.home() / ".catfish"
    return base / "mail_archive"


def safe_name(raw: str) -> str:
    """把 Message-ID 之类的远端字符串变成安全的文件名主干。

    ⚠ 输入是**邮件头**, 也就是发件人完全可控。这里的每一步都不是洁癖:

        "<../../../../etc/passwd>"  → 路径穿越
        "a/b/c"                     → 意外建出多层目录
        "\\x00foo"                  → 某些文件系统上截断
        300 个汉字                   → OSError: File name too long

    做法是白名单 + 长度上限 + 冲突后缀:

      1. NFKC 归一, 去掉尖括号和空白
      2. 非 [A-Za-z0-9._-] 一律换 '-'  ← 路径分隔符、空字节、控制字符全在此列
      3. 掐掉开头的 '.' —— 不制造隐藏文件, 也堵死 '..'
      4. 按**字节**截断到 _NAME_MAX_BYTES
      5. 末尾接上原始串的 sha256 前 12 位 —— 消毒和截断都会制造冲突
         (a/b 和 a-b 消毒后同名; 两个超长 ID 前 120 字节可能一样),
         而两封不同的信落到同一个文件名 = 后到的覆盖先到的, 悄无声息

    第 5 步不能省: 前四步都是**多对一**的映射。
    """
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:12]

    stem = unicodedata.normalize("NFKC", raw).strip().strip("<>").strip()
    stem = _SAFE_CHARS.sub("-", stem)
    # ⚠ 必须把 '.' 和 '-' **一起**剥, 不能分两步。
    #   第一版写的是 .lstrip(".") 然后 .strip("-"), 测试当场逮到:
    #       "../../secret" → sub → "..-..-secret"
    #                      → lstrip(".") → "-..-secret"
    #                      → strip("-")  → "..-secret"   ← 前导点又回来了
    #   剥掉 '-' 之后把后面的 '.' 重新暴露在开头, 于是隐藏文件/".." 那道防线
    #   在正好交替的输入上失效。合成一个字符集一次剥干净就没有这个顺序问题。
    stem = stem.strip(".-")

    encoded = stem.encode("utf-8", "ignore")[:_NAME_MAX_BYTES]
    # 截断可能切断多字节字符, errors="ignore" 把残字节丢掉;
    # 也可能把尾巴切成 '.' 或 '-', 所以这里再剥一次
    stem = encoded.decode("utf-8", "ignore").strip(".-")

    return f"{stem}-{digest}" if stem else digest


def relpath_for(*, account: str, ident: str, date_iso: str) -> str:
    """档案里的相对路径: ``<account>/<YYYY-MM>/<name>.eml``。

    按年月分目录不是为了好看 —— 一个目录几万个文件时, 很多工具 (Finder、
    ls、备份软件) 会明显变慢甚至卡死。五年邮箱按月分开, 每个目录几百个。

    date_iso 解析不出来就落 ``unknown/`` —— **绝不能因为日期坏了就不归档**,
    日期错顶多放错抽屉, 不归档是真丢信。
    """
    month = "unknown"
    if len(date_iso) >= 7 and date_iso[4] == "-":
        head = date_iso[:7]
        if head[:4].isdigit() and head[5:7].isdigit():
            month = head
    return f"{safe_name(account)}/{month}/{safe_name(ident)}.eml"


def write_message(root: Path, relpath: str, raw: bytes) -> tuple[int, str]:
    """把原始 RFC822 字节原子地写进档案。返回 (字节数, sha256)。

    **原子性是这里的全部要点。** 直接 open(path,'wb').write() 在写一半时断电/
    被杀, 磁盘上会留下半封 .eml。而调用方拿到返回值就会把 archived_at 写进
    索引 —— 那封信从此被当成"已归档", 保留策略到期就把服务器上那份删了。
    半封 + 服务器已删 = 信没了, 而且没有任何地方会报错。

    所以: 写临时文件 → fsync → 原子 rename。rename 在同一个文件系统上是
    原子的, 要么看到完整的旧文件(或没有), 要么看到完整的新文件。
    """
    dest = root / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")

    try:
        with open(tmp, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())      # 光 flush 只到内核缓冲, 断电照样丢
        os.replace(tmp, dest)          # 原子; Windows 上也能覆盖已存在的
    except BaseException:
        # 失败就别留垃圾 —— 残留的 .tmp 会被下一次 rglob 扫到, 当成邮件解析失败
        tmp.unlink(missing_ok=True)
        raise

    return len(raw), hashlib.sha256(raw).hexdigest()


def verify_message(root: Path, relpath: str, *, expect_bytes: int, expect_sha256: str) -> str:
    """**重新从磁盘读一遍**核对。通过返回 "", 否则返回一句人话。

    # 为什么必须重读

    写完顺手拿内存里那份 raw 算个哈希、跟刚才算的比一比 —— 那是在验证
    它等于它自己, 一个字节的磁盘问题都发现不了。截断、坏块、文件系统
    满了只写进去一半、被别的进程改了, 全都漏过去。

    而这个函数的返回值是**服务器端删除的唯一依据**。它说 OK, 那封信在
    服务器上就可能被删掉, 之后本地这份是世上唯一一份。所以它必须是一次
    真正独立的读取。

    # 为什么返回字符串而不是 bool

    校验不过的时候, 调用方要往界面和日志里写"为什么不过"。返 False 的话
    那句话就得在调用方现编, 而调用方不知道是大小不对还是哈希不对。
    降级必须说话, 说话就得有内容。
    """
    path = root / relpath
    if not path.exists():
        return f"档案文件不在: {relpath}"
    try:
        data = path.read_bytes()
    except OSError as e:
        return f"档案读不出来: {relpath} ({e})"

    if len(data) != expect_bytes:
        return (
            f"档案大小对不上: 盘上 {len(data)} 字节, 记录 {expect_bytes} 字节 "
            f"({relpath})"
        )
    actual = hashlib.sha256(data).hexdigest()
    if actual != expect_sha256:
        return (
            f"档案内容哈希对不上: 盘上 {actual[:16]}…, 记录 {expect_sha256[:16]}… "
            f"({relpath})"
        )
    return ""


# ═══════════════════════════════════════════════════════════════════
# 起点 —— "从使用的第一天开始"
# ═══════════════════════════════════════════════════════════════════
#
# 不回填历史邮件。启用那一刻记一条水位线, 之后新来的才归档。
#
# # 为什么用 UID 水位线而不是日期
#
# 三个候选:
#
#   Date 头        **发件人可控**。垃圾邮件写 Date: 2030 是家常便饭, 那封
#                  会永远在水位线之上; 写 1970 的则永远归不了档。
#   INTERNALDATE   服务器收到的时间, 可信, 但要多一次 FETCH, 而且跨时区/
#                  服务器时钟偏移都得处理。
#   UID            IMAP **保证**在一个 UIDVALIDITY 周期内单调递增、不复用。
#                  新到的信 UID 一定比启用时的最大 UID 大。
#
# UID 不需要任何时钟, 也不受发件人摆布, 判据就是一个整数比大小。
#
# # UIDVALIDITY 变了怎么办
#
# 水位线跟着失效 (UID 从头发放, 旧的数没有可比性)。这时候**重新取当前最大
# UID 当新起点**, 也就是继续"不管以前"。
#
# 换一个做法 —— 把水位线归零 —— 会在邮箱重建的那天把整个邮箱重灌一遍,
# 而邮箱重建往往正是因为容量满了在清理, 用户那天最不需要的就是这个。


def _cutoff_file(root: Path, account: str) -> Path:
    return root / safe_name(account) / _CUTOFF_NAME


def read_cutoff(root: Path, account: str) -> dict[str, dict]:
    """读起点。返回 {folder_raw: {"uidvalidity": str, "uid": int}}。

    文件不存在 / 读坏了都返回空 dict —— 调用方看到空就知道"还没设过起点",
    该去设一条。**绝不能因为读不出来就当成 uid=0**, 那等于从头重灌。
    """
    path = _cutoff_file(root, account)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        # 坏了要出声。静默返回 {} 的话, 调用方会以为"还没启用"然后重设起点,
        # 于是从今天开始 —— 中间那段就这么无声无息地漏了。
        logger.warning("档案起点文件读不出来 (%s): %s —— 当成还没设过起点", path, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("档案起点文件形状不对 (%s): 是 %s 不是 dict", path, type(data).__name__)
        return {}
    out: dict[str, dict] = {}
    for folder, mark in data.items():
        if isinstance(mark, dict) and "uid" in mark:
            try:
                out[folder] = {
                    "uidvalidity": str(mark.get("uidvalidity", "")),
                    "uid": int(mark["uid"]),
                }
            except (TypeError, ValueError):
                logger.warning("档案起点里 %s 那条坏了, 跳过: %r", folder, mark)
    return out


def write_cutoff(root: Path, account: str, marks: dict[str, dict]) -> None:
    """原子地写起点。理由跟 write_message 一样: 写一半的起点比没有更坏。"""
    path = _cutoff_file(root, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(marks, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def above_cutoff(
    marks: dict[str, dict], folder_raw: str, uidvalidity: str, uid: str | int
) -> bool:
    """这封信在水位线之上 (= 该归档) 吗？

    没设过这个文件夹的起点 → **False**。不是 True。

    这一条的方向很要紧: 拿不准的时候宁可不归档, 也不要把整个历史邮箱
    灌下来。漏归档是"少存了", 用户能看出来也能补; 误判成要归档是几小时
    的下载 + 几个 GB 磁盘, 而且发生在用户完全没预期的时候。

    UIDVALIDITY 对不上也返回 False —— 旧水位线跟新周期的 UID 没有可比性,
    调用方该重设起点而不是拿两个不相干的数比大小。
    """
    mark = marks.get(folder_raw)
    if not mark:
        return False
    if mark.get("uidvalidity") != str(uidvalidity):
        return False
    try:
        return int(uid) > int(mark["uid"])
    except (TypeError, ValueError, KeyError):
        return False
