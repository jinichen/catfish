"""RFC822 邮件的通用取值 —— 跟邮件从哪来无关。

9/18: 抽出来是因为 eml_dir 和 imap_mail 要用同一套。今天刚在 wiki 那边吃过
"同一份逻辑抄三遍、各自漂移"的亏 (三条写入产线对 related/sources 的处理各写
各的), 这里第二个用户出现时就抽, 不等第三个。

这些函数只碰 `email.message.EmailMessage`, 不知道邮件是文件里读的还是 IMAP
拉的。所有解码失败都兜住 —— 一封邮件的编码坏了, 不该让整个收件箱打不开。
"""
from __future__ import annotations

import base64
import re
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import unquote


def header(msg: EmailMessage, name: str) -> str:
    """原样取一个头字段; 没有返回空串。"""
    value = msg.get(name)
    return str(value) if value else ""


def subject(msg: EmailMessage) -> str:
    """``=?GB2312?B?...?=`` → 中文。

    国内企业邮箱的主题基本都是 GB2312 的 base64 编码 (9/18 实测 chinatelecom.cn
    的收件箱, 每一封都是)。不解的话前端看到的全是那串乱码。
    坏编码原样返回 —— 有个歪的主题, 好过整封读不出来。
    """
    raw = header(msg, "Subject")
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001
        return raw


def addresses(msg: EmailMessage, name: str) -> list[str]:
    """``To`` / ``Cc`` 这类多值字段 → ``['显示名 <地址>', ...]``。"""
    return [
        f"{display} <{addr}>" if display else addr
        for display, addr in getaddresses(msg.get_all(name, []))
        if addr
    ]


def format_address(raw: str) -> str:
    """单个地址规整成 ``显示名 <地址>``; 解不出就原样去空白。"""
    found = getaddresses([raw])
    if not found:
        return raw.strip()
    display, addr = found[0]
    return f"{display} <{addr}>" if display else addr


def date_iso(msg: EmailMessage) -> str:
    """``Date`` 头 → ISO-8601; 缺失或解不出返回空串。"""
    raw = header(msg, "Date")
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return ""


def body_text(msg: EmailMessage) -> str:
    try:
        part = msg.get_body(preferencelist=("plain",))
        return part.get_content() if part is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def body_html(msg: EmailMessage) -> str:
    try:
        part = msg.get_body(preferencelist=("html",))
        return part.get_content() if part is not None else ""
    except Exception:  # noqa: BLE001
        return ""


# ============================================================
# 内嵌图 (cid:)
# ============================================================
#
# 9/18 鸿波 catch "为什么图片显示不出来"。实测那封 (信息安全中心的通知,
# apple_mail|Chinatelecom|2610) 的结构:
#
#     multipart/related
#     ├── multipart/alternative (text/plain + text/html)
#     ├── image/jpeg  Content-ID: <_Foxmail.1@0a5f…>  122 KB
#     └── image/jpeg  Content-ID: <_Foxmail.1@25e1…>  309 KB
#
# 正文里是 `<img src="cid:_Foxmail.1@0a5f…">`。浏览器不认 cid: 这个协议 ——
# 它是 RFC 2392 的邮件内部引用, 只有邮件客户端自己能解。
#
# 为什么这些图的字节一直拿不到: _walk_attachments 只收
# `Content-Disposition: attachment` 的部件, 而内嵌图连 Content-Disposition
# 头都没有。那个过滤对**附件列表**是对的 (谁也不想把签名档 logo 列成附件),
# 但它顺带让内嵌图的字节在整个系统里无处可取。所以这里另开一条路, 不去动
# 附件语义。
#
# 换成 data: URL 而不是别的: CSP 本来就放行 img-src data:, 不用为了看张图
# 去松安全配置; data: 图片是惰性的, 不会执行任何东西。

#: 一封邮件最多内联多少字节 (base64 后约 1.37 倍)。超了的图给一句说明, 不
#: 是为了省磁盘 —— 这份 HTML 要穿过子进程管道进 webview, 一张 20 MB 的原图
#: 会让"点开一封邮件"卡住好几秒。
INLINE_IMAGE_TOTAL_CAP = 8 * 1024 * 1024

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR = re.compile(r"""\bsrc\s*=\s*(?P<q>["'])(?P<url>.*?)(?P=q)""", re.IGNORECASE | re.DOTALL)


def inline_image_parts(msg: EmailMessage) -> dict[str, tuple[str, bytes]]:
    """``{content-id: (content-type, 字节)}``。

    key 去掉了 ``<>`` —— 头里是 ``<abc@d>``, 正文里引用的是 ``cid:abc@d``。
    """
    out: dict[str, tuple[str, bytes]] = {}
    try:
        parts = list(msg.walk())
    except Exception:  # noqa: BLE001
        return out
    for part in parts:
        cid = (part.get("Content-ID") or "").strip()
        if not cid:
            continue
        cid = cid.strip("<>").strip()
        if not cid:
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            continue
        if payload:
            out[cid] = (part.get_content_type() or "application/octet-stream", payload)
    return out


