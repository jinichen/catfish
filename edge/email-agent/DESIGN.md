# catfish-email · 设计文档

> P1-1 三大支柱之二: 让小鲶替员工读邮件 + 起草回复, 但**不自动发**。
>
> 现状约束 (2026-04-26 跟员工对齐): 公司邮箱不支持 IMAP / web / API,
> 只有桌面客户端 — 所以走客户端集成路径。
>
> **5/17 BL-EMAIL-APPLEMAIL 调整**: macOS 端目标客户端从 **Outlook for Mac**
> 改成 **Apple Mail (Mail.app, 系统自带)**. 理由: Mail.app macOS 100% 装机,
> AppleScript dictionary 完整, 国内员工免 Microsoft 365 订阅. Windows 端仍是
> Outlook (Windows + 企业 Outlook 是国内主力组合).

---

## 1. Why this design

### 1.1 跟 IMAP 方案为什么不一样

原始计划走 IMAP + SMTP, 因为通用、成熟。但**公司邮箱后端不支持 IMAP**, 那条路 day 1 就断。

剩下三条路:
- ❌ Web 邮箱抓取 → 公司邮箱**没 web 客户端**
- ❌ REST API → 公司邮箱**没 API**
- ✅ 桌面客户端集成 → 唯一可行

### 1.2 桌面客户端集成的关键 insight

不要试图重写一个邮件客户端。**让 catfish 跟员工已经登录的桌面客户端对话**, 借助:
- macOS **Apple Mail (Mail.app)** → AppleScript (Apple 官方 dictionary 完整)  ← **5/17 替代 Outlook for Mac**
- Windows Outlook → COM (pywin32, Microsoft 官方接口)
- Windows Foxmail → 解析本地 `.box` 数据文件 (无官方 API, 逆向)
- macOS Foxmail → 同 Windows 但**只读** (写入路径不可靠)

凭据 / OAuth / Exchange auth 全部由客户端自己管, catfish 完全不碰密码。

**为啥 Apple Mail > Outlook for Mac**:
- macOS 100% 装机, Outlook for Mac 需 Microsoft 365 订阅 (国内员工渗透 <20%)
- Apple Mail AppleScript dictionary 比 Outlook for Mac 完整 (Microsoft AS dictionary 历史失修)
- Mail.app 跟 Exchange / IMAP / iCloud / Gmail 全兼容, 后端独立于客户端选择
- 唯一权限墙: macOS Automation (一次性弹窗确认, 跟 Foxmail Mac 同模式)

### 1.3 不自动发的红线

| 动作 | MVP | 红线原因 |
|---|---|---|
| 列收件箱 / 读 / 搜 / 起草到草稿箱 | ✅ | 安全 |
| **自动发送** | ❌ | 邮件错发是核武器, 不可撤回。员工开客户端点"发送"的 0.5s 是认知 checkpoint, 挡住 90% 低级错误 |
| 删除 / 移动 / 标垃圾 | ❌ | 同上, 不可撤回 |
| 标已读 (员工明确说"标已读") | ⚠ 看情况 | 安全但要谨慎, MVP 不开 |

---

## 2. 模块结构

