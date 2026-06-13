"""P3.3.60 (6/12 鸿波): html_strip 单测.

跑法: cd edge/email-agent && pytest tests/test_html_strip.py -q

红线 cover:
  - 简单 HTML → plain
  - script / style 整段砍 (含内部 JS / CSS, 不漏 leaked 出来)
  - br / p / div 换行
  - HTML 实体 (&nbsp; &lt; &amp; &#160; 数字引用) → 字面
  - HeyGen newsletter 真 case (HTML-only 邮件)
  - 输入 None / 空串 不挂
"""
from catfish_email.html_strip import strip_html


def test_empty_input():
    assert strip_html("") == ""
    assert strip_html(None) == ""  # type: ignore[arg-type]


def test_simple_text_in_p():
    assert strip_html("<p>Hello world</p>") == "Hello world"


def test_br_to_newline():
    assert strip_html("Line1<br>Line2") == "Line1\nLine2"
    assert strip_html("Line1<br/>Line2") == "Line1\nLine2"
    assert strip_html("Line1<br />Line2") == "Line1\nLine2"


def test_p_block_newline():
    out = strip_html("<p>Para1</p><p>Para2</p>")
    assert "Para1" in out and "Para2" in out
    assert "\n" in out  # 两段之间有换行


def test_script_style_stripped():
    html = """
        <p>Visible</p>
        <script>alert('xss')</script>
        <style>body { color: red; }</style>
        <p>Also visible</p>
    """
    out = strip_html(html)
    assert "Visible" in out
    assert "Also visible" in out
    # 内部 JS / CSS 不能漏
    assert "alert" not in out
    assert "color: red" not in out


def test_html_entities_unescaped():
    assert strip_html("&nbsp;hello&nbsp;") == "hello"
    assert strip_html("a &lt; b") == "a < b"
    assert strip_html("&amp;copy") == "&copy"
    # 数字 / hex 实体
    assert strip_html("&#160;A") == "A"
    assert strip_html("&#x41;") == "A"


def test_div_block_newline():
    out = strip_html("<div>line1</div><div>line2</div>")
    assert "line1" in out and "line2" in out
    assert "\n" in out


def test_heygen_newsletter_like():
    """HeyGen 这种 HTML-only newsletter, body_html 有内容, body_text 空."""
    html = """
    <html><body>
      <h1>You now have a HeyGen expert on demand</h1>
      <p>Hi there,</p>
      <p>We're excited to introduce you to your dedicated HeyGen expert.</p>
      <p><a href="https://app.heygen.com/dashboard">Open dashboard</a></p>
      <p>Cheers,<br>The HeyGen Team</p>
    </body></html>
    """
    out = strip_html(html)
    assert "You now have a HeyGen expert on demand" in out
    assert "Hi there" in out
    assert "introduce you to your dedicated HeyGen expert" in out
    assert "Open dashboard" in out
    assert "The HeyGen Team" in out
    # tag 不能漏
    assert "<" not in out
    assert ">" not in out


def test_multi_whitespace_compressed():
    out = strip_html("<p>a   b\t\t\tc</p>")
    assert "a b c" in out  # 多空格 / tab 压成单空格


def test_multi_newline_compressed():
    # 多个空段落不应该产出 5 个换行
    out = strip_html("<p></p>" * 5 + "<p>only</p>")
    assert out.count("\n") <= 2
    assert "only" in out


def test_nested_tags_dont_break():
    html = "<div><p><span><strong>bold</strong> text</span></p></div>"
    out = strip_html(html)
    assert "bold text" in out


def test_no_tags_passthrough():
    assert strip_html("just plain text") == "just plain text"


def test_calendar_invite_like():
    """Google Calendar invite 含大量 link + 表格, strip 出关键内容."""
    html = """
    <div>You have been invited to the following event.</div>
    <h3>Title: HeyGen Hangouts</h3>
    <p>When: 2026-06-15 14:00</p>
    <p>Where: Online</p>
    """
    out = strip_html(html)
    assert "HeyGen Hangouts" in out
    assert "2026-06-15" in out
    assert "Online" in out
