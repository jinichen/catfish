"""所有邮件客户端 adapter 的统一接口 (ABC) + 数据类。

设计目标:
    - SKILL.md / CLI 调用方不需要知道平台 (Mac/Win) 或客户端 (Outlook/Foxmail)
    - 4 个 adapter 都实现这个接口, 提供"最小公共能力"
    - Foxmail Mac 支持读但不支持写 → create_draft 抛 NotSupportedError
    - 数据类用 frozen dataclass —— immutable 让上层放心传递

约定:
    所有 datetime 字段统一 ISO-8601 UTC 字符串, 不直接用 datetime 对象
    (跨平台序列化更稳, 也方便 JSON 输出给 SKILL helper)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence


# ============================================================
# 异常
# ============================================================


class EmailAdapterError(Exception):
    """所有 adapter 错误的基类。"""


class NotSupportedError(EmailAdapterError, NotImplementedError):
    """该 adapter 不支持此操作 (例如 Foxmail Mac 不支持 create_draft)。

    SKILL 检测到此异常应降级 (例如把正文输出给员工自己复制粘贴)。
    """


class ClientNotRunningError(EmailAdapterError):
    """客户端没在跑 (Outlook 走 COM/AppleScript 时必须先开)。

    SKILL 检测到此异常应提示员工"先打开 Outlook/Foxmail 我再帮你"。
    """


class DataNotFoundError(EmailAdapterError):
    """找不到客户端的数据目录 (Foxmail) 或邮箱账号 (Outlook)。

    SKILL 检测到此异常应让员工 catfish doctor 排查。
    """


# ============================================================
# 数据类
# ============================================================


@dataclass(frozen=True)
class Account:
    """邮箱账号元信息。一个客户端可能配多个账号。"""

    name: str
    """显示名 (员工在 Outlook/Foxmail 里设置的, 例如 '工作' / '个人')。"""

    address: str
    """邮箱地址, 例如 'hongbo@company.com'。"""

    is_default: bool = False
    """是否是默认账号 (新邮件 / 起草回复默认从这个账号发)。"""


@dataclass(frozen=True)
class Attachment:
    """附件元信息 —— 不含正文, 只是说"这封带了 X 个附件"。"""

    filename: str
    size_bytes: int
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class Message:
    """单封邮件。

    list_messages 场景 body_text = 摘要 (前 200 字), body_html 空。
    read_message 场景 body_text 为完整纯文本正文, body_html 为完整 HTML 正文。
    """

    id: str
    """adapter 自定义的稳定 ID (Outlook entryID / Foxmail 文件路径 hash)。
    必须能让 read_message(id) 重新拉这封邮件。"""

    account: str
    """这封邮件归属的邮箱账号地址 (用于多账号场景的回信路径选择)。"""

    folder: str
    """所在文件夹: 'Inbox' / 'Sent' / 'Drafts' / 自定义子文件夹路径。"""

    subject: str
    sender: str
    """格式 '显示名 <地址>' 或纯地址。"""

    recipients: Sequence[str] = ()
    cc: Sequence[str] = ()
    bcc: Sequence[str] = ()

    date: str = ""
    """ISO-8601 UTC, 例如 '2026-04-26T14:30:00+00:00'。"""

    is_read: bool = False
    has_attachments: bool = False
    attachments: Sequence[Attachment] = ()

    body_text: str = ""
    """list 场景为 snippet (前 ~200 字), read 场景为完整纯文本。"""

    body_html: str = ""
    """仅 read 场景填; list 场景空字符串 (节省 IPC)。"""

    in_reply_to: str | None = None
    """如果这是别人邮件的回复, 这里指向原邮件 ID (用于 thread 视图)。"""

    thread_id: str | None = None
    """同一个对话线程的统一 ID, adapter 自己实现稳定生成规则。"""


@dataclass(frozen=True)
class ListFilter:
    """统一的筛选条件。各 adapter 自行翻译成本机查询语法。

    所有字段可选; None / 空字符串 = 不限制。
    """

    folder: str = "Inbox"
    account: str | None = None
    """None 表示用默认账号; 多账号客户端这里指定地址。"""

    since: str | None = None
    """ISO date '2026-04-26'。包含当天 00:00 起。"""

    until: str | None = None
    """ISO date '2026-04-27'。不包含。"""

    sender_contains: str | None = None
    subject_contains: str | None = None
    body_contains: str | None = None
    """正文搜索代价高 (Foxmail 要全 box 扫一遍), adapter 实现 best-effort。"""

    unread_only: bool = False
    has_attachments: bool | None = None
    """None = 不限; True = 只要带附件的; False = 只要不带附件的。"""

    limit: int = 50
    """硬上限 —— 防内存爆。CLI 默认 50, SKILL 可以覆盖。"""


# ============================================================
# Adapter ABC
# ============================================================


class EmailAdapter(ABC):
    """所有客户端 adapter 的统一接口。

    继承注意事项:
        - 实现要 raise EmailAdapterError 子类, 不要裸 Exception
        - 所有方法都是同步 (邮件操作量小, 不必 async)
        - read_message / create_draft 必须 raise DataNotFoundError 在 id 不存在时
        - 不支持的方法 (例如 Foxmail Mac 的 create_draft) raise NotSupportedError
    """

    #: adapter 名 (用于日志和错误消息)
    name: str = "base"

    #: 是否支持写操作 (起草)
    supports_drafts: bool = False

    @abstractmethod
    def list_accounts(self) -> list[Account]:
        """列当前客户端配的所有邮箱账号。

        Returns:
            按照客户端里的顺序; 若有 default 标识, is_default=True。
        Raises:
            ClientNotRunningError: 客户端没在跑 (Outlook COM 必须客户端开着)
            DataNotFoundError: 找不到任何账号 (员工没配过)
        """

    @abstractmethod
    def list_messages(self, filt: ListFilter) -> list[Message]:
        """列收件箱 (或其它文件夹), 返回 snippet 列表。

        body_text 是摘要 (前 ~200 字), body_html = ""。
        要全文用 read_message。
        """

    @abstractmethod
    def read_message(self, message_id: str) -> Message:
        """拉单封完整邮件 (含 body_html / 附件元数据)。

        Raises:
            DataNotFoundError: id 不存在
        """

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        account: str | None = None,
        folder: str = "Inbox",
        limit: int = 30,
    ) -> list[Message]:
        """全文 / 字段搜索。各 adapter 走客户端原生搜索能力。

        query 是自然语言关键词, adapter 决定怎么 tokenize:
            - Outlook: 用 Restrict() 或 AdvancedSearch
            - Foxmail: 全 box 扫一遍 subject/sender/body 简单 substring
        """

    def create_draft(
        self,
        *,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        in_reply_to: str | None = None,
        account: str | None = None,
    ) -> str:
        """起草到客户端的草稿箱 (不发送), 返回新建草稿的 message_id。

        默认实现 raise NotSupportedError —— 子类不支持就不用 override。

        Foxmail Mac 用这个默认; Outlook Win/Mac + Foxmail Win 自己 override。
        """
        raise NotSupportedError(
            f"{self.name} 不支持 create_draft (只读 adapter)。"
            f"建议 SKILL 把正文 quote 给员工, 让员工自己复制粘贴到客户端。"
        )

    def mark_read(self, message_id: str, *, read: bool = True) -> None:
        """把邮件标记为已读 / 未读. 在客户端那一侧持久化 (next launch 仍是这状态).

        5/18 BL-EMAIL-MARK-READ. 默认实现 raise NotSupportedError — 不会写
        邮件状态的客户端 (e.g. 纯只读 IMAP 镜像) 不必 override.

        Apple Mail: AS `set read status of m to true`.
        Foxmail Mac: SQL `UPDATE mailinfo SET readstat=1 WHERE mailid=?`.

        Args:
            message_id: 跟 read_message / list_messages 返的 id 同格式
                (含 client 前缀: `apple_mail|...` / `foxmail-mac|...`).
            read: True = 标已读 (默认), False = 标回未读.

        Raises:
            NotSupportedError: 子类不支持
            DataNotFoundError: id 不存在
            EmailAdapterError: 其它失败
        """
        raise NotSupportedError(
            f"{self.name} 不支持 mark_read (只读 adapter)。"
        )
