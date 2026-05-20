---
name: catfish-journal
description: 改员工 ~/.catfish/employee_journal.md 里的 TODO 必选此 skill. **任何**改 journal TODO 的请求都用这个 — 标完成 / 删任务 / 新加 TODO. 触发词包括但不限于"标完成" / "已完成" / "搞定了" / "做完了 X" / "X 不做了" / "取消 X" / "删掉那个 X" / "把 X 加到今天 TODO" / "记一下 Y" / "新加任务 Z" / "把 P0 bug 标完成" / "周报写完了" / "那个 X 任务不用做了" / **跨 source: "把 X 那封邮件改成待办" / "今天的会都加进 TODO" / "这周要回的邮件全加上"** (5/20 v0.2.0). 直接读写员工本机 ~/.catfish/employee_journal.md 文件 (catfish 隐私设计: 员工 journal 是员工本机数据). 双重定位 line + hint 防误伤. 不自动加任何东西 — 员工 explicit 说改才改.
version: 0.2.0
author: 鲶鱼 Catfish Platform Team
license: MIT
metadata:
  hermes:
    tags: [journal, todo, productivity, catfish]
    related_skills: [catfish-email]
prerequisites:
  commands: [catfish-journal]
---

# catfish-journal

让小鲶通过对话改员工日记 TODO 状态 — 替员工"标完成 / 删除 / 新加".

# 跟 catfish 早安播报 (BriefingCard) 配套: 那里只读展示, 这里负责改. 员工在 chat
# 跟小鲶说话改 journal, 下次切早安 tab 看到变化.

---

## 何时调用

### 典型触发 (员工原话)

- "P0 bug 那个我搞定了"
- "周报已经写完了, 标完成"
- "今天那个汇报不用做了"
- "把 X 删掉"
- "记一下: 周四要给老李一份方案"
- "新加任务: 明天 3 点跟客户开会准备"

### 不该调用的场景

- 员工只是问"今天有什么 TODO" → 用 `catfish-journal list` 列出来, 但**不改**
  (列也是这 skill, 因为它读 journal)
- 员工想用 Apple Reminders / 飞书任务 → 不在这 skill 范围, 提示员工去对应工具
- 员工想改的不是 TODO, 是 journal 的其他段 (日记正文) → 让员工自己用编辑器开
  ~/.catfish/employee_journal.md 改 (我们不动 TODO 之外的内容)

---

## ⚠️ 红线 (绝对不能违反)

journal 是员工的真生产数据, 错改会损失. 永远不能:

- ❌ **批量 / 模糊匹配改** — 必须 line + hint 双重定位**单条**改, 一次最多 1 条
- ❌ **改 TODO 之外的内容** — 段标题 / journal 正文 / 笔记不许动, CLI 已 enforce
  (`delete` 拒绝非 TODO 行)
- ❌ **没 explicit 说就改** — 员工说"哦那个 X" 不够明确, 要确认"是要把 X 标完成 / 删 / 还是?"
- ❌ **bash sed / awk / 手写 Python 读写 journal** — 一律调 `catfish-journal` CLI,
  双重定位 + 白名单 regex 都在 CLI 里 enforce
- ❌ **加 TODO 加得太勤** — 员工随口说"明天看下 X" 不一定要加 TODO,
  问"要把这个加到 journal TODO 里?"再加

工具挂的正确反应:

1. `catfish-journal list --format=human` 直接命令行测, 看 journal 是否能读到
2. 看具体哪个子命令 / 哪条 line 挂, 报员工
3. 别建议 reinstall / 写代码绕过

---

## 工具 (通过 terminal 调)

```
catfish-journal list [--limit N] [--format json|markdown|human]
                                                        # 列未完成 TODO
catfish-journal done --line N --hint TEXT               # 标 - [ ] → - [x]
catfish-journal delete --line N --hint TEXT             # 删 TODO 整行
catfish-journal add "TEXT" [--section SECTION]          # 追加新 TODO
```

输出默认 JSON (机器可读). list 加 `--format=human` 切人类可读.

退出码:
- 0 = OK
- 2 = 参数错 / text_hint 不匹配 / line 越界 / 非 TODO 行 (拒绝改)
- 3 = journal 文件不存在 (`done` / `delete` 时, `add` 会自动建)

---

## 4 大模式

### 模式 1 · 列当前 TODO

员工: "今天有什么 TODO" / "我还没做完啥"

