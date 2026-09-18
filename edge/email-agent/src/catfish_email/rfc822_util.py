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


def has_attachments(msg: EmailMessage) -> bool:
    try:
        return any(True for _ in msg.iter_attachments())
    except Exception:  # noqa: BLE001
        return False


def attachment_meta(msg: EmailMessage) -> list[tuple[str, int, str]]:
    """``[(文件名, 字节数, content-type), ...]`` —— 只取元信息, 不解内容。"""
    out: list[tuple[str, int, str]] = []
    try:
        parts = list(msg.iter_attachments())
    except Exception:  # noqa: BLE001
        return out
    for part in parts:
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        out.append(
            (part.get_filename() or "(未命名附件)", len(payload), part.get_content_type())
        )
    return out