```
edge/email-agent/
├── DESIGN.md                       # 本文档
├── README.md                       # 装/用快速指南
├── pyproject.toml
├── src/catfish_email/
│   ├── __init__.py
│   ├── __main__.py                 # CLI 入口: catfish-email <subcmd>
│   ├── config.py                   # 平台 + 客户端检测 + 数据目录定位
│   │
│   ├── adapters/                   # ★ 适配器模式
│   │   ├── __init__.py
│   │   ├── base.py                 # EmailAdapter ABC + 数据类
│   │   ├── apple_mail.py           # macOS Mail.app AppleScript + EMLX 兜底  ← 5/18 替代 outlook_mac.py
│   │   ├── outlook_win.py          # Windows COM (TODO)
│   │   ├── foxmail_win.py          # Foxmail Win 完整读写 (含 draft 注入, TODO)
│   │   └── foxmail_mac.py          # Foxmail Mac 只读 (draft 抛 NotSupportedError)
│   │
│   ├── box_parser.py               # Foxmail .box / .ind 解析器 (Win/Mac 共用)
│   ├── draft.py                    # 调 gateway LLM 起草 (平台无关)
│   ├── relevance.py                # (可选) 复用 feishu-monitor 风格关键词过滤
│   └── inbox.py                    # Adapter 工厂 + 高层协调
│
├── hermes-skill/
│   └── catfish-email/
│       ├── SKILL.md                # 4 模式 + 红线 + 失败降级
│       └── (helper bash 脚本调 CLI)
│
├── tests/
│   ├── test_box_parser.py          # 用 fixture .box 文件
│   ├── test_adapter_apple_mail.py  # mock osascript + EMLX 文件 (50 tests)  ← 5/18
│   ├── test_adapter_foxmail_mac.py # 真合成 Profile 目录 (32 tests)
│   ├── test_adapter_outlook_win.py # mock win32com (TODO)
│   └── fixtures/
│       ├── sample.box
│       └── sample.ind
│
└── install.sh                      # 装 skill 到 ~/.hermes/skills/productivity/
```

---

## 3. Adapter 抽象 (核心契约)

```python
# adapters/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Sequence


class NotSupportedError(NotImplementedError):
    """该适配器不支持此操作 (例如 Foxmail Mac 不支持 create_draft)。"""


@dataclass(frozen=True)
class Account:
    """邮箱账号元信息。一个客户端可能配多个账号。"""
    name: str            # 显示名 (Outlook/Foxmail 里设的)
    address: str         # 邮箱地址
    is_default: bool = False


@dataclass(frozen=True)
class Attachment:
    filename: str
    size_bytes: int
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class Message:
    id: str              # adapter 自定义 (Outlook entryID / Foxmail 文件路径)
    account: str         # 邮箱地址
    folder: str          # "Inbox" / "Sent" / 等
    subject: str
    sender: str          # "张三 <zhang@x.com>"
    recipients: Sequence[str]
    cc: Sequence[str] = ()
    bcc: Sequence[str] = ()
    date: str            # ISO-8601 UTC
    is_read: bool = False
    has_attachments: bool = False
    attachments: Sequence[Attachment] = ()
    body_text: str = ""              # 列表场景为 snippet, 详情场景为全文
    body_html: str = ""              # 详情场景才填
    in_reply_to: str | None = None   # 用于 reply chain
    thread_id: str | None = None


@dataclass(frozen=True)
class ListFilter:
    """统一的筛选条件。各 adapter 自己实现成本机查询。"""
    folder: str = "Inbox"
    account: str | None = None       # None = 默认账号
    since: str | None = None         # ISO date "2026-04-26"
    until: str | None = None
    sender_contains: str | None = None
    subject_contains: str | None = None
    body_contains: str | None = None
    unread_only: bool = False
    has_attachments: bool | None = None
    limit: int = 50


class EmailAdapter(ABC):
    """所有客户端 adapter 的统一接口。

    注: 所有 list/read 都是同步操作 (邮件操作量小, 不必 async)。
    SKILL 调用方不需要知道平台, 通过 inbox.get_adapter() 拿到合适的实例。
    """

    @abstractmethod
    def list_accounts(self) -> list[Account]:
        """列出当前客户端配的所有账号。"""

    @abstractmethod
    def list_messages(self, filt: ListFilter) -> list[Message]:
        """列收件箱 (或其它文件夹), 按筛选条件返回 list snippet。"""

    @abstractmethod
    def read_message(self, message_id: str) -> Message:
        """拉单封完整邮件 (含 body_html / 附件元数据)。"""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        account: str | None = None,
        folder: str = "Inbox",
        limit: int = 30,
    ) -> list[Message]:
        """全文 / 字段搜索。各 adapter 走客户端原生搜索能力。"""

    @abstractmethod
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
        """起草到草稿箱 (不发送), 返回新建草稿的 message_id。

        Foxmail Mac 抛 NotSupportedError —— SKILL 检测到就降级到
        '我把正文给你, 复制粘贴到 Foxmail 草稿' 模式。
        """
```