```
1. terminal: catfish-journal list --format=json
2. parse JSON, markdown 表格输出
3. 主动建议下一步: "要把哪个标完成?"
```

### 模式 2 · 标完成 (员工说做完了)

员工: "P0 bug 那个我搞定了" / "周报写完了, 标完成"

```
1. (如果还没拿过 TODO 列表) catfish-journal list --format=json 拿 line + text
2. 找 LLM 觉得员工指的那条 TODO (按 text 匹配; 模糊就跟员工确认)
3. catfish-journal done --line=<N> --hint="<text 前 10-20 字>"
4. 成功 → "✅ '...' 已标完成". 失败 (text_hint 不匹配) → 重拉 list 再试
5. 不要批量改 — 一次一条, 多条要员工 explicit confirm 每条
```

### 模式 3 · 删除 (员工说不做了)

员工: "那个 X 不做了" / "把 X 删掉" / "取消 Y"

```
1. catfish-journal list 找 line + hint
2. 跟员工 confirm: "确认要删 'X' 这条?" — explicit yes 才删
3. catfish-journal delete --line=<N> --hint="<前 10-20 字>"
4. 报员工: "🗑 删了 '...'"
```

注: delete 跟 done 区别 — done 留 `- [x] xxx` 在 journal 作"完成历史", delete 整行抹掉.
通常**优先 done, 真要砍才 delete**.

### 模式 4 · 新加 TODO

员工: "记一下周四给老李一份方案" / "明天 3 点跟客户开会, 加 TODO 提醒我"

```
1. 先 catfish-journal list 看 journal 有没有今天 / 明天的 section 标题
   (`## 2026-05-20` 这种)
2. catfish-journal add "<员工说的事>" [--section="<日期段>"]
   - 给定 section → 插到该段尾
   - 没给 → 追加到文末
3. 报员工: "📝 加了 '...' 到 [section]"
```

注: 不要自动加. 员工随口说"明天看下 X" 先问"要加到 TODO 吗"再 add.

---

## 输出格式约定

### 列 TODO

```markdown
**未完成 TODO · 5 件**

| line | text | section | source |
|------|------|---------|--------|
| 12 | 给老李写汇报 | 2026-05-20 | checkbox |
| 14 | 修 P0 bug | 2026-05-20 | checkbox |
| 23 | 联系供应商确认价格 | 2026-05-21 | inline |

要把哪个标完成 / 删 / 还是新加?
```

### 标完成成功

```markdown
✅ '给老李写汇报' 已标完成 (line 12).

还要改其它吗?
```

### 拒绝 (text_hint 不匹配 → 员工自己改了 journal)

```markdown
✗ 拒绝改 line 12 — 该行内容 'XXX' 跟你说的对不上, 我先重拉一下当前 journal...

(自动调 catfish-journal list 重列, 再问员工)
```

---

## Quick Examples

### Example 1: "P0 bug 搞定了"

```
你: P0 bug 那个我搞定了
小鲶: [terminal] catfish-journal list --format=json
小鲶: 找到 'L14 修 P0 bug'. 标完成?
你: 是
小鲶: [terminal] catfish-journal done --line=14 --hint="修 P0 bug"
小鲶: ✅ '修 P0 bug' 已标完成 (line 14).
```

### Example 2: "明天加一条 TODO 提醒跟客户开会"

```
你: 明天 3 点跟客户开会, 加 TODO 提醒我
小鲶: 加 'TODO: 3 点跟客户开会' 到 2026-05-21 那段?
你: 对
小鲶: [terminal] catfish-journal add "3 点跟客户开会" --section="2026-05-21"
小鲶: 📝 加好了, 明天打开早安 tab 你会看到.
```

### Example 3: 模糊指代 — 先 confirm

```
你: 那个不做了
小鲶: [terminal] catfish-journal list --format=json
小鲶: 你 journal 里 3 件未完成: L12 给老李写汇报 / L14 修 P0 bug / L23 联系供应商.
      哪个 "不做了"?
