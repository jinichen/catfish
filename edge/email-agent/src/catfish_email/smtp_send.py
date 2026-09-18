"""SMTP 发信 —— 单独一个文件, 因为它根本不是 IMAP。

9/18: IMAP 协议里**没有"发信"这回事**。读邮件走 IMAP，发邮件走 SMTP：
另一个端口、另一次 TLS 握手、另一次认证。之前把 IMAP 那套建好就以为
"邮件功能做完了"，其实只做了一半。

# 端口怎么选

    465  隐式 TLS (SMTPS)      —— 连上就是加密的
    587  STARTTLS             —— 先明文连, 再升级
    25   明文 / STARTTLS       —— 国内运营商普遍封禁出站 25

默认 465。它没有"升级失败就裸奔"这个失败模式 —— STARTTLS 如果服务器不
应答升级, 有些实现会继续用明文发, 那密码和正文就都在网上裸跑了。所以走
STARTTLS 时这里**强制要求升级成功**, 失败就断开报错, 绝不降级。

# 主机怎么猜

imap.example.com → smtp.example.com。只是个默认值, 员工能改。不做
DNS SRV / autodiscover: 要联网、要处理超时、还未必准, 而猜错的代价只是
员工改一行字。

# 凭据

跟 IMAP 用同一个账号密码 —— 企业邮箱基本都是同一套。密码从系统凭据库来,
跟 IMAP 同一条路, 这个文件自己不碰存储。
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # 只为类型, 避免循环导入
    from .adapters.imap_mail import ImapConfig

logger = logging.getLogger("catfish_email.smtp_send")

#: 隐式 TLS。默认选它, 理由见模块注释。
PORT_SSL = 465
#: STARTTLS。走这个时**强制**升级成功, 不降级。
PORT_STARTTLS = 587

TIMEOUT_SECONDS = 30

HOST_ENV = "CATFISH_SMTP_HOST"
PORT_ENV = "CATFISH_SMTP_PORT"


class SmtpError(Exception):
    """发信失败。消息里**绝不带密码**。"""


def guess_host(imap_host: str) -> str:
    """imap.example.com → smtp.example.com。认不出的原样返回。"""
    host = (imap_host or "").strip().lower()
    if host.startswith("imap."):
        return "smtp." + host[len("imap.") :]
    return host


def resolve(config: "ImapConfig", env: dict[str, str] | None = None) -> tuple[str, int]:
    """(主机, 端口)。环境变量优先, 否则从 IMAP 主机猜。"""
    import os

    src = env if env is not None else os.environ
    host = (src.get(HOST_ENV) or "").strip() or guess_host(config.host)
    raw_port = (src.get(PORT_ENV) or "").strip()
    try:
        port = int(raw_port) if raw_port else PORT_SSL
    except ValueError:
        logger.warning("%s 不是数字 (%r), 用默认 %d", PORT_ENV, raw_port, PORT_SSL)
        port = PORT_SSL
    return host, port


def send(
    config: "ImapConfig",
    raw_message: bytes,
    recipients: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    transport=None,
) -> None:
    """把一封拼好的 RFC822 邮件发出去。

    Args:
        transport: 测试用的注入点。生产传 None, 走真的 smtplib。

    Raises:
        SmtpError: 连不上 / 认证失败 / 服务器拒收。**消息里不含密码**。
    """
    if not recipients:
        raise SmtpError("没有收件人")
    host, port = resolve(config, env)
    logger.info("SMTP 发信 %s:%d → %d 个收件人", host, port, len(recipients))

    client = None
    try:
        # ⚠ transport 只替换**建连**这一步, 不替换下面的协议逻辑。
        #
        # 第一版写成了 `if transport: client = transport(...)` 直接跳过整个
        # STARTTLS 分支 —— 于是注入测试替身之后, 那道强制根本不执行, 测试
        # 写了也证明不了任何东西。一个绕过被测逻辑的测试接缝比没有测试更糟:
        # 它让人以为守住了。(9/18 写 test_smtp_send.py 时被自己的测试逮到。)
        if port == PORT_SSL:
            client = (
                transport(host, port)
                if transport is not None
                else smtplib.SMTP_SSL(
                    host, port, timeout=TIMEOUT_SECONDS,
                    context=ssl.create_default_context(),
                )
            )
        else:
            client = (
                transport(host, port)
                if transport is not None
                else smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
            )
            client.ehlo()
            # ⚠ 强制升级。不加这一步的话, 服务器不支持 STARTTLS 时会**继续
            #   用明文发** —— 密码和整封邮件在网上裸跑, 而且界面上看起来
            #   一切正常。宁可发不出去, 不可明文发出去。
            if not client.has_extn("starttls"):
                raise SmtpError(
                    f"{host}:{port} 不支持 STARTTLS, 拒绝用明文发送。"
                    f"请改用 {PORT_SSL} 端口 (隐式 TLS)。"
                )
            client.starttls(context=ssl.create_default_context())
            client.ehlo()

        client.login(config.user, config.password)
        client.sendmail(config.user, list(recipients), raw_message)
    except SmtpError:
        raise
    except smtplib.SMTPAuthenticationError:
        # 绝不把服务器返回的原文拼进来 —— 有些服务器会回显用户名
        raise SmtpError(
            f"SMTP 认证被拒 ({config.redacted()})。"
            "企业邮箱通常要用「授权码」而不是登录密码。"
        ) from None
    except (smtplib.SMTPException, OSError) as error:
        raise SmtpError(
            f"发送失败 {host}:{port}: {type(error).__name__}"
        ) from error
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:  # noqa: BLE001
                pass
