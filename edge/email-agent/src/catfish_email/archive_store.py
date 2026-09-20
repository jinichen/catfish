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
import logging
import os
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

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