---

## 4. 4 个 Adapter 各自策略

### 4.1 macOS Apple Mail (`apple_mail.py`)  ← 5/17 替代原 outlook_mac.py

**机制**: subprocess 调 `osascript -e '<applescript>'` 跑 .applescript 文件。

**.applescript 输出 JSON**, Python 用 `json.loads()` 反序列化成 `Message` 对象。

**关键 AppleScript 命令**:
```applescript
tell application "Mail"
    -- 列账户
    set accs to accounts
    -- 列 (最近 7 天)
    set msgs to (messages of inbox where (date received > date "2026-04-26"))
    -- 读 (Mail.app 用 message id, 不是 entryID)
    set msg to (first message of inbox of account "Work" whose id is "X")
    set body to content of msg  -- 注: 不是 Outlook 的 "plain text content"
    -- 搜索
    set msgs to (messages of inbox whose subject contains "X")
    -- 起草: 草稿放 Drafts mailbox
    set draft to make new outgoing message with properties {subject:"X", content:"Y"}
    set visible of draft to true  -- 弹给员工 review
    -- 不调 send! 让员工自己看一眼再发
end tell
```

**坑**:
- AppleScript 字符串转义、CJK 编码 — 用 heredoc + UTF-8 osascript 参数
- 附件路径: Mail.app sandboxed, 附件读得到但写要 user 显式 grant
- **Automation 权限墙**: 第一次 osascript 调 Mail, macOS 弹 "Catfish wants to control Mail". 员工不点允许 → adapter `supports_drafts = False`, 降级到 EMLX 文件只读模式
- AppleScript dictionary 用 `Mail.app` 自带, 不是 Outlook for Mac — 命令名跟 Outlook 不同 (e.g. `content` vs `plain text content`)

**EMLX fallback (没 Automation 权限时)**:
- 读 `~/Library/Mail/V10/<Account>/<Mailbox>.mbox/<UUID>/Messages/*.emlx`
- emlx = RFC822 + plist header. Python 标库 `email.parser` 跟 `plistlib` 解析
- 只读, 写不了草稿 (写 emlx 后 Mail.app 不会重新索引)

### 4.2 Windows Outlook (`outlook_win.py`)

**机制**: `win32com.client.Dispatch("Outlook.Application")`。

**关键代码模式**:
```python
import win32com.client

ol = win32com.client.Dispatch("Outlook.Application")
ns = ol.GetNamespace("MAPI")
inbox = ns.GetDefaultFolder(6)  # 6 = olFolderInbox

# 列
items = inbox.Items
items.Sort("[ReceivedTime]", True)  # 倒序
for m in items:
    if m.UnRead and m.ReceivedTime > since:
        ...

# 起草 (不发)
mail = ol.CreateItem(0)  # 0 = olMailItem
mail.To = "x@y.com"
mail.Subject = "..."
mail.Body = "..."
mail.Save()  # 落到草稿箱; .Send() 不调!
```

**坑**: Outlook 必须在跑 (COM 启动它); 必须配过账号; 大邮箱 .Items 遍历慢, 用 .Restrict() 过滤。

### 4.3 Foxmail Windows (`foxmail_win.py`)

**机制 (读)**: 解析 `~/AppData/Local/Tencent/Foxmail7/Storage/<email>/Mail/<folder>/*.box`

