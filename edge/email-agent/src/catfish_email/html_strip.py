"""HTML → plain text 极简 strip (stdlib 实现, 不引外部依赖).

P3.3.60 (6/12 鸿波): HTML-only 邮件 (如 HeyGen newsletter) body_text 空,
fallback 从 body_html strip. 共享给 foxmail_mac + apple_mail adapter.

设计:
  - 纯正则 + html.unescape 实体替换 (`&nbsp;` `&lt;` 等)
  - 不依赖 BeautifulSoup / html2text (catfish-email pyproject 标"标准库够用")
  - 容错: 输入是 None / 空串 / 完全没标签都不挂
  - 不处理嵌套 list / table 复杂结构, 邮件钓鱼检测+员工查看够用

不做:
  - 不格式化 markdown
  - 不渲染 image alt
  - 不解 CSS
"""
from __future__ import annotations

import html as _html_lib
import re


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", flags=re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>", flags=re.I)
_BLOCK_CLOSE_RE = re.compile(r"</(p|div|h\d|li|tr)>", flags=re.I)
_BLOCK_OPEN_RE = re.compile(r"<(p|div|h\d|li|tr)[^>]*>", flags=re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def strip_html(html: str | None) -> str:
    """把 HTML 转 plain text — 简单 + 容错.

    Args:
        html: HTML 字符串 (可 None / 空).

    Returns:
        plain text. 空输入返空串.
    """
    if not html:
        return ""
    s = html
    # script / style 整段砍 (含内部 JS / CSS)
    s = _SCRIPT_STYLE_RE.sub("", s)
    # br / 块级 tag → 换行
    s = _BR_RE.sub("\n", s)
    s = _BLOCK_CLOSE_RE.sub("\n", s)
    s = _BLOCK_OPEN_RE.sub("\n", s)
    # 其它 tag 全砍
    s = _TAG_RE.sub("", s)
    # HTML 实体 (&nbsp; &lt; &amp; &#160; 等) — stdlib 处理
    s = _html_lib.unescape(s)
    # 多空格 / 多换行压缩
    s = _MULTI_SPACE_RE.sub(" ", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip()
