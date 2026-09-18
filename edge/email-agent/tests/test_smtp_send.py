"""SMTP 发信。

# 这个文件存在的理由

变异对照时发现: 把 `if not client.has_extn("starttls")` 那道强制去掉,
**一条测试都不挂**。而那是整个新代码里最敏感的一行 —— 它一旦回退, 密码和
整封邮件就在网上明文裸跑, 界面上一切正常, 邮件也正常送达。没有任何人会
发现。

所以这里钉的全是"看不出来的" 性质:

  ① 默认走隐式 TLS (465), 不是明文
  ② 走 STARTTLS 时**必须**升级成功, 服务器不支持就拒发, 绝不降级明文
  ③ 任何错误消息里都不许出现密码
"""
from __future__ import annotations

import smtplib

import pytest

from catfish_email import smtp_send
from catfish_email.adapters.imap_mail import ImapConfig

CFG = ImapConfig(host="imap.chinatelecom.cn", user="me@chinatelecom.cn", password="TOPSECRET")


class Recorder:
    """记下到底做了什么 —— 尤其是"有没有在没升级 TLS 的情况下把信发出去"。"""

    def __init__(self, host, port, *, starttls=True):
        self.host, self.port = host, port
        self._has_starttls = starttls
        self.upgraded = False
        self.sent: list[tuple[str, list[str], bytes]] = []
        self.logged_in = False

    def ehlo(self):
        return 250, b"ok"

    def has_extn(self, name):
        return name.lower() == "starttls" and self._has_starttls

    def starttls(self, context=None):
        self.upgraded = True

    def login(self, user, password):
        self.logged_in = True

    def sendmail(self, sender, recipients, raw):
        self.sent.append((sender, list(recipients), raw))

    def quit(self):
        pass


def make(**kw):
    box = {}

    def factory(host, port):
        box["client"] = Recorder(host, port, **kw)
        return box["client"]

    factory.box = box
    return factory


# ─────────────────────────────────────────────────────────────
# ② 明文这条路必须走不通
# ─────────────────────────────────────────────────────────────


def test_starttls_is_mandatory_on_port_587():
    """服务器不支持 STARTTLS 就**拒发**。

    不加这道强制的话, smtplib 会老老实实用明文把密码和正文发出去 ——
    邮件照常送达, 界面上一个字都不会变, 只有中间的人看得见。
    宁可发不出去, 不可明文发出去。
    """
    factory = make(starttls=False)
    with pytest.raises(smtp_send.SmtpError, match="STARTTLS"):
        smtp_send.send(
            CFG, b"raw", ["a@b.cn"],
            env={smtp_send.PORT_ENV: "587"}, transport=factory,
        )
    assert factory.box["client"].sent == [], "没升级 TLS 却还是把信发出去了"
    assert factory.box["client"].logged_in is False, "密码明文发出去了"


def test_starttls_actually_upgrades_before_login():
    factory = make(starttls=True)
    smtp_send.send(
        CFG, b"raw", ["a@b.cn"], env={smtp_send.PORT_ENV: "587"}, transport=factory
    )
    client = factory.box["client"]
    assert client.upgraded is True
    assert client.sent


def test_default_port_is_implicit_tls():
    """默认 465 —— 它没有"升级失败就裸奔"这个失败模式。"""
    assert smtp_send.PORT_SSL == 465
    factory = make()
    smtp_send.send(CFG, b"raw", ["a@b.cn"], env={}, transport=factory)
    assert factory.box["client"].port == 465


# ─────────────────────────────────────────────────────────────
# ③ 凭据不许漏
# ─────────────────────────────────────────────────────────────


def test_auth_failure_message_has_no_password():
    """有些服务器会把用户名回显在错误里, 原文拼进去就等于往日志里写凭据。"""

    class Rejects(Recorder):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad TOPSECRET")

    with pytest.raises(smtp_send.SmtpError) as caught:
        smtp_send.send(CFG, b"raw", ["a@b.cn"], env={}, transport=Rejects)
    assert "TOPSECRET" not in str(caught.value)
    assert "授权码" in str(caught.value), "该告诉员工企业邮箱要用授权码"


def test_network_failure_message_has_no_password():
    class Dies(Recorder):
        def login(self, user, password):
            raise OSError("connection reset by TOPSECRET")

    with pytest.raises(smtp_send.SmtpError) as caught:
        smtp_send.send(CFG, b"raw", ["a@b.cn"], env={}, transport=Dies)
    assert "TOPSECRET" not in str(caught.value)


# ─────────────────────────────────────────────────────────────
# 主机 / 端口
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "imap_host,expected",
    [
        ("imap.chinatelecom.cn", "smtp.chinatelecom.cn"),
        ("imap.qq.com", "smtp.qq.com"),
        ("mail.example.com", "mail.example.com"),  # 认不出就原样, 让员工自己改
    ],
)
def test_host_is_guessed_from_the_imap_host(imap_host, expected):
    assert smtp_send.guess_host(imap_host) == expected


def test_explicit_host_wins_over_the_guess():
    host, _ = smtp_send.resolve(CFG, {smtp_send.HOST_ENV: "smtp2.example.cn"})
    assert host == "smtp2.example.cn"


def test_a_garbage_port_falls_back_instead_of_crashing():
    """配置里一个手滑不该让发信变成一个 ValueError 堆栈。"""
    _, port = smtp_send.resolve(CFG, {smtp_send.PORT_ENV: "八六五"})
    assert port == smtp_send.PORT_SSL


def test_no_recipients_is_refused_before_connecting():
    factory = make()
    with pytest.raises(smtp_send.SmtpError, match="收件人"):
        smtp_send.send(CFG, b"raw", [], env={}, transport=factory)
    assert "client" not in factory.box, "没有收件人却还是连了服务器"