**机制 (起草)**:
1. 生成 RFC 5322 .eml 内容 (Python `email.message.EmailMessage`)
2. 写到 `Storage/<email>/Mail/Drafts/`
3. 触发 Foxmail rescan (改 `.lst` 或 `.ind` 索引文件 + 等用户重启或主动按 F5)

**坑**: .box 二进制格式逆向; Foxmail 7 vs 8 vs 9 路径和格式不一致; rescan 触发不稳定 — **MVP 接受"员工 F5 / 重启 Foxmail 才看到草稿"** 的妥协, 文档里写明。

### 4.4 Foxmail Mac (`foxmail_mac.py`)

**机制**: 复用 `box_parser.py` 读 `~/Library/Containers/.../Foxmail/Storage/`, **`create_draft` 抛 NotSupportedError**。

---

## 5. SKILL.md 大纲 (跟 browser-compliance 同结构)

```
---
name: catfish-email
description: 帮员工读公司邮箱 + 起草回复 (Outlook / Foxmail 桌面客户端).
触发: "今天有什么邮件" / "回复张三那封" / "项目 X 邮件" / ...
不发送 (红线), 草稿落到草稿箱让员工自己 review + 点发送.
---

# catfish-email

## 何时调用
... (员工原话清单)

## 4 大模式
1. 列收件箱 (按未读 / 时间 / 发件人)
2. 读单封 + 附件 (附件名列表, 不下载除非员工说)
3. 搜索 (按主题 / 发件人 / 正文)
4. 起草回复 (LLM 生成 → 落客户端草稿箱 → quote 给员工预览)

## 红线
- 不自动发送 (技术上能, 政策上禁)
- 不删除 / 不移动 / 不标垃圾
- "标已读" 也要明示, 默认不动
- 起草必 quote 给员工预览, 等 explicit yes

## 失败降级
- 客户端没启 → 报员工"先开 Outlook/Foxmail 我再帮你"
- 草稿箱写失败 (Foxmail Mac) → 输出正文给员工复制粘贴
- 邮箱多账号 → 默认账号; 员工说"用 X 账号"才切

## 输出格式
- 列表 → markdown 表 (发件人 / 时间 / 主题 / 未读标记)
- 详情 → 卡片格式 (元信息 + 正文)
- 起草 → quote 块 (含完整收件人 / 抄送 / 主题 / 正文) + "确认我把它存草稿箱?"
```

---

## 6. 测试策略

| 类型 | 怎么做 |
|---|---|
| **box_parser** | fixture `.box` / `.ind` 文件 + 解析后跟期望值对比 (主题 / 发件人 / 正文) |
| **apple_mail** | mock subprocess (osascript) + 合成 EMLX 文件; 验证 AS 输出解析 + EMLX fallback chain + body_html 抽取 (50 tests) |
| **outlook_win** | mock `win32com.client.Dispatch`; 验证 COM 调用序列 (TODO) |
| **foxmail_win** | mock 文件系统 (pyfakefs / tmp_path); 写 draft 后验证 .eml 在指定目录 (TODO) |
| **foxmail_mac** | 复用 box_parser fixture; 验证 `create_draft` 抛 NotSupportedError (32 tests) |
| **draft** | mock gateway HTTP; 验证 prompt 含必要 context (in_reply_to body / 收件人 / 语气配置) |
| **inbox 工厂** | mock platform 检测; 验证返回正确的 adapter 实例 |
| **端到端** (真客户端) | 手工跑 + checklist; 自动化太脆 |

---

## 7. CLI 设计 (员工不直接用, 给 SKILL helper 调)

```bash
catfish-email list [--account=X] [--unread] [--since=today] [--limit=20]
catfish-email read <message-id> [--with-html]
catfish-email search <query> [--account=X] [--folder=Inbox] [--limit=30]
catfish-email draft \
    --to "x@y.com" --subject "S" --body-file /tmp/body.md \
    [--cc "z@y.com"] [--in-reply-to <message-id>] [--account=X]
```

