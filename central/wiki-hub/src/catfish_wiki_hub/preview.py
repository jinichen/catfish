"""卡片上那两行摘要 (11/12)。

原来是正文前 200 字原样截: 带着 `#` `**` `|` 这些格式符号, 而且正文第一行通常就是「# 标题」——
摘要的第一行在重复标题。原件页开头还有小鲶入库时写的「SOURCE_PATH: /Users/…」, 连本机目录一起露出来。
发布时 (storage.publish) 按这个生成; 列表接口对老数据也过一遍 (老数据库里存的是原样截的 200 字)。
"""
from __future__ import annotations

import re

PREVIEW_CHARS = 200

_META_LINE = re.compile(r"^\s*(?:SOURCE_PATH|SOURCE_FILE|KEPT_PATH|kept_path|METHOD|EXTRACT(?:ED)?_(?:BY|WITH))\s*[:：].*$", re.M)
_LOCAL_PATH = re.compile(
    r"(?:~|/Users/[^/\s]+|/home/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)[/\\]"
    r"(?:(?:(?!://)[^\n\"'`<>|;；，。、])*?\.[A-Za-z0-9]{1,8}(?=$|[\s，。；;、）)\]\"'`])|[^\s\"'`<>|，。；;、）)\]]+)",
    re.M,
)


def _norm(s: str) -> str:
    return re.sub(r"[\s·\-—_：:（）()]+", "", s).lower()


def make_preview(title: str, body: str, limit: int = PREVIEW_CHARS) -> str:
    text = _META_LINE.sub("", body or "")
    text = _LOCAL_PATH.sub(lambda m: re.split(r"[/\\]", m.group(0).rstrip("/\\ "))[-1], text)
    lines = []
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or re.fullmatch(r"[\s|:\-]+", ln) or ln.startswith("```"):
            continue
        heading = ln.startswith("#")
        ln = re.sub(r"^#+\s*", "", ln)
        ln = re.sub(r"^(?:[-*+]\s+|>\s*|\d+[.)、]\s+)", "", ln)
        ln = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", ln)
        ln = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", ln)
        ln = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), ln)
        ln = re.sub(r"(\*\*|__|\*|`|~~)", "", ln)
        ln = ln.replace("|", " ").strip()
        ln = re.sub(r"^Upload:\s*", "", ln) if heading else ln
        if not ln:
            continue
        # 第一行的标题跟卡片标题一样 (或是上传文件名) 就不重复
        # 第一行是「# 标题」就不重复; 「# 标题 - 一句说明」只留说明
        if not lines and heading:
            if raw.lstrip("# ").startswith("Upload:") or _norm(ln) in _norm(title):
                continue
            if title and ln.startswith(title):
                ln = re.sub(r"^[\s\-—:：·|]+", "", ln[len(title):]).strip()
                if not ln:
                    continue
        lines.append(ln)
        if sum(len(x) for x in lines) > limit:
            break
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:limit]