def _image_notice(reason: str) -> str:
    """图没显示出来时**摆在原地的一句话**。

    绝不留空框: 员工看到一个白洞只会理解成"鲶鱼坏了", 而不是"这张图没取到"。
    今天在空收件箱上已经领教过没有解释的降级有多难排查。
    """
    safe = (
        reason.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        '<span style="display:inline-block;padding:6px 10px;margin:2px 0;'
        'border:1px dashed #c0c0c0;border-radius:4px;color:#888;'
        'font-size:12px;font-family:inherit;">图片未显示：' + safe + "</span>"
    )


def embed_inline_images(
    html: str, msg: EmailMessage, *, total_cap: int = INLINE_IMAGE_TOTAL_CAP
) -> str:
    """把正文里的 ``<img src="cid:…">`` 换成 ``data:`` URL。

    只在**读整封**时调 —— 列清单不该为了缩略图去解几百 KB 的 base64。

    取不到 / 超预算的那些换成一句说明, 不是留着解不开的 cid: (那会显示成一个
    空洞)。
    """
    if not html or "cid:" not in html.lower():
        return html
    parts = inline_image_parts(msg)
    used = 0

    def one(match: "re.Match[str]") -> str:
        nonlocal used
        tag = match.group(0)
        src = _SRC_ATTR.search(tag)
        if src is None:
            return tag
        url = src.group("url").strip()
        if not url.lower().startswith("cid:"):
            return tag
        cid = unquote(url[4:]).strip().strip("<>")
        found = parts.get(cid)
        if found is None:
            # 常见于转发几手之后: 正文还引着 cid, 图早丢了
            return _image_notice("邮件里找不到这张图")
        ctype, payload = found
        if used + len(payload) > total_cap:
            return _image_notice(f"太大, 未加载 ({len(payload) // 1024} KB)")
        used += len(payload)
        data_url = f"data:{ctype};base64,{base64.b64encode(payload).decode('ascii')}"
        return tag[: src.start("url")] + data_url + tag[src.end("url") :]

    return _IMG_TAG.sub(one, html)


def _html_parts(msg: EmailMessage) -> list[str]:
    """所有正文 text/html 段 (Apple Mail 发出的信 HTML 会被附件切成好几段, 每段都要看)。"""
    out: list[str] = []
    for part in msg.walk():
        if part.get_content_type() != "text/html" or part.get_content_disposition() == "attachment":
            continue
        try:
            text = part.get_content()
        except Exception:  # noqa: BLE001
            text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        out.append(text)
    return out


def _img_cid_refs(msg: EmailMessage) -> set[str]:
    """正文里被 ``<img src="cid:…">`` **画成图**的那些 Content-ID (小写)。

    11/13 鸿波「为什么附件又显示不出来」: 以前这里是正文里出现过的**任何** ``cid:``。
    Apple Mail 存草稿时每个附件在正文里是一个
    ``<object type="application/x-apple-msg-attachment" data="cid:…">`` (原地画附件图标),
    也是 cid 引用 —— 于是标成 inline 的 PDF 全被当成「正文里的图」从附件列表里剔掉了,
    只剩标成 attachment 的 xlsx。只有 ``<img>`` 才是正文自己画出来的图。
    """
    refs: set[str] = set()
    for html in _html_parts(msg):
        if "cid:" not in html.lower():
            continue
        for tag in _IMG_TAG.findall(html):
            src = _SRC_ATTR.search(tag)
            if src and src.group("url").strip().lower().startswith("cid:"):
                refs.add(unquote(src.group("url").strip()[4:]).strip().strip("<>").lower())
    return refs


# Apple Mail 存草稿 / 发信时附件在正文里的占位: <object type="application/x-apple-msg-attachment" data="cid:…"></object>
# (有的客户端写 <embed src="cid:…">)。webview 里它是个插件, 显示成「Blocked Plug-in」。
_ATTACH_OBJECT = re.compile(
    r"""<object\b(?P<attrs>[^>]*)>(?:(?:(?!<object\b).)*?</object\s*>)?|<embed\b(?P<eattrs>[^>]*)/?>""",
    re.IGNORECASE | re.DOTALL,
)
_CID_ATTR = re.compile(r"""\b(?:data|src)\s*=\s*(?P<q>["'])\s*cid:(?P<cid>.*?)(?P=q)""", re.IGNORECASE | re.DOTALL)


