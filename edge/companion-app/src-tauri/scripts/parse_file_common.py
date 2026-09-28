"""parse_file 系列共用的 preview 上限常量和截断工具。

8/15 从 parse_file.py 抽出来 (872 行, 过了 CLAUDE.md §1 的 800 红线)。

# 为什么要有这个文件 —— 已经飘过一次了

5/21 把 audio 解析抽到 parse_file_audio.py 时, `PREVIEW_MAX_CHARS` 和
`_truncate` 是**照抄了一份**过去的, 不是共用。三个月后的今天两份已经不一样:

    parse_file.py       "[... preview 截到 {limit} 字, 完整数据用 execute_code 读]"
    parse_file_audio.py "... [Audio transcript truncated at {limit} chars]"

一份中文一份英文。这种飘不报错, 只是员工看到的提示忽中忽英, 而且没人知道
哪份是"对的"。8/15 这次要再抽 PDF / Office 两个模块出去, 如果继续照抄就是
第四份、第五份。所以先立这个共用层。

# audio 那份**没有**改过来

它的文案确实不一样 ("Audio transcript truncated" vs 通用的 preview 提示),
看起来是有意为之 —— 改用户可见的文字不是重构该干的事。这里只是把它记下来,
要不要统一是产品决定, 不是拆分顺手能定的。

# 这一层只依赖标准库

谁都能 import 它, 它谁都不 import。parse_file.py 会 re-export 这几个名字,
所以 `pf._truncate` / `pf.PREVIEW_ROWS` 这些老写法照常。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: preview 文本软上限 (字)
PREVIEW_MAX_CHARS = int(os.environ.get("CATFISH_PREVIEW_MAX_CHARS") or 5000)
# Excel/CSV 每 sheet/file 显示前 N 行
PREVIEW_ROWS = int(os.environ.get("CATFISH_PREVIEW_ROWS") or 20)
# PDF 显示前 N 页
PREVIEW_PAGES = int(os.environ.get("CATFISH_PREVIEW_PAGES") or 5)
# Word 显示前 N 段
PREVIEW_PARAS = int(os.environ.get("CATFISH_PREVIEW_PARAS") or 30)


def _truncate(s: str, limit: int = PREVIEW_MAX_CHARS) -> str:
    """preview 防意外超出, 多裁掉. 上限是软上限, 5K 一般够."""
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n[... preview 截到 {limit} 字, 完整数据用 execute_code 读]"


# ── 字符集 (9/28) ──────────────────────────────────────────────
#
# 9/28 Windows 上传文件报:
#     UnicodeEncodeError: 'gbk' codec can't encode character '\xa5'
# 中文 Windows 上 Python 往管道写 stdout 默认用系统编码 GBK, 文件里有个 "¥" (或
# emoji、生僻字) 就在最后一行 print 时崩 —— 解析其实已经做完了。Companion 那边
# 已经对子进程设了 PYTHONIOENCODING=utf-8 (process::python_command); 这里是第二道,
# 手工跑脚本或别的调用方忘了设时也不崩。邮件组件 (catfish_email/__main__.py) 同款。


def force_utf8_stdio() -> None:
    """stdout / stderr 固定 UTF-8 —— Rust 端一律按 UTF-8 读。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


#: 文本类上传 (csv / txt / md / log) 的解码顺序。
#: 中文 Windows 上 Excel「另存为 CSV」、老记事本存的 txt 都是 GBK (ANSI);
#: GB18030 是 GBK 的超集, 用它解不会漏字。
TEXT_ENCODINGS = ("utf-8-sig", "gb18030")


def detect_text_encoding(path: Path) -> str:
    """整份文件能按哪种编码解开就用哪种; UTF-16 看 BOM (记事本「Unicode」)。

    都解不开返回 "utf-8" —— 调用方用 errors="replace" 读, 至少不崩。
    """
    with path.open("rb") as f:
        head = f.read(2)
    if head in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    for encoding in TEXT_ENCODINGS:
        try:
            with path.open(encoding=encoding, newline="") as f:
                while f.read(1 << 20):
                    pass
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8"


def encoding_label(encoding: str) -> str | None:
    """给 LLM 看的编码说明; UTF-8 不用说 (返回 None)。"""
    if encoding in ("utf-8", "utf-8-sig"):
        return None
    return "GB18030 (GBK)" if encoding == "gb18030" else encoding.upper()
