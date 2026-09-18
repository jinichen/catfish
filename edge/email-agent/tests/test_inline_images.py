"""内嵌图 (cid:) —— 夹具照 9/18 真机那封抓到的结构造。

# 撞到的是什么

鸿波 catch "为什么图片显示不出来": 信息安全中心那封通知, 正文里两张截图,
界面上是两个白洞。实测 (apple_mail|Chinatelecom|2610) 的 MIME 树:

    multipart/related
    ├── multipart/alternative (text/plain + text/html)
    ├── image/jpeg  Content-ID: <_Foxmail.1@0a5f…>  122 KB   disp=(空)
    └── image/jpeg  Content-ID: <_Foxmail.1@25e1…>  309 KB   disp=(空)

正文里是 `<img src="cid:_Foxmail.1@0a5f…">`。浏览器不认 cid: —— 那是 RFC 2392
的邮件内部引用, 只有邮件客户端自己能解。

# 我先猜错了一次

我以为是 `iter_attachments()` 在 multipart/alternative 顶层看不到嵌在
related 里的图。探针打出真结构后推翻了: related 在**外层**, iter_attachments
两张都看得见。

真因在 `_walk_attachments`: 它只收 `Content-Disposition: attachment` 的部件,
而内嵌图连 Content-Disposition 头都没有 (上面 disp 那一列是空的)。那个过滤
对**附件列表**是对的 —— 谁也不想把签名档 logo 列成附件 —— 但它顺带让内嵌图
的字节在整个系统里无处可取。所以修法是另开一条路, 不动附件语义。

# 这个文件钉什么

  ① 没有 Content-Disposition 的内嵌图也要能取到字节 (真机上就是这样)
  ② cid: 换成 data:, 且 **tag 上其它属性不能丢** (width/style 一丢排版就垮)
  ③ 取不到的 cid: 要留一句话, **不能留空洞**
  ④ 列清单那条路一个字节都不许多解
  ⑤ 附件语义不许被这个改动带偏 —— 内嵌图仍旧不算附件
"""
from __future__ import annotations

import base64
import email
import email.policy
from email.message import EmailMessage

import pytest

from catfish_email import rfc822_util as rfc822

#: 一张 1x1 的 GIF, 够小, 内容不重要 —— 我们测的是搬运, 不是解码
TINY = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

CID_A = "_Foxmail.1@0a5f73df-00de-c5d5-051c-25dcaedf822c"
CID_B = "_Foxmail.1@25e19d40-45d2-985a-0163-f84fe34d49cb"


def build(html: str, cids=(CID_A, CID_B), *, payload=TINY, disposition=None) -> EmailMessage:
    """照真机那棵树造: related 在外, alternative 在内, 图是 related 的直接子部件。"""
    raw_parts = []
    for cid in cids:
        headers = [
            "Content-Type: image/jpeg",
            "Content-Transfer-Encoding: base64",
            f"Content-ID: <{cid}>",
        ]
        if disposition:
            headers.append(f"Content-Disposition: {disposition}")
        raw_parts.append(
            "\r\n".join(headers)
            + "\r\n\r\n"
            + base64.b64encode(payload).decode("ascii")
        )
    body = (
        "Content-Type: multipart/related; boundary=OUTER\r\n"
        "Subject: =?GB2312?B?1Nq9qM/uxL/H5bWl?=\r\n"
        "From: ff_nic@chinatelecom.cn\r\n"
        "\r\n"
        "--OUTER\r\n"
        "Content-Type: multipart/alternative; boundary=INNER\r\n"
        "\r\n"
        "--INNER\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "纯文本版\r\n"
        "--INNER\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n" + html + "\r\n"
        "--INNER--\r\n"
    )
    for part in raw_parts:
        body += "--OUTER\r\n" + part + "\r\n"
    body += "--OUTER--\r\n"
    return email.message_from_bytes(body.encode("utf-8"), policy=email.policy.default)


# ─────────────────────────────────────────────────────────────
# ① 取字节: 靠 Content-ID, 不靠 Content-Disposition
# ─────────────────────────────────────────────────────────────


def test_inline_images_found_without_content_disposition():
    """真机上这两张图**一个 Content-Disposition 都没有** —— 这正是它们一直
    取不到的原因, 所以这条单独钉住。"""
    msg = build("<p>x</p>")
    parts = rfc822.inline_image_parts(msg)
    assert set(parts) == {CID_A, CID_B}
    assert parts[CID_A][0] == "image/jpeg"
    assert parts[CID_A][1] == TINY


def test_content_id_angle_brackets_are_stripped():
    """头里是 <abc@d>, 正文里引的是 cid:abc@d —— 不去掉尖括号就永远对不上。"""
    msg = build("<p>x</p>")
    assert all(not k.startswith("<") for k in rfc822.inline_image_parts(msg))


# ─────────────────────────────────────────────────────────────
# ② 改写
# ─────────────────────────────────────────────────────────────


def test_cid_becomes_a_data_url():
    msg = build(f'<p>看图</p><img src="cid:{CID_A}">')
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg)
    assert "cid:" not in out
    assert "data:image/jpeg;base64," + base64.b64encode(TINY).decode() in out