def _attachment_chip(name: str) -> str:
    safe = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<span style="display:inline-block;padding:4px 10px;margin:4px 6px 4px 0;'
        'border:1px solid #c9d6d8;border-radius:6px;background:#f4f8f8;color:#0e6169;'
        'font-size:12px;font-family:inherit;">📎 ' + safe + "</span>"
    )


def replace_attachment_objects(html: str, msg: EmailMessage | None, *, drop: bool = False) -> str:
    """正文里的附件占位 (``<object …data="cid:…">`` / ``<embed src="cid:…">``) 换成一个「📎 文件名」小标签。

    附件本身照常在附件栏里列、能打开; 这里只是不让 webview 画出「Blocked Plug-in」。
    ``drop=True`` 直接去掉 (发草稿重建时用: 附件另外挂回去, 留着占位会指向不存在的 cid)。
    只动带 cid: 引用的 object / embed, 别的 (理论上邮件里不该有) 原样不碰 —— CSP 照样拦。
    """
    if not html or ("<object" not in html.lower() and "<embed" not in html.lower()):
        return html
    names: dict[str, str] = {}
    if msg is not None and not drop:
        try:
            for part in msg.walk():
                cid = (part.get("Content-ID") or "").strip().strip("<>").strip().lower()
                if cid and not part.is_multipart():
                    names[cid] = part.get_filename() or ""
        except Exception:  # noqa: BLE001
            names = {}

    def one(match: "re.Match[str]") -> str:
        attrs = match.group("attrs") if match.group("attrs") is not None else (match.group("eattrs") or "")
        ref = _CID_ATTR.search(attrs)
        if ref is None:
            return match.group(0)
        if drop:
            return ""
        cid = unquote(ref.group("cid")).strip().strip("<>").lower()
        return _attachment_chip(names.get(cid) or "附件")

    return _ATTACH_OBJECT.sub(one, html)


def display_html(html: str, msg: EmailMessage) -> str:
    """读整封时给界面显示的正文: 内嵌图换成 data:, 附件占位换成「📎 文件名」。"""
    return embed_inline_images(replace_attachment_objects(html, msg), msg)


def attachment_parts(msg: EmailMessage) -> list[EmailMessage]:
    """邮件里**用户看来是附件**的那些部件。全系统只认这一个口径 (列附件 / 有没有附件 / 导出附件)。

    11/07 鸿波: 已发送里带两个 PDF 的信, 鲶鱼显示没附件。真机 MIME 树:

        multipart/alternative
        ├── text/plain
        └── multipart/mixed
            ├── text/html
            ├── application/pdf   Content-Disposition: inline; filename="党委会记录….pdf"
            ├── text/html
            ├── application/pdf   Content-Disposition: inline; filename="…59号….pdf"
            └── text/html

    Apple Mail 把附件标成 ``inline`` (它要在正文里原地画出 PDF 图标), 不是
    ``attachment``; 以前只收 ``attachment`` 的过滤一个都没认出来。

    规则:
      - ``Content-Disposition: attachment`` → 附件 (哪怕也带 Content-ID)
      - 其余带文件名的叶子 (inline / 没写 disposition) → 附件,
        **除非**它的 Content-ID 被正文 ``<img src="cid:…">`` 引用 —— 那是正文里的图
        (签名档 logo / Foxmail 内嵌截图), 正文已经画出来了, 不该再列成附件。
        只认 ``<img>``: Apple Mail 草稿里附件的 ``<object data="cid:…">`` 占位不算 (11/13)
      - 没文件名的 (正文本身、没名字的内嵌图) → 不算
    """
    try:
        parts = list(msg.walk())
    except Exception:  # noqa: BLE001
        return []
    refs: set[str] | None = None
    out: list[EmailMessage] = []
    for part in parts:
        if part.is_multipart():
            continue
        try:
            disp = part.get_content_disposition()
            name = part.get_filename()
        except Exception:  # noqa: BLE001
            continue
        if disp == "attachment":
            out.append(part)
            continue
        if not name:
            continue
        cid = (part.get("Content-ID") or "").strip().strip("<>").strip().lower()
        if cid:
            if refs is None:
                refs = _img_cid_refs(msg)
            if cid in refs:
                continue
        out.append(part)
    return out


def has_attachments(msg: EmailMessage) -> bool:
    return bool(attachment_parts(msg))


def attachment_meta(msg: EmailMessage) -> list[tuple[str, int, str]]:
    """``[(文件名, 字节数, content-type), ...]`` —— 只取元信息, 不解内容。"""
    out: list[tuple[str, int, str]] = []
    for part in attachment_parts(msg):
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        out.append(
            (part.get_filename() or "(未命名附件)", len(payload), part.get_content_type())
        )
    return out
