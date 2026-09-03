# catfish-email

> 鲶鱼平台的邮件 agent. 让小鲶替你**读**公司邮箱 + **起草**回复. **不自动发送**(红线).

源自实测约束: 公司邮箱不支持 IMAP / web / API, 只有桌面客户端
(Apple Mail / Outlook / Foxmail). 我们直接读客户端的本地数据, 凭据由客户端
自己管, catfish 完全不碰密码.

详细设计: 见 [DESIGN.md](DESIGN.md).

---

## 当前状态

| 平台 + 客户端 | 列 | 读 | 搜 | 起草 | 状态 |
|---|---|---|---|---|---|
| **macOS Apple Mail** (Mail.app) | ✅ | ✅ | ✅ | ✅ (草稿落 Drafts) | **MVP 可用** (5/18 BL-EMAIL-APPLEMAIL-IMPL) |
| **macOS Foxmail 1.5+** | ✅ | ✅ | ✅ | ❌(红线 + 写入不可靠) | **可用** |
| Windows Outlook | ✅ | ✅ | ✅ | ❌ | **只读可用** (W2 pywin32 COM). 起草 / 发送 / 删除 / 标已读 6 个可选 method 待 W3 在真 Windows 机器上补 |
| Windows Foxmail | ✅ | ✅ | ✅ | ❌ | **只读可用**（读取本地 `.box` / `.eml`） |

> **5/17-18 BL-EMAIL-APPLEMAIL**: macOS 端从 Outlook for Mac 改 Apple Mail.app.
> 5/18 真 ship MVP — 5 方法走 AppleScript via osascript subprocess, 32 单测覆盖.
> 详见 DESIGN.md 1.2 + 4.1.

### Apple Mail 第一次用

macOS 第一次跑会弹 **"catfish wants to control Mail"** — **必须点允许**, 否则
`supports_drafts=False` 降级到只读. 不小心点了拒绝:
- System Settings → Privacy & Security → Automation
- 找运行 catfish 的 terminal / Catfish.app
- 勾上 Mail

Mail.app 没开时 → `ClientNotRunningError`, 先开 Mail 再用.

---

## 装

macOS / Linux:

```bash
cd edge/email-agent
bash install.sh
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File edge\email-agent\install.ps1
```

两边都做同样两件事:
1. `pip install -e` 到 Hermes 的 venv, 暴露 `catfish-email` 命令
2. 把 SKILL 目录挂到 hermes 的 `skills/productivity/`, 重启 hermes 后能用

路径按平台不同 —— Windows 的 hermes 在 `%LOCALAPPDATA%\hermes` 而不是
`~/.hermes`, 可执行文件在 `venv\Scripts\catfish-email.exe` 而不是 `venv/bin/`。
`install.ps1` 头部注释逐条列了这四处差异。

Windows Foxmail 不需要把账号再次“关联”到 Companion：适配器直接读取 Foxmail
已经同步到本机的 Storage 数据。默认探测 `%LOCALAPPDATA%` 和 `%APPDATA%` 下的
Foxmail7/Foxmail Storage；如果企业版使用了自定义目录，可设置
`CATFISH_FOXMAIL_ROOT` 指向包含账号目录的 `Storage` 目录。首次启动会由
Companion 自动安装 `catfish-email`，不需要员工手动执行 PowerShell。

当前 Windows Foxmail 是只读能力：列账号、列收件箱、全文搜索、打开正文和读取
附件元数据；起草、删除、发送仍明确提示去 Foxmail 操作，不直接修改 Foxmail
本地数据库。

> **install.ps1 还没在真 Windows 机器上跑过。** 其中 hermes 读 skills 的目录
> 是按 `_phase1_win_install_hermes.ps1` 的 `$HermesHome` 约定推导的, 仓库里
> 没有 Windows 先例可对照。装完 banner 里没出现 catfish-email 的话, 用
> `.\install.ps1 -SkillsDir <正确路径>` 指过去。

---

## 命令行用法

```bash
# 列账号
catfish-email accounts --human

# 列收件箱前 20 封
catfish-email list --limit=20 --human

# 只看今天的未读
catfish-email list --since=$(date +%F) --unread --human

# 按发件人筛
catfish-email list --sender=张三 --human

# 全文搜
catfish-email search "周报" --human

# 读单封 (id 从 list/search 拿)
catfish-email read --id "foxmail-mac|706574875@qq.com|12345" --human
```

JSON 输出 (默认): SKILL helper / 脚本调用用; `--human` 切 markdown 给员工自己看.

---

## hermes 里用

装完 + 重启 hermes 后, 自然语言就行:

```
你: 今天有什么邮件没回?
小鲶: (调 catfish-email list --since=today --unread, 列 markdown 表给你)

你: 张三那封打开
小鲶: (调 catfish-email read --id ... 给你看正文)

你: 帮我回他
小鲶: (LLM 生成回复正文, quote 给你预览, 等你说"OK 我去客户端粘贴发")
```

**不会自动发邮件** —— 起草后让你自己开客户端 review + 点发送, 这是红线.

---

## 数据来源

### Foxmail Mac 1.5+

数据在沙盒容器:
```
~/Library/Containers/com.tencent.Foxmail/Data/Library/Foxmail/Profiles/<email>/
├── messages.db                              ← SQLite (mailinfo / boxes / mail_box_info / mail_fts / attachmentinfo)
└── Mail/<folder_id>/<bucket>/<mailid>.mail  ← 单文件 RFC822
```

只读访问 (`mode=ro` SQLite URI), 不动 Foxmail 自己的写入.

---

## 测试

```bash
PYTHONPATH=src python -m pytest tests/
# 32 passed
```

不依赖真 Foxmail 安装, conftest.py 里 factory 合成完整 Profile 目录.
