---
name: catfish-email
description: 看邮件 / 列邮件 / 搜邮件 / 起草回复必选此 skill, 不走 himalaya / mutt / IMAP. 触发: 今天几封新邮件 / 未读 / 回复 X 那封 / 搜 X 邮件 / 起草回信 / 看老板邮件. 直读员工本地 Apple Mail / Foxmail / Outlook 客户端数据, 不需密码. 红线: 不自动发, 草稿写到 Drafts 员工自发. 当前 macOS Apple Mail + Foxmail (只读), 后续 Windows.
version: 0.2.0
author: 鲶鱼 Catfish Platform Team
license: MIT
metadata:
  hermes:
    tags: [email, apple-mail, foxmail, outlook, productivity, catfish]
    related_skills: [catfish-browser-task]
---

# catfish-email

让小鲶替员工**读**公司邮箱 + **起草**回复, 不自动发送.

源自实测约束: 公司邮箱不支持 IMAP / web / API, 只有桌面客户端
(Outlook / Foxmail). 我们直接读客户端的本地数据 (Foxmail SQLite + .mail 文件;
Outlook 走 AppleScript / COM), 凭据由客户端自己管, catfish 完全不碰密码.

---

## 何时调用

### 典型触发 (员工原话)

- "今天有什么邮件"
- "把未读的列出来"
- "查张三上周给我发的"
- "项目 X 相关邮件有哪些"
- "把昨天那封带 PDF 的找出来"
- "回复李四告诉他 Y" (起草)
- "搜下'合规'相关邮件"
- "帮我看下最新的那封 GitHub 通知"

### 不该调用的场景

- 员工要发邮件给陌生人 → **永远不能**, 不在能力范围. 让员工自己打开客户端写
- 员工要订阅 / 退订邮件列表 → 让员工去客户端操作
- 公网邮箱 (Gmail / iCloud) → 这个 skill **就是**桌面客户端路径 (员工自己在 Mail.app
  里把 Gmail / iCloud 加成账号, catfish 通过 AppleScript 读). 不需要"另外的 IMAP 模式"

---

## ⚠️ 红线 (绝对不能违反 — 反设计建议会被项目主一票否决)

这 skill 故意不走 IMAP / SMTP / web API. 当你看到工具挂 / 数据不全, **永远不要建议**:
- "切到 IMAP 直连模式更稳" — ❌ 立项前提就是企业邮箱**没开** IMAP / web / API,
  这是 FFCS 等核心客户场景的硬约束, 不是工具缺陷
- "用 Gmail API / Microsoft Graph" — ❌ 同样: 用了就要碰员工密码 / OAuth 凭据,
  违 catfish 隐私设计原则 (`docs/PRIVACY-PRINCIPLES.md`: catfish 完全不碰密码)
- "走 himalaya / mutt / mu / notmuch" — ❌ 都是 IMAP 客户端, 同样违红线
- "查 ~/Library/Mail/V*/MailData/Envelope\ Index 这个 sqlite" — ⚠️ 不算违红线
  但**绕过 catfish-email CLI**, 不走 adapter 抽象, 输出格式不稳, 不要这么搞

工具挂的正确反应:
1. 看 `catfish-email accounts` 是否列出账号 (没有 → Mail.app 没开 / Automation 权限没给)
2. 看具体哪个账号 / 哪个 folder 挂 → 报错信息给员工 + 建议他自己开 Mail.app 看
3. **挂的时候说"我这查不了, 你自己打开 Mail.app 看"** —— 比建议反设计方案诚实

---

## 工具 (通过 terminal 调)

```
catfish-email accounts                                  # 列所有账号
catfish-email list [--folder=Inbox] [--unread] [--since=2026-04-26]
                   [--sender=张三] [--subject=周报] [--limit=20]
catfish-email read --id <message-id>                    # 读单封全文
catfish-email search "<关键词>" [--limit=20]             # 全文搜索
```

输出默认 JSON (机器可读). 加 `--human` 切 markdown.

退出码:
- 0 = OK
- 1 = adapter 不可用 (没装 Foxmail / Outlook)
- 2 = 参数错
- 3 = 邮件 id 找不到 (read 时)

---

## 4 大模式

### 模式 1 · 列收件箱 + 筛选

员工: "今天有什么邮件" / "把未读的列出来" / "查张三给我发的"

```
1. terminal: catfish-email list --since=<today> --limit=20 --json
2. parse JSON, 排序 / 截断后 markdown 表格输出给员工
3. 主动建议下一步: "要看哪封? 我读详情给你"
```

### 模式 2 · 读单封 + 附件

员工: "把第一封打开" / "看那封 GitHub 的"

```
1. (前置: 已经在列表场景拿过 message id)
2. terminal: catfish-email read --id <id> --json
3. 抽 subject / sender / date / body / attachments 给员工
4. 如果带附件且员工问"附件里啥": 提示员工自己打开客户端看 (我们目前不下载附件)
```

### 模式 3 · 全文搜索

员工: "搜下'合规'" / "项目 X 邮件" / "查李四给我的"

```
1. terminal: catfish-email search "<关键词>" --limit=10 --json
2. 输出 markdown 表 (主题 / 时间 / 发件人)
3. 主动追问: "要看哪封?"
```

注: Foxmail Mac 的 FTS (中文分词) 在某些 Python sqlite3 编译版本下不可用, 自动
退化到 LIKE 子串匹配, 速度仍然 ms 级.

### 模式 4 · 起草回复 (通过 LLM 自己生成)

员工: "回复张三告诉他 Y"