你: 联系供应商那个
小鲶: 确认删 'L23 联系供应商确认价格'?
你: 是
小鲶: [terminal] catfish-journal delete --line=23 --hint="联系供应商"
小鲶: 🗑 删了.
```

---

## 跨 source 案例 (BL-COMPANION-BRIEFING-V2 sub-task 3, 5/20)

让小鲶把邮件 / 日历转成 journal TODO. 走 chain skill 组合 (catfish-email
+ catfish-calendar + catfish-journal), **不引入新 native tool**, LLM 自己组合.

### Example 4: "把张三那封改成待办"

```
你: 把张三那封工资邮件改成待办
小鲶: [tool] catfish_email_search query="张三 工资"
小鲶: 找到 1 封: "5月工资单确认 — 张三 2026-05-19". 加 TODO "回复张三确认 5月工资单"?
你: 是
小鲶: [terminal] catfish-journal add "回复张三确认 5月工资单" --section="2026-05-20"
小鲶: 📝 加了, 早安 tab 工作计划行会显.
```

红线: 不要把整封邮件正文 copy 进 TODO text, **简洁动词短语 < 30 字**.
不抢标完成 — TODO 加进 journal, 员工自己回邮件后说"回了" 才标完成.

### Example 5: "今天的会都加进 TODO"

```
你: 今天那几个会都加 TODO 提醒我准备一下
小鲶: [terminal] osascript ... # 通过 BriefingCard / calendar tool 列今日会议
小鲶: 今天 3 个会: 10:00 客户对接 / 14:00 内部周会 / 16:00 产品 review.
      加 3 条 TODO?
你: 嗯, 加
小鲶: [terminal] catfish-journal add "准备 10:00 客户对接材料" --section="2026-05-20"
小鲶: [terminal] catfish-journal add "准备 14:00 周会汇报" --section="2026-05-20"
小鲶: [terminal] catfish-journal add "准备 16:00 产品 review 提案" --section="2026-05-20"
小鲶: 📝 3 条都加好了.
```

红线:
- **批量 add 仍是逐条调 CLI** — 不允许 `for e in events: add ` 一次性塞 — 单条 add
  保留员工对每条 confirm 的能力 (员工说"加" 后逐条问"这条 'X' 要加吗?" 可拆但太烦,
  实战 LLM 应 batch 报 "加 3 条" 一次 confirm 走整批)
- LLM **不要自动**调用 — 员工不 explicit 说"加进 TODO" 不行. "今天有 3 个会"
  这种问询**只列不加**

### Example 6: "本周邮件里要回的都加 TODO"

```
你: 这周收到的邮件里需要我回的都加进 TODO
小鲶: [tool] catfish_email_search query="" --account ALL  # 查本周全部
小鲶: 这周 8 封未读, LLM 评级: 🔴 急 3 / 🟡 中 2 / 🔵 低 3.
      只把 🔴 急 3 封加 TODO?
你: 嗯
小鲶: [terminal] catfish-journal add "回 老李 - 项目周报草稿" --section="2026-05-20"
... (省略 2 条)
小鲶: 📝 3 条已加. 低/中的没加 (你可以批量回复 / 跳过).
```

红线: **只加急的, 中/低 默认不加** — 防 TODO list 爆量. 员工想全加 explicit 说
"全加" 才走全量路径.

### chat-first 跨 source 设计思路

`catfish_email_search` (5/18 native tool) + `catfish-journal` (5/20 skill)
组合实现"邮件 → TODO". 日历同理 (calendar_today shell out + journal add).
**不为这个用例新加 native tool** — chain 现有 building blocks 让 LLM 自己组装,
跟 5/12 BL-MM9-FREEZE-v2 eis-checkin 复用 eis-login 同精神.

如果实盘发现 LLM 不会自然 chain (e.g. 调 email_search 后没 follow up
catfish-journal add), 5/21+ 再考虑加 native tool 兜底.

---

## 失败降级

| 场景 | 处理 |
|------|------|
| `catfish-journal list` 返空 | "你 journal 没未完成事项, 全清完了 🌊" |
| text_hint 不匹配 (exit 2) | "journal 可能你自己改过了, 我重列一下" → 自动 list 重拉 |
| line 越界 (exit 2) | 同上, 重列 |
| journal 文件不存在 (exit 3) | "你还没建 journal, 我帮你 add 一条它会自动建" (只 add 场景) |
| 员工 confirm 是, 但 done 失败 | 报错给员工, 别重试 (LLM 重试可能改错条) |

---

## 调试 hint (给开发者看)

```bash
# 命令行直接测
catfish-journal list --format=human
catfish-journal done --line=1 --hint='X'
catfish-journal add "test" --section="dev"
cat ~/.catfish/employee_journal.md

# 看 source
~/person_task/catfish/edge/journal-agent/src/catfish_journal/

# 跟 Rust 端 (Companion BriefingCard 用) 对齐
~/person_task/catfish/edge/companion-app/src-tauri/src/commands/journal.rs
```
