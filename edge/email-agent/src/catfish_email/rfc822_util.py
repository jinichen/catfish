"""RFC822 邮件的通用取值 —— 跟邮件从哪来无关。

9/18: 抽出来是因为 eml_dir 和 imap_mail 要用同一套。今天刚在 wiki 那边吃过
"同一份逻辑抄三遍、各自漂移"的亏 (三条写入产线对 related/sources 的处理各写
各的), 这里第二个用户出现时就抽, 不等第三个。

这些函数只碰 `email.message.EmailMessage`, 不知道邮件是文件里读的还是 IMAP
拉的。所有解码失败都兜住 —— 一封邮件的编码坏了, 不该让整个收件箱打不开。
"""
from __future__ import annotations

from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime


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
