"""IMAP 连接配置 —— 环境变量、端口、保留策略。

9/21 从 imap_mail.py 拆出来 (那个文件加完归档取原文和保留策略之后 824 行,
越过仓库的 800 行红线)。拆这一块的理由跟当初拆 imap_folders.py 一样:
**它跟"怎么跟服务器说话"没有关系**。这里全是"员工配了什么"的形状和默认值,
不发一条 IMAP 命令。

拆开之后还有个好处: 保留策略是这套配置里唯一不可逆的一项 (它决定要不要删
员工服务器上的邮件), 单独一个文件更容易一眼看全它的默认值和兜底方向。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("catfish_email.adapters.imap_config")

HOST_ENV = "CATFISH_IMAP_HOST"
PORT_ENV = "CATFISH_IMAP_PORT"
USER_ENV = "CATFISH_IMAP_USER"
#: 凭据。阶段一从环境变量取 (Companion 从 keyring 读出来再注入子进程),
#: 这样 catfish-email 自己永远不碰 keyring, 也不需要平台后端。
PASSWORD_ENV = "CATFISH_IMAP_PASSWORD"
#: 归档之后多久把服务器上那份删掉。员工在界面上选, Companion 注进来。
RETENTION_ENV = "CATFISH_IMAP_RETENTION"

DEFAULT_PORT = 993


#: 保留策略 → 归档校验通过之后等多少秒才从服务器删。None = 永不删。
#:
#: 基准是 **verified_at**, 不是 archived_at。写入返回成功只说明 write() 没抛
#: 异常; 盘上那份到底对不对, 只有独立回读核对过才知道。拿 archived_at 计时
#: 等于把"我以为写成功了"当成"确实存下来了"。
RETENTION_SECONDS: dict[str, float | None] = {
    "immediate": 0.0,
    "1w": 7 * 24 * 3600.0,
    "2w": 14 * 24 * 3600.0,
    "never": None,
}
#: 默认 never。**这个默认值是刻意的**: 删服务器上的邮件不可逆, 不能因为
#: 员工没注意到设置项就悄悄开始删。要删必须是他明确选过。
DEFAULT_RETENTION = "never"


@dataclass(frozen=True)
class ImapConfig:
    host: str
    user: str
    password: str
    port: int = DEFAULT_PORT
    #: immediate / 1w / 2w / never
    retention: str = DEFAULT_RETENTION

    def retention_seconds(self) -> float | None:
        """None = 永不删。认不出来的值也当 never —— 拿不准就别删。"""
        return RETENTION_SECONDS.get(self.retention, None)

    def redacted(self) -> str:
        """能进日志的形态 —— 密码永远不出现。"""
        return f"{self.user}@{self.host}:{self.port} (保留策略={self.retention})"


def config_from_env() -> ImapConfig | None:
    host = os.environ.get(HOST_ENV, "").strip()
    user = os.environ.get(USER_ENV, "").strip()
    password = os.environ.get(PASSWORD_ENV, "")
    if not (host and user and password):
        return None
    try:
        port = int(os.environ.get(PORT_ENV, "").strip() or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    retention = os.environ.get(RETENTION_ENV, "").strip() or DEFAULT_RETENTION
    if retention not in RETENTION_SECONDS:
        # 认不出来就退回 never 并**出声**。静默当成默认值的话, 员工以为设了
        # "两周" 其实一直没删, 或者更糟 —— 打错字被当成 immediate。
        logger.warning(
            "不认识的保留策略 %r, 退回 %s (可选: %s)",
            retention, DEFAULT_RETENTION, "/".join(RETENTION_SECONDS),
        )
        retention = DEFAULT_RETENTION
    return ImapConfig(
        host=host, user=user, password=password, port=port, retention=retention
    )