```
1. terminal: catfish-email read --id <被回复的邮件 id>  → 拿原文
2. 自己用 LLM 生成回复正文 (中文工作邮件套路: 称谓 + 正文 + 祝好 + 名字)
3. 把生成的内容 quote 给员工预览, 含: To / Cc / Subject (Re: ...) / Body
4. **STOP 等员工 explicit yes**
5. 拿到 yes → **不调 catfish-email 发送** (红线), 而是输出"复制以下到客户端
   写邮件框" 让员工自己粘贴 + 自己点发送
```

注: Foxmail Mac 不支持自动建草稿 (写入数据库 + 触发 rescan 不可靠), 必须走
"复制粘贴让员工手动" 这条降级路径. Outlook 后续支持后会自动建 draft (但仍不
自动发).

---

## ⚠ 红线

这些操作**永远不能**做, 即使员工说 "yes":

- ❌ **自动发送邮件** (绝对红线; 员工必须自己开客户端点发)
- ❌ **删除 / 移动 / 标垃圾** (不可撤回)
- ❌ **改邮件状态** (标已读 / 取消标星) — MVP 一律拒绝, 默认只读不动
- ❌ **导出 / 下载附件** — MVP 不下载, 让员工自己开客户端打开
- ❌ **读其他人邮箱** — 即便员工有访问权限
- ❌ **绝对不要调用 `skill_manage(action="delete")` 删除任何 catfish-* skill**, 包括 catfish-email 自己。如果命令调用挂了, 报员工"catfish-email 命令跑不通, 让你的开发者重装", **不要试图 reinstall / delete / repair skill**。

## 命令找不到时的处理 (重要)

如果 `terminal` 调 `catfish-email` 报 "command not found":
1. **不要**调用 `skill_manage` 删除或 reinstall (那是更深的破坏)
2. **不要**自己写 Python 代码读 SQLite (绕过 catfish-email CLI 是反模式)
3. 直接报员工: "catfish-email 命令在你 Mac 上没装好。请运行: `cd ~/person_task/catfish/edge/email-agent && bash install.sh`"

特殊提醒:
- 起草时必须把 **完整的 To / Cc / Subject / Body** quote 给员工看, 不能省略
- 收件人列表不要"自动猜" — 原邮件的 Reply-To / From / Cc 沿用, 员工说改才改
- 看到附件不要自动假设员工要内容. 问员工 "要看附件吗" — 我们现在还不能下载,
  让员工自己开客户端

---

## 失败降级

| 场景 | 怎么处理 |
|------|----------|
| `catfish-email accounts` 返回空 / exit 1 | "你的 Foxmail / Outlook 还没配账号? 打开客户端加一个再试" |
| 客户端没在跑 (Outlook 报错) | "先打开 Outlook 我再帮你" |
| Foxmail Mac 的 .mail 文件丢了 (邮件被自动清理) | adapter 自动 fallback 到 abstract; 可以告知员工 "完整正文不在了, 只有摘要" |
| 中文搜索结果异常 | FTS 退化到 LIKE 已经处理, 一般员工无感 |
| 起草后员工反悔 | 不发送, 提示员工"OK, 草稿你自己保留" |

---

## 输出格式约定

### 列邮件

```markdown
**收件箱 · 5 封 (今天)**

| 状态 | 时间 | 主题 | 发件人 |
|------|------|------|--------|
| ● | 14:30 | [GitHub] PR #42 review | github@... |
| ○ | 09:15 | 周报 4-26 | 张三 |
| ● | 08:00 | Newsletter Q2 | newsletter@... |

要看哪封? (说"第 1 封" / "GitHub 那封" / 给我 message id)
```

### 读单封

```markdown
**[GitHub] PR #42 review** (来源: foxmail-mac)

- 发件人: GitHub <noreply@github.com>
- 收件人: hongbo@x.com
- 时间: 2026-04-26 14:30
- 状态: 未读
- 附件: 无

---

(正文 ...)

要回复? 我帮你起草
```

### 起草回复 (员工说"回复 Y" 时)

```markdown
**起草草稿** (准备复制到 Foxmail / Outlook 撰写窗口):

To: 张三 <zhang@x.com>
Cc:
Subject: Re: 周报 4-26

---

张三您好,

(LLM 生成正文)

祝好,
鸿波

---

确认这版? (确认我就把它打印出来你复制粘贴; 想改告诉我哪里不对)
```

---

## Quick Examples

### Example 1: "今天有什么邮件没回"

```
你: 今天有什么邮件没回?
小鲶: [terminal] catfish-email list --since=$(date +%F) --unread --limit=20 --json
小鲶: 收件箱今天 3 封未读:

| 时间 | 主题 | 发件人 |
|------|------|--------|
| 14:30 | [GitHub] PR #42 review | GitHub |
| 09:15 | 周报 4-26 | 张三 |
| 08:00 | Newsletter Q2 | superlinear |

哪封要先看 / 回?

你: 张三那封
小鲶: [terminal] catfish-email read --id foxmail-mac|...|12345 --json
小鲶: ...(展示内容)... 要回复? 我先帮你起草.
```

### Example 2: "搜合规相关的"

```
你: 搜下"合规"相关邮件
小鲶: [terminal] catfish-email search "合规" --limit=10 --json
小鲶: 找到 7 封提到"合规"的邮件:
... (markdown 表)
```

---

## 调试 hint (给开发者看)

如果 skill 行为不对:

1. 直接命令行测: `catfish-email accounts --human`, 应该列出账号
2. `catfish-email list --human --limit=3` 看是否拿到邮件
3. `~/Library/Containers/com.tencent.Foxmail/Data/Library/Foxmail/Profiles/<email>/messages.db` 这个 SQLite db 用 sqlite3 cli 打开能读
4. Foxmail 改版导致 schema 不一致 → 看 `edge/email-agent/src/catfish_email/foxmail_db.py` 里的 schema 注释更新