输出统一 JSON (给 SKILL 解析)。`--human` flag 给员工自己跑时输出 markdown。

---

## 8. 起草 prompt 设计

`draft.py` 里给 gateway 的 prompt 模板:

```
你正在帮员工写邮件回复。

[原邮件]
发件人: {sender}
时间: {date}
主题: {subject}
正文:
{body_text}

[员工要求]
{employee_intent}

[语气]
{tone}     # 正式 / 简洁 / 友好, 默认"专业但不僵硬"

[规则]
1. 中文工作邮件: 称谓 + 正文 + "祝好" + 自己名字, 不堆套话
2. 英文工作邮件: 称谓 + 正文 + "Best regards" + 自己名字, 简洁
3. 引用原邮件用 ">" 块, 不超过 3 行
4. 收件人列表 / 抄送原样保留, 除非员工说改

输出: 只输出邮件正文, 不要解释, 不要包 markdown 代码块.
```

通过 `param_overrides` 给 gateway 传 `temperature: 0.4` (邮件要稳)。

---

## 9. MVP 里程碑

跟员工对齐的执行顺序 (跟员工日常工具优先):

1. **Day 1 · 脚手架 + draft + adapter base + Mac Outlook** (员工 Mac 日常)
2. **Day 2 · macOS Outlook 端到端跑通** (列 + 读 + 搜 + 起草)
3. **Day 3 · SKILL.md + helper + 真邮箱测**
4. **Day 4 · Windows Outlook (远程跑或员工 PC 测)**
5. **Day 5 · Foxmail .box parser**
6. **Day 6 · Foxmail Win draft 注入**
7. **Day 7 · Foxmail Mac (read-only) + 全平台 install.sh + 端到端**
8. **Day 8 · buffer / polish / 文档**

---

## 10. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| Foxmail .box 格式跨版本不兼容 | 部分员工读不了 | 用 magic byte 探版本号, 不支持版本报清楚错 |
| Outlook 不在跑时 COM/AppleScript 启不了 | 员工首次用必懵 | adapter 启动检测 + 提示员工"先打开 Outlook" |
| AppleScript 时延高 (~500ms / 命令) | 列 100 封要 50s | 列表用一个 AppleScript 批量返回, 不一封一个调用 |
| Foxmail rescan 不稳定 | 草稿写了员工看不到 | 文档明示"按 F5 / 重启看草稿"; 长期看 UI Automation |
| 多账号歧义 ("用哪个邮箱回这个") | 错发 | adapter 强制返回 account 字段; LLM prompt 包含原邮件账号; 起草默认沿用原账号 |

---

## 11. 跟现有组件的接口

| 组件 | 接入方式 |
|---|---|
| `central/llm-gateway` | `draft.py` 走 `POST /v1/chat/completions`, model 默认 `catfish-private-main` (内部数据安全), 员工配里能改 |
| `plugins/catfish-policy` | 加一条 rule: `tool_name: "send_email"` 完全 deny (即便 catfish-email adapter 没暴露, 也防其它 skill / agent 偷做) |
| `edge/branding/catfish` | 启动时检测一下是否装了 catfish-email skill, 没装的话 `catfish doctor` 输出"邮件 skill 未装, bash install.sh 装一下" |
| `edge/companion-app/Dashboard` | (P1.5) 加一张 "Email Inbox" 卡片显示未读数 (调 list 然后 count) |

---

## 12. 不做的事 (写明白以免日后争议)

- ❌ 不做 mail server / 不实现自己的 SMTP
- ❌ 不做"实时新邮件桌面通知" (那是 P1.5 daemon)
- ❌ 不集成 Webmail (公司没)
- ❌ 不集成 IMAP (公司不开)
- ❌ 不自动归档 / 删除 / 移动 / 标垃圾
- ❌ 不发送邮件 (永久红线)
- ❌ 不读其他人邮箱 (即便员工有权限)
- ❌ 不导出全员邮件备份
