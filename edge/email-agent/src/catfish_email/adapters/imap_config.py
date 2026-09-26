"""IMAP 连接配置 —— 环境变量、端口、保留策略。

9/21 从 imap_mail.py 拆出来 (那个文件加完归档取原文和保留策略之后 824 行,
越过仓库的 800 行红线)。拆这一块的理由跟当初拆 imap_folders.py 一样:
**它跟"怎么跟服务器说话"没有关系**。这里全是"员工配了什么"的形状和默认值,
不发一条 IMAP 命令。

拆开之后还有个好处: 保留策略是这套配置里唯一不可逆的一项 (它决定要不要删
员工服务器上的邮件), 单独一个文件更容易一眼看全它的默认值和兜底方向。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("catfish_email.adapters.imap_config")

HOST_ENV = "CATFISH_IMAP_HOST"
PORT_ENV = "CATFISH_IMAP_PORT"
USER_ENV = "CATFISH_IMAP_USER"
#: 凭据。优先从环境变量取 (Companion 从凭据库读出来注入子进程)。
#:
#: 9/26 起 Windows 多一条: 环境变量没有时自己去凭据管理器读 —— 见
#: _windows_stored_config。macOS 仍然只认环境变量 (钥匙串 ACL 只放行 Companion)。
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
        return _windows_stored_config() if sys.platform == "win32" else None
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


# ── Windows: 没有注入环境变量时, 自己读 Companion 存的那份 ─────────────────
#
# 9/26 Windows 实证: 小鲶的 catfish_email_read 连调 4 次都退出 1。邮件 CLI 有两个
# 调用方 —— Companion (email.rs, 会注入 IMAP 环境变量) 和 tool-bridge (小鲶的邮件
# 工具, **什么都不注入**)。tool-bridge 起的 CLI 看不到 IMAP, 在只有 IMAP 的 Windows
# 机器上等于没有邮件来源, 于是去试 Outlook COM, 报错退出。macOS 上 Apple Mail 兜着,
# 从来没暴露。
#
# 为什么只在 Windows 自己读: Windows 凭据管理器没有按程序的访问名单, 同一个用户下
# 的任何进程本来就读得到 (companion_secrets.py 文件头同一结论, 教学凭据的 wincred://
# 也是这么走的)。这里读并没有扩大谁能拿到密码。macOS 钥匙串只放行 Companion, 别的
# 进程去读会弹一个后台看不见的授权框, 所以 macOS 不这么做。
#
# 条目格式跟 Companion 写入端 (imap_credentials.rs::entry_for) 一致:
#   keyring-rs 的 Entry::new("catfish", "imap:<邮箱>") 在 Windows 上存成
#   Generic 凭据, target = "imap:<邮箱>.catfish"。

_CATFISH_SERVICE = "catfish"


def _source_json() -> Path:
    return Path.home() / ".catfish" / "imap-source.json"


def _read_windows_credential(target: str) -> str | None:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(pcred)):  # 1 = CRED_TYPE_GENERIC
        return None
    try:
        cred = pcred.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        advapi.CredFree(pcred)
    # keyring-rs 按 UTF-16LE 存密码
    return blob.decode("utf-16-le") if blob else None


def _windows_stored_config() -> ImapConfig | None:
    try:
        source = json.loads(_source_json().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    host = str(source.get("host") or "").strip()
    user = str(source.get("user") or "").strip()
    if not (host and user):
        return None
    try:
        password = _read_windows_credential(f"imap:{user}.{_CATFISH_SERVICE}")
    except OSError:
        password = None
    if not password:
        logger.warning("配置了 IMAP (%s) 但凭据管理器里取不到密码, 请在邮件页重新保存一次", user)
        return None
    # 发信用的 SMTP 设置也在这份 JSON 里; Companion 那条路是注环境变量,
    # 这里补到本进程环境 (只影响这一次 CLI), smtp_send 照旧从环境变量读。
    if source.get("smtp_host"):
        os.environ.setdefault("CATFISH_SMTP_HOST", str(source["smtp_host"]))
    if source.get("smtp_port"):
        os.environ.setdefault("CATFISH_SMTP_PORT", str(source["smtp_port"]))
    retention = str(source.get("retention") or DEFAULT_RETENTION)
    return ImapConfig(
        host=host, user=user, password=password,
        port=int(source.get("port") or DEFAULT_PORT),
        retention=retention if retention in RETENTION_SECONDS else DEFAULT_RETENTION,
    )
