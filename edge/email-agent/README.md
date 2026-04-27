# catfish-email

> 鲶鱼平台的邮件 agent. 让小鲶替你**读**公司邮箱 + **起草**回复. **不自动发送**(红线).

源自实测约束: 公司邮箱不支持 IMAP / web / API, 只有桌面客户端
(Outlook / Foxmail). 我们直接读客户端的本地数据, 凭据由客户端自己管,
catfish 完全不碰密码.

详细设计: 见 [DESIGN.md](DESIGN.md).

---

## 当前状态

| 平台 + 客户端 | 列 | 读 | 搜 | 起草 | 状态 |
|---|---|---|---|---|---|
| **macOS Foxmail 1.5+** | ✅ | ✅ | ✅ | ❌(红线 + 写入不可靠) | **可用** |
| macOS Outlook | ⏳ | ⏳ | ⏳ | ⏳ | TODO (AppleScript) |
| Windows Outlook | ⏳ | ⏳ | ⏳ | ⏳ | TODO (pywin32 COM) |
| Windows Foxmail | ⏳ | ⏳ | ⏳ | ⏳ | TODO (.box parser 已有) |

---

## 装

```bash
cd edge/email-agent
bash install.sh
```

会做:
1. `pip install -e` 到 Hermes 的 venv (`~/.hermes/hermes-agent/venv`), 暴露
   `catfish-email` 命令
2. 软链 SKILL.md 到 `~/.hermes/skills/productivity/`, 重启 hermes 后能用

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