def test_other_attributes_on_the_img_tag_survive():
    """只换 src 的值, 别把 tag 重建一遍 —— width/style 一丢排版就垮。"""
    msg = build(f'<img width="600" src="cid:{CID_A}" style="border:0" alt="截图">')
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg)
    assert 'width="600"' in out
    assert 'style="border:0"' in out
    assert 'alt="截图"' in out


def test_both_images_are_replaced():
    msg = build(f'<img src="cid:{CID_A}"><br><img src="cid:{CID_B}">')
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg)
    assert out.count("data:image/jpeg;base64,") == 2


def test_single_quotes_and_uppercase_tags_are_handled():
    """邮件 HTML 是各家客户端拼出来的, 不能假定都是双引号小写。"""
    msg = build(f"<IMG SRC='cid:{CID_A}'>")
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg)
    assert "cid:" not in out
    assert "data:image/jpeg;base64," in out


def test_remote_and_data_images_are_left_alone():
    """这一层只管 cid:。远程图拦不拦是渲染策略, 在前端决定。"""
    html = '<img src="https://example.com/a.png"><img src="data:image/gif;base64,AAAA">'
    msg = build(html)
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg)
    assert 'src="https://example.com/a.png"' in out
    assert 'src="data:image/gif;base64,AAAA"' in out


# ─────────────────────────────────────────────────────────────
# ③ 取不到的时候要说话
# ─────────────────────────────────────────────────────────────


def test_missing_cid_leaves_a_sentence_not_a_hole():
    """转发几手之后常见: 正文还引着 cid, 图早丢了。

    留个空洞的话员工只会理解成"鲶鱼坏了"。今天在空收件箱上已经领教过没有
    解释的降级有多难排查。
    """
    msg = build('<img src="cid:早就没了@x">', cids=())
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg)
    assert "cid:" not in out
    assert "图片未显示" in out
    assert "找不到" in out


def test_oversized_images_say_so_with_a_size():
    msg = build(f'<img src="cid:{CID_A}">', payload=b"x" * 5000)
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg, total_cap=1000)
    assert "data:" not in out
    assert "图片未显示" in out
    assert "KB" in out, "得告诉员工多大, 否则他没法判断值不值得另想办法"


def test_the_budget_is_for_the_whole_message_not_per_image():
    """一封邮件里五十张图, 每张都不超标, 加起来能把 webview 拖死。"""
    msg = build(
        f'<img src="cid:{CID_A}"><img src="cid:{CID_B}">', payload=b"x" * 600
    )
    out = rfc822.embed_inline_images(rfc822.body_html(msg), msg, total_cap=1000)
    assert out.count("data:image/jpeg;base64,") == 1, "第二张应该超预算"
    assert "图片未显示" in out


def test_notice_text_is_escaped():
    """说明文字里混进 < 不该把邮件 HTML 结构撬开。"""
    assert "<script>" not in rfc822._image_notice("<script>alert(1)</script>")


# ─────────────────────────────────────────────────────────────
# ④⑤ 不许波及别的
# ─────────────────────────────────────────────────────────────


def test_html_without_any_cid_skips_the_mime_walk(monkeypatch):
    """没有 cid: 就别去走那棵 MIME 树 —— 读每封邮件都会到这一行。

    第一版这条写的是 `assert 结果 is 原串`, 变异跑出来发现它**根本不挂**:
    re.sub 没匹配到东西时 CPython 会把原对象还回来, 于是删掉早退那行断言
    照样成立。盯"有没有白干活"才是真判据, 盯返回值身份是在赌实现细节。
    """
    msg = build("<p>纯文字通知</p>")
    walked: list[int] = []
    monkeypatch.setattr(
        rfc822, "inline_image_parts", lambda m: (walked.append(1), {})[1]
    )
    html = rfc822.body_html(msg)
    out = rfc822.embed_inline_images(html, msg)
    assert walked == [], "没有 cid: 却还是解了一遍 MIME 树"
    assert out == html


def test_inline_images_still_do_not_count_as_attachments():
    """附件语义不许被这个改动带偏: 签名档 logo 不该出现在附件行里。

    (这是 _walk_attachments 那条 Content-Disposition 过滤的本意, 它没错 ——
     错的是当年没给内嵌图另开一条路。)
    """
    from catfish_email.adapters.apple_mail_emlx import _walk_attachments

    msg = build(f'<img src="cid:{CID_A}">')
    assert _walk_attachments(msg) == []


def test_a_real_attachment_is_still_collected():
    """反过来也要成立 —— 别为了让内嵌图显示, 把真附件也一起漏掉。"""
    from catfish_email.adapters.apple_mail_emlx import _walk_attachments

    msg = build(
        f'<img src="cid:{CID_A}">',
        cids=(CID_A,),
        disposition='attachment; filename="报表.xlsx"',
    )
    got = _walk_attachments(msg)
    assert [a.filename for a in got] == ["报表.xlsx"]


@pytest.mark.parametrize("broken", ["<img>", '<img src="">', "<img src=cid:无引号>"])
def test_broken_img_tags_do_not_blow_up(broken):
    """一个畸形 tag 不该让整封邮件打不开。"""
    msg = build(broken)
    rfc822.embed_inline_images(rfc822.body_html(msg), msg)
