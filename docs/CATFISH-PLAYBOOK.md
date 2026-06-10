# 鲶鱼能力深度场景手册

> 客户售前 + 真机演示走查用. 不是产品规格书, 也不是营销稿. 选 6 个员工日常工作的高频场景, 一个一个把鲶鱼真做了什么走一遍, 含工作流截图位置 / 输入输出样例 / 常见疑问回答.
>
> 已有的 `ELEVATOR-PITCH.md` / `POSITIONING.md` / `COMPETITIVE-DIFFERENTIATION.md` / `COMPARE-1PAGER.md` 讲的是定位跟差异化, 本手册补的是"打开 Companion 那一刻, 鲶鱼具体怎么帮员工干活". 重叠的部分 (邮件起草 / 汇报公文 / 浏览器操作 / 复盘演练) 这里只点一下, 不再展开 — 那 4 个场景在 5 月已经 demo 过. 本手册聚焦还没系统讲过的 6 个场景.
>
> 客户场景里我们一律说"鲶鱼", 不说"catfish". UI 里员工看到的是"鲶鱼 Companion".

---

## 场景一: 一天怎么开始 — 早安参谋 + 任务对话

### 痛点

员工早上打开电脑, 邮件 50 封, 日历 6 个会, 待办清单上有 12 件事, 不知道先做哪个. 现有工具能列出来, 但不能告诉员工"这 12 件里哪 3 件是今天必须处理的".

### 鲶鱼怎么帮

**步骤 1 — 打开 Companion, 默认进早安 tab**

顶部一行: 问候语 + 日期 + 当前城市天气. 天气走 wttr.in 公开接口, IP 自动定位, 一天 4 次自然落点. 员工出差换城市, 临时加个城市名能并排看.

中间 8 秒, 鲶鱼参谋按员工档案 (tier=frontline/mid/senior) 给出"今天的主菜". mid 级别一般是 3-4 件项目主菜, frontline 是 5-8 件具体待办, senior 是 1-2 件战略级 + 异常例外. 主菜按急/中/低分组列在左边, 每件标着标题 + 一句话理由.

数据来源: 员工本机 `~/.catfish/` 里的邮件摘要 (50 封) + 日历 (今日 + 未来 7 天) + journal 待办 + 跨会话画像. 鲶鱼参谋自己读, 不上传中央, 一次推理出全部主菜.

**步骤 2 — 点一条主菜, 右边出详情**

右边不是死板的"目标 / 进度 / 建议"三段文字, 而是一个完整的对话框. 员工直接跟参谋说话:

> 员工: 今天先跟黄捷确认结项报告的两个细节, 然后再写整体材料, 顺序对吗?
>
> 鲶鱼: 顺序对. 两个细节优先级排一下:
> 1. **结项报告的具体提交时间点** (最好精确到"今天下班前"或"明天上午"), 确保不影响 6 月初的最终申报
> 2. **科创部准备的材料里是否包含"研发费用辅助账"或"人员工时表"** (这两项是高新认定必查项)
>
> 要不要我帮你列一份"高新申报核心材料核对清单"发给你, 方便你跟黄捷逐项过吗?

参谋知道这条主菜的上下文 (标题 / 紧急度 / 跟哪个项目相关 / 早晨已给过的 3 个建议口径), 不需要员工再描述一遍. 同时它能调工具帮员工真做事 — 起草邮件 / 跑数 / 查公司知识库 / 写报告.

**步骤 3 — 跨刷新对话不丢, 跨重启对话不丢**

员工跟主菜聊到一半, 切去看别的, 再回来对话历史还在. 关 Companion 第二天打开, 也在. 这件事的内部实现是, 每条主菜有一个稳定 ID, 跟标题完全脱钩 — 参谋每天会按当天情况重写主菜标题 ("CSMM-4 评估撰写" 可能变成 "CSMM-4 正式评估准备"), 但对话历史按稳定 ID 索引, 不丢.

**步骤 4 — 参谋下次刷新会"记得"上次聊过什么**

员工跟某条主菜聊过 5 轮 → 鲶鱼后台自动把对话摘成 100-150 字, 写进参谋的工作记忆里. 下次参谋重新出主菜时看到 "员工已经跟我聊过: 准备先发邮件给黄捷确认两项, 然后下周一前补研发费用辅助账", 它就不会再推同样的早晨建议, 而是按 follow-up 风格给出"今天最后一步是什么". 这个摘要按对话文件大小做版本指纹, 没有新对话就直接复用, 不重复烧推理.

### 输出样例

打开早安 tab, 员工在 1 屏幕看到:
- 当天天气 (San Jose 20°C Sunny)
- 4-6 条按急/中/低排序的主菜 (cache 命中秒出, cache 过期重跑 LLM 一般 10-30s, 客户内网千问慢时可能 60s 超时走 stale cache fallback)
- 默认处理的低优归档 (培训通知 / 安全预警 / 通用通知等, 由 LLM 按当天具体分类标)
- 选中任意一条 → 右侧直接进入对话, 不切 tab

### 客户常见疑问

**Q: 这跟 ChatGPT 区别在哪?**
ChatGPT 不知道你今天的邮件 / 日历 / 待办, 你得粘进去. 鲶鱼自己读, 一次合并出主菜. 对话内容只在员工本机, 不出端.

**Q: 鲶鱼怎么知道我的工作角色?**
首次启动 onboarding 时员工填或鲶鱼按邮件 / 日历样本反推 (画像置信度低于 0.3 时不给主菜, 提醒员工补充档案).

**Q: 多人同时用会冲突吗?**
每个员工本机一份 vault, 不互通. 跨员工只有部门级 wiki 沉淀 (见场景四).

---

## 场景二: 处理一批 Excel 表格 — 文件批处理工作流

### 痛点

月底, 各部门往员工邮箱发了几份 Excel: 销售台账 / 采购明细 / 费用报销. 员工要做的事:
1. 把销售台账按客户合并去重
2. 提取每份明细的"金额 > 5 万"行
3. 出一份月度汇总 + 同期对比图表
4. 把异常 (重复供应商 / 金额超出 50%) 标红
5. 包成 PPT 给老板看

之前要会写 Python / VBA / Excel 函数, 或者手动 4 小时.

### 鲶鱼怎么帮

**步骤 1 — 拖文件进工作台对话框**

工作台 chat tab 支持图片 + 文档附件 (image / pdf / xlsx / xls / docx / csv / txt / md). 一次最多 6 个附件 (Companion 当前 MAX_ATTACHMENTS=6, 文件多时可分批跑). 拖进对话框, 鲶鱼自动跑 preview 解析 (xlsx 抽出 sheet 名 + 总行数 + 前 20 行内容 + meta), 文件附件不全文上传 LLM, 只把 preview (约 5KB) 进 prompt. 完整文件存员工本机 `~/.catfish/uploads/<时间戳>-<文件名>`. 大文件 (≥50KB) 还生成 BM25 sidecar 用于检索. 解析在 Tauri 后端用 Python (`parse_file.py`) 走 openpyxl / pypdf 等做.

**步骤 2 — 一句话讲清要做什么**

> 员工: 这几个 Excel, 把销售台账合并, 提取所有 > 5 万的明细, 出月度汇总和环比图, 异常标红, 最后包成 PPT 给老板.

**步骤 3 — 鲶鱼分步骤跑工具**

鲶鱼按工作流拆几步, 一步一步调工具 (真工具名按 hermes / catfish-tool-bridge 注的):
1. `execute_code` 跑一段 pandas, 读销售台账 → 按客户去重 → 输出合并 csv
2. `execute_code` 再跑一段, 跨多份扫"金额 > 5 万"行
3. `execute_code` 算月度汇总 + 同期环比 + 异常检测
4. `catfish_run_skill` 调内置 xlsx skill 把汇总 + 图表写进新 xlsx (放 ~/.catfish/outputs/)
5. `catfish_run_skill` 调内置 pptx skill 按模板把核心数字 / 图表 / 异常列表写进 PPT

中间每一步, 员工看到对话流里有"工具卡片":

```
🔧 execute_code (跑 pandas 合并) ⏳ 进行中
  └ args: { code: "import pandas as pd; ..." }
```

跑完显示结果或错误, 可折叠看完整代码 / 输出.

**步骤 4 — 重要操作走人工批准**

`execute_code` 这类能跑任意代码的工具, 默认要员工点"批准"才执行. 顶部弹一个倒计时 60 秒的浮窗, 员工看一眼代码内容, 点"批准"/"本次始终"/"永久"/"拒绝". 60 秒没操作自动拒绝.

这是鲶鱼的双保险: LLM 调工具 + 员工最后一道确认. 跟客户 IT 关心的"AI 自动执行风险"对接 — 任何文件写入 / 代码执行 / 邮件发送都得员工点头.

**步骤 5 — 输出在本地, 直接打开**

最终 5 步都跑完, 对话里出现 2 个文件 pill:
- `5月销售汇总_2026-06-10.xlsx` [打开]
- `5月汇报_老板版_2026-06-10.pptx` [打开]

点击直接调系统默认应用打开. 文件全部在 `~/.catfish/outputs/2026-06-10/` 目录下, 员工可以自己直接到 Finder 看 / 编辑 / 发邮件.

### 输出样例

4 小时人工活, 鲶鱼花 3-5 分钟跑完 (含 LLM 推理 + Python 执行 + 文件写入). 中间每步员工只点 2-3 次批准. 全程对话内容 + 文件不出员工本机.

### 客户常见疑问

**Q: 跑 Python 代码安全吗?**
两层保险:
1. **员工批准** (默认开) — 任何 `execute_code` / `shell_exec` 调用都弹批准窗, 员工点了才跑
2. **沙盒** (可选, 客户内网部署时启用) — 设环境变量 `CATFISH_SANDBOX_EXEC=1` 后, macOS 走 sandbox-exec (SBPL profile 限文件系统 / 网络), Linux 走 nsjail (namespace + rlimit 限 CPU/mem/fork/fsize/fd). 资源默认: CPU 30s / 单文件 50MB / fd 256 / stack 8MB
默认两个都开员工批准, 沙盒按客户 IT 决定是否启用.

**Q: 文件会上传到 LLM 厂商吗?**
不会. 完整文件留员工本机, 只 preview (sheet 名 / 列头 / 前 20 行 + 元信息, 约 5KB) 进 prompt. 真要算数 / 合并的部分, 让 LLM 写代码, 代码在本机跑 pandas / openpyxl.

**Q: 复杂操作出错怎么办?**
工具卡片显示具体错误信息 (e.g. "sheet 名不存在") + LLM 自动调整再跑一次. 20 轮工具调用上限, 防死循环. 员工随时可以"停止"打断.

---

## 场景三: 月度规划 + 周中冲突 — 日历智能排期

### 痛点

月初做下月计划, 月中临时插单, 周三发现两个会撞了, 重要客户访问没安排车. 员工现有工具能"列出来", 不能"提醒先做什么、什么时候做、跟谁拼时间".

### 鲶鱼怎么帮

**月初规划**

员工早上跟早安参谋说:

> 员工: 月初了, 帮我看下这个月有哪些重要 deadline, 跟主要的几个项目交叉看下, 哪个周特别紧?

鲶鱼参谋拉本月日历 + 邮件里提到 deadline 的关键词 + 待办里有日期的, 合并出本月时间线. 它会主动说:

> 您这个月有 3 个关键节点:
> - **6/15** CSMM-4 评估材料截止 (今天到 6/15 还有 5 个工作日, 涉及您之前跟黄捷确认的 2 项材料)
> - **6/22** ITSS 评审 (评审组上周已发议程, 您还没回邮件)
> - **6/30** 高新申报最终递交
>
> 第 2 周 (6/15-6/19) 您日历上有 4 个会 + CSMM-4 截止 + ITSS 评审前的最后一轮材料 review. 建议这周末加排一个 2 小时块给材料 final check. 要不要现在帮您起草给评审组的回信确认参会?

**周中冲突**

员工突然被加了一个客户访问会, 跟原定 14:00 部门例会撞. 直接跟参谋说:

> 员工: 老李说今天下午 14:00 想见, 我下午有部门会, 帮我看下怎么排.

参谋自动:
1. 看 14:00 真有什么会 (部门周例会 / 必须本人参加吗?)
2. 看老李过去发起的会议是否能短一些 (是否有"半小时即可"的迹象 / 邮件里有没有说大概多久)
3. 看本人今天其他空档 (15:30-17:00 空, 16:00 有个 30 分钟评审)
4. 给出 2 个备选: "请老李 15:30 改约, 给他 1 小时" / "请部门会改去掉本人参加, 给老李 14:00 1 小时"

参谋给完不擅自发邮件, 让员工拍.

**会议邀起草**

确认 15:30 见老李后, 员工:

> 员工: 帮我起草一封简短的回老李, 说 14:00 有个绕不开的部门会, 改 15:30 在我办公室, 大概聊 1 小时, 谢谢理解.

参谋跑 `catfish_email_reply_draft` 工具, 5 秒出 5 句话回信, 落到 `~/.catfish/outputs/` 由员工自己看后改了发, 鲶鱼不替员工发.

### 输出样例

10 秒内员工得到完整的时间冲突分析 + 2 个备选 + 起草好的回信. 月度规划 30 秒内得到本月时间线 + 风险周 + 主动建议.

### 客户常见疑问

**Q: 日历跟谁的对接?**
当前 ship: macOS Calendar.app (走 osascript JXA + Swift EventKit FFI, 跟员工本机已配的账号同 — 比如员工 Calendar.app 里已经接了公司 Exchange / Google / iCloud, 鲶鱼就能看). Outlook 直连 EWS / 飞书日历 API 在 roadmap 里, 客户内网部署时跟 IT 谈优先级. 公司中央不需要专门配置, 每个员工自己授权 Companion 读自己日历.

**Q: 改约请求会自动发吗?**
不会. 鲶鱼只起草, 草稿在员工本机, 员工自己看了改了点"发送"才走出.

**Q: 团队日历能看到吗?**
能看公开时段 (busy/free), 看不到他人会议内容 (除非员工本人也是参会者). 这跟 Outlook / Calendar 的常规权限模型一致.

---

## 场景四: 知识库 — 写过的 / 学过的 / 问过的, 都能找回来

### 痛点

工作 2-3 年下来, 员工脑子里装的东西多: 哪个客户当年谁负责的 / 这条法规当时怎么解读的 / 那个 SQL 写过为什么这么写. 想找回来要翻邮件 / 翻飞书 / 翻文档库, 经常 30 分钟找不到. 离职时这些上下文全带走.

### 鲶鱼怎么帮

鲶鱼自动维护一个员工的个人 wiki, 三类内容三个目录:

**entities (具体的人 / 客户 / 项目)** — 你跟客户 A 公司打过的所有交道
**concepts (抽象的概念 / 政策 / 法规)** — "高新认定" / "ITSS 标准" 这种, 你怎么理解的
**queries (跨会话归档的问答)** — "上次问鲶鱼 CSMM-4 跟 CMMI-5 的区别, 它怎么答的"

### 数据怎么进 wiki

**方式 1 — 鲶鱼后台自动抽**

hermes 的 catfish-memory plugin 每 5 轮对话后会跑一次 LLM analyze, 自动抽 entity / concept 写进 `~/.catfish/wiki/`. 默认不区分"是新的还是老的" — 撞重名时靠后续 `merge_files_with_llm` 跑合并去重. 实测可能出现同一概念的多个文件, 用一段时间后由合并机制收敛. 员工不需要做任何事, 但偶尔需要打开 wiki tab 手动 review 一下合并结果.

**方式 2 — 一键归档**

工作台 chat 每条 assistant 消息底下有"💾 存 wiki" 按钮 (FeedbackButtons 组件). 点了, 这轮 Q&A 整段进 `~/.catfish/wiki/queries/<日期>-<主题>.md`, frontmatter 含 `sources: [chat]`. 关联实体抽取在 catfish-memory plugin 后台跑.

**方式 3 — 员工手写**

打开 Companion 的"知识体系" tab, 左边树结构 (entities / concepts / queries), 中间 markdown 编辑器, 直接 `+ 新建` 写自己的笔记. wiki 是开放的 markdown vault, 员工也可以直接用 Obsidian / VSCode 编辑 — Companion 不锁文件格式.

### 找回来怎么搜

三层搜索, 一个搜索框, 员工不用切模式:

1. **标题搜索** — 输入"高新", 0.1 秒返所有标题含"高新"的笔记
2. **全文搜索** — 标题没命中, 自动转全文 BM25, 找正文里提到的
3. **语义搜索** — 全文还没找到时, 走本机 BGE-M3 embedding (1.2GB 模型, 离线推理), 返"虽然字面没那个词, 但讲的是这件事"的笔记

切到知识体系 tab, 右侧还有一个 sigma.js 关系图谱: entity 跟 entity 之间通过 wikilink 自动连边, 员工点一个客户名能看到所有跟它有关的笔记, 一目了然.

### 跨员工沉淀 (roadmap — 当前未 ship)

个人 wiki 100% 留员工本机 `~/.catfish/wiki/`, **当前没有部门级 publish / 共享 vault 功能**. 想做的事 (员工同意后把某条 entity / concept publish 给部门, 新员工入职能看到老员工沉淀): wiki publish endpoint + 共享 vault 服务 + 审计 UI 还在 roadmap. 目前如果客户要做团队级知识共享, 走员工自己把 markdown 复制进公司已有的 Confluence / 飞书文档 (鲶鱼 wiki 是开放 markdown, 复制粘贴即可).

跨员工 skill 共享 (场景五 catfish_skill_publish + skills-hub) 是 ship 的, 思路一样但走另一条链路.

### 输出样例

员工 1 年下来 wiki 累积 100-300 篇 markdown. 这个 vault 是开放的, 跟 Obsidian / Logseq / VSCode 完全兼容, 员工换 Companion / 换公司也能带走自己的笔记.

### 客户常见疑问

**Q: 这跟 Notion / 飞书知识库区别?**
Notion / 飞书是要员工手动写. 鲶鱼是 hermes plugin 后台自动抽, 员工不主动写也会累积. 而且默认在员工本机, 不强制上传中央.

**Q: 老员工离职, 公司知识怎么留?**
当前 wiki 100% 在员工本机, 跟员工走 (跟你的 OneNote / Apple Notes 同款). 公司层面的知识沉淀靠员工主动 publish skill (场景五) + 已有的公司 KB (Confluence / 飞书). 部门 wiki publish 在 roadmap.

**Q: 中央能看员工个人 wiki 吗?**
不能. 中央服务对 wiki 内容是零可见, 也没有 wiki 相关的审计端点. wiki 只在员工本机.

---

## 场景五: 技能复用 — 一个员工的"自动化方法"传给整个团队

### 痛点

部门里张三发现一个绝活: 每月对账时, 走 5 个 Excel + 1 个 SQL + 一段固定话术发邮件给财务. 他自己每月省 2 小时. 但同部门的李四王五完全不知道有这个方法.

传统做法: 张三写文档 → 放 Confluence → 大家看 → 自己摸索复制. 实际上看的人少, 用的人更少, 文档很快过时.

### 鲶鱼怎么帮

**张三录技能**

张三某次对账时, 在 Companion 开 RecMode (录制模式). 跑完整套流程后, 鲶鱼自动把这次操作抽成一个可复用的 skill:
- skill 名: `对账邮件 5 步走`
- 输入参数: `month` (哪个月)
- 步骤: 5 步, 每步含调用的工具 / 参数模板 / 上下文
- 例子: "请把 5 月的对账邮件准备好"

`~/.catfish/skills/对账邮件_5_步走/` 自动落地. 张三看了一眼觉得 OK.

**张三共享给部门**

技能详情页"共享给部门"按钮, 点了走 `catfish_skill_publish` 工具上传到部门 skills-hub (独立 FastAPI 服务, 跑客户内网, 默认 port 8997). 上传前鲶鱼跑 3 层扫描 (代码在 `skill_publish.py`):

1. **凭据扫描** — password / api_key / sk- / 私钥
2. **PII 扫描** — 身份证号 / 手机号 / 工号 / 银行卡
3. **内网 URL 扫描** — RFC 1918 IP / .corp / .internal / eis / oa 域名

任一命中就**拒**, 让员工脱敏后重发. (自动替换路径成相对 / 把人名邮箱抽成参数 — 这两条在 roadmap, 当前是员工自己脱敏.)

3 层扫全过 → 走 SSO 拿 id_token → multipart upload 到 skills-hub `POST /skills/{namespace}`. hub 用 PG 存元数据 (版本 / 发布人 / 时间) + FS 存 `~/.catfish-hub/skills/{ns}/{name}/{version}/` 完整文件.

**李四装上即用**

李四打开 Companion 的 Dashboard, SkillsHubCard 每分钟刷新一次, 看到新增"对账邮件 5 步走 (张三贡献)". 点"安装" → 走 `catfish_skill_install` 工具:
1. 从 hub 拉 `ns/name/version` 到 staging 目录
2. dry-run 验证 (script.py 能 import + 有入口函数)
3. 重复检查 (名/描相似的已装 skill 列给员工)
4. 装到 `~/.catfish/skills/{namespace}/{name}/`
5. 自动注册进 `~/.hermes/config.yaml skills.external_dirs` → hermes 扫到 → LLM 能调

下次李四对账时跟参谋说"帮我准备 5 月对账邮件", LLM 看到 skill 列表里有它, 跑 `catfish_run_skill` 加载. skill 内部按张三录的步骤跑完.

### 治理

**版本管理** — hub 保留**全部历史版本** (不止 3 个), list_skills 返 all_versions 数组. 员工 install 时可指定 `@version`. 重装命中新版本时装新版 (主动推送"有升级"的 UI 提醒在 roadmap).

**审计** — hub 端用 skills_audit PG 表 + jsonl 兜底, 记 publish / delete 时刻 / 人 / 版本. Companion 端 `skill_audit.jsonl` 记每次 skill 调用的 ts / path / version / param_keys / duration_ms / ok.

**撤回** — 走 `catfish_skill_delete` (admin 接口, 软删到 `~/.catfish/skill-trash/`, 30 天保留期后真删). 已装员工的本机副本不会自动卸载, 下次他们 list_skills 时看到这版本被 hub 撤回, 自己决定卸不卸.

**离职** — 张三换工作, hub 里他贡献的 skill 仍在. 这是部门资产, 不跟人走.

### 输出样例

一个张三的"绝活"从他个人优化变成部门标准. 部门 Skills Hub 累积 20-50 个 skill 后, 新员工入职上手时间从 3 个月降到 2 周.

### 客户常见疑问

**Q: 装别人 skill 会跑别人代码吗?**
是的, 跟装 npm 包 / Python 包一样. 部门 Skills Hub 默认只允许部门内员工贡献, 不开外网, 跟"装别人不认识的 npm 包"风险等级不同. 也可以加 IT 审核 — 新 skill publish 后, IT 看一眼批准才上架.

**Q: skill 里能写恶意代码吗?**
能写但被两道防线挡:
1. **publish 前** 3 层扫 (凭据 / PII / 内网 URL) — 命中拒. (代码层面破坏 / 提权 / 网络外发 的检测在 roadmap, 当前主要靠员工批准)
2. **执行时** 员工批准弹窗 (能看到要跑什么代码) + 可选沙盒 (CATFISH_SANDBOX_EXEC=1 启用)

**Q: 跟 Office 宏 / Power Automate 区别?**
Office 宏只能在 Office 内, 鲶鱼 skill 是跨工具的 (邮件 + 文件 + 浏览器 + 数据库 + 任意 MCP 连接器). Power Automate 是流程图配置, 鲶鱼 skill 是 LLM 自然语言驱动 + 真的 Python 执行环境.

---

## 场景六: 数据 / 合规 / 离职清理

### 痛点

公司 IT / 合规 / 法务对 AI 助手的疑问:
- 数据存在哪
- 公司要追溯 3 年, 怎么留
- 员工跟 AI 聊的内容公司能不能审计
- 员工离职后怎么清理
- 出了事故 / 数据泄漏责任怎么定

### 鲶鱼怎么回答

### 数据存在哪 (默认架构)

- **员工跟 AI 的对话内容** — 员工本机 `~/.catfish/` 下 jsonl 文件. **不上传中央**.
- **员工档案 / 工作笔记** — 同上, 员工本机.
- **员工自动化技能 (skill)** — 员工本机 `~/.catfish/skills/`. 员工主动 publish 的进部门 Skills Hub.
- **中央服务知道的** — 员工今天用了几次 / 跑了什么 tool / 总 token 消耗 (metadata 级别). 不看内容.

### 公司怎么审计

中央 gateway 跑 LLM 请求时, 在本机 JSONL 留 metadata (`metrics.py` 接管, 不含对话内容):
- LLM 请求时间戳 + 员工 ID + 模型名 + 输入/输出 token + latency + status (ok / error)
- 配额 (`quota.py`) — 按员工日 / 月 token 额度跟踪 + 超额拦截
- approval 决策留痕 — 工具调用 + 员工选 (once / session / always / deny) 在员工本机 `~/.catfish/decisions.jsonl`
- skill audit (publish / delete / install / run) — hub 端 PG skills_audit 表 + 兜底 jsonl, 本机 `~/.catfish/skill_audit.jsonl`

中央 web UI 的 AuditPage 显这些 metadata. **不暴露**: 对话内容 / 草稿内容 / wiki 笔记内容.

**接 SIEM**: 当前 metadata 留本地 JSONL, 转 SIEM 走 log shipping (filebeat / rsyslog / fluentd 等客户已有方案). 鲶鱼自己没内置 SIEM forward, 由客户 IT 按已有 logging 基础设施接.

**要审计对话内容**: 当前**没有**中央触发员工本机内容快照的 endpoint. 必要时走员工本机 ssh / VDI 直接查 `~/.catfish/task_chat/*.jsonl` 跟 hermes session db, 走员工同意 + 部门主管申请 + 法务审批的多重流程, 在员工现场操作.

### 数据留存 (3 年合规要求)

- 员工本机数据 (~/.catfish/) 是 append-only 的 jsonl, 鲶鱼不主动删
- 员工本机数据备份走客户已有的端点备份方案 (OneDrive / 公司 NAS / Time Machine 等), 鲶鱼不另开备份通道
- 中央 metadata JSONL 默认不删, 留存时长按客户 SIEM / log shipping 策略走 (鲶鱼不内置 retention policy, 客户 IT 自己配)

### 员工离职清理

3 步:
1. **skills-hub 里他贡献的 skill** — 留在公司, 跟前任无关. (部门 wiki publish 当前不存在, 见场景四)
2. **员工本机个人 wiki / task chat / 草稿** — 跟员工走 (个人电脑的话员工自己处理; 公司电脑的话 IT 按现有离职流程清盘, 鲶鱼数据跟 OneNote / Apple Notes 同处理)
3. **中央 metadata JSONL** — 按客户 retention policy 处理. 鲶鱼自身不强制做 anonymize, 客户可在 SIEM 端跑 anonymization 脚本

### 出事故了责任怎么定

鲶鱼任何"做事"的操作 (发邮件 / 写文件 / 跑代码) 都走员工批准弹窗 + 决策留痕 (`~/.catfish/decisions.jsonl`):
- 时间戳
- 哪个工具
- 工具参数
- 员工点了"批准 / 拒绝 / 始终 / 永久"中的哪个
- 后续工具执行结果

合规 / 法务追溯责任时, 看员工本机的 decisions.jsonl 跟中央 gateway metrics JSONL 互相印证. 不存在"AI 自己决定发出去"的情况.

### 跟外部 LLM 厂商的关系

鲶鱼可接入: DeepSeek (国内) / 千问 (内网) / Claude (合规出海客户) / 公司私有 LLM. 客户可在中央服务配 LLM 白名单 (e.g. "只允许 DeepSeek 跟内网千问"). 跟 LLM 厂商的合规协议由客户自己签, 鲶鱼不介入.

对话内容到 LLM 厂商: 这是不可避免的 (LLM 必须看到 prompt 才能回答). 鲶鱼能做的:
- prompt 中只放员工当前问题 + 必要上下文 (preview 不传全文)
- 文件内容尽量让 LLM 写代码本机跑 (不进 prompt)
- 客户可选私有 LLM, 完全内网

### 客户常见疑问 (整套)

**Q: 我们是央企, 对数据出境敏感.**
私有部署 + 内网 LLM. 鲶鱼整套 (Companion 客户端 + 中央 hermes + gateway + identity-server + mcp-registry + secret-broker + web) 都能在客户机房 docker 跑, 中央服务连员工本机走内网, LLM 走客户的内网千问 / 私有 GPT.

**Q: 我们之前装过别的 AI 产品, 数据上了厂商云, 跑路了.**
鲶鱼是 Apache 2.0 开源 + 私有部署. 客户拿走源码自己跑, 不锁定. 数据全在客户机房, 厂商跑路不影响.

**Q: AI 给员工建议错了导致业务事故, 谁负责?**
鲶鱼是参谋, 不是代理. 任何"做事"动作都员工最后批准. 员工根据 AI 建议做的决定, 责任在员工 (跟员工用 Excel / Google 搜索后做的决定责任在员工同理).

---

## 附 A: 部署架构概览

鲶鱼整套含两端:

### 中央侧 (客户机房 docker compose)

- **gateway** — 主入口, 反向代理 + LLM 调用路由 + metrics 日志
- **identity-server** — SSO + OAuth + JWT 签发
- **mcp-registry** — 客户企业内可见的 MCP 连接器目录 (Slack / Jira / 内部数据库 等)
- **secret-broker** — 员工凭据加密存储 (AES-256-GCM + PG 持久化)
- **web** — 中央 web UI (audit / 配额 / 员工管理, 给 IT 用)
- **hermes** — LLM agent runtime (2 个 catfish 自家 plugin: **catfish-memory** + **catfish-xcatfish-user**, 跟 hermes 自带的 approval / sandbox / memory 等 plugin 并行)
- **skills-hub** — 员工 publish skill 的部门级 hub (FastAPI, port 8997, PG + FS 双层存储)

**部署规模 — 严肃说**:

LLM 推理是 GPU 大户. 真实部署规模见 `docs/DEPLOYMENT-50-USERS.md`:
- **PoC / 入门** (1-10 员工): 单卡 H20 96GB / 4 卡 H20 384GB, 跑量化后 qwen 72B 或更小
- **50 人并发 15** (推荐): 8×H20 单机 **768GB GPU**, 跑全精度 Qwen 122B + vision 30B 并发
- **100+ / 500 员工**: 多机集群, 含 LLM 服务多副本 + 网关 / identity / secret-broker / mcp-registry / hub 各自扩

**不含 LLM 的 5 个非 GPU service** (gateway / identity / mcp-registry / secret-broker / web / skills-hub) 单机 32GB 内存够 100+ 员工用 — 但 LLM 那台机器是另算的, 客户不能按"32GB 一台机器"做整个鲶鱼的预算.

### 员工侧 (Companion app)

- macOS 主推 (M 系列 + Intel 都支持)
- Windows 支持 (Tauri 2 跨平台编译)
- Linux 支持 (主要给开发 / 运维)
- 安装包 ~80MB (含 BGE-M3 embedding 模型 1.2GB 首次启动后台下载)

Companion 启动后通过 SSO 连中央, 拿到中央配置 (有哪些 LLM 可用 / 哪些 skill 装上 / 部门 wiki 地址), 之后日常工作 Companion 主要跟员工本机 + 中央 metadata 通讯.

### 网络拓扑

```
员工本机 Companion
    ↓ HTTPS (mTLS 可选)
中央服务 gateway (内网 only)
    ↓ 内网
hermes / mcp-registry / secret-broker / identity-server / web (docker compose)
    ↓ 内网或公网 (按配置)
LLM 厂商 API (千问 / DeepSeek / Claude) 或客户私有 LLM
```

LLM 出口是唯一的"出客户内网"的方向. 客户可配 LLM 白名单 / egress proxy / TLS 拦截审计.

---

## 附 B: 跟其他 AI 工具的简短对比

| | 鲶鱼 | ChatGPT 企业版 | Notion AI | 飞书 / 钉钉 AI 助手 |
|---|---|---|---|---|
| 工作上下文 (邮件 / 日历 / 待办自动读) | ✓ | ✗ | 部分 | 部分 (限飞书 / 钉钉内) |
| 对话内容默认存哪 | 员工本机 | 厂商云 | 厂商云 | 厂商云 |
| 工具调用 + 审批 | ✓ | 部分 | ✗ | ✗ |
| 跨工具自动化 (邮件 + 文件 + 浏览器 + DB) | ✓ | ✗ | ✗ | 限平台内 |
| 私有部署 / 数据不出端 | ✓ | 部分 (企业版受限) | ✗ | ✗ |
| 自动整理员工知识 / 部门沉淀 | ✓ (wiki) | ✗ | 半 (要手写) | ✗ |
| 员工自录 skill 部门共享 | ✓ | ✗ | ✗ | ✗ |
| 跟 LLM 厂商绑定 | 无 (随时切) | OpenAI | OpenAI / Anthropic | 厂商自定 |

对比详情见 `COMPARE-1PAGER.md`. 本表只列本手册涉及场景里的差异.

---

## 附 C: 演示 / 试点建议

### 30 分钟 demo 走法

1. **开 Companion 看早安主菜** (5 分钟) — 场景一前半
2. **跟某条主菜聊** (5 分钟) — 场景一后半 + 工具调用
3. **拖 Excel 进对话做合并** (8 分钟) — 场景二完整
4. **打开知识体系看个人 wiki** (5 分钟) — 场景四, 含搜索 + 图谱
5. **Dashboard 上看 SkillsHubCard + 装一个 skill** (3 分钟) — 场景五 (含从 hub 拉到本机 + dry-run + 注册 hermes)
6. **数据 / 隐私 / 部署 Q&A** (4 分钟) — 场景六 + 附 A

### 2 周试点建议

- 选 5-10 个员工 + 2 个 IT 接口人
- 部署单机 docker (32GB 一台)
- 接入员工本机 (macOS 为主)
- LLM 用客户内网千问 (或客户已有 LLM)
- 第 1 周: 员工自己用, 鲶鱼自动学画像
- 第 2 周: IT 看 SOM 审计 / 员工 publish skill 试

试点结束指标:
- 员工日均与鲶鱼交互次数
- 自动化 skill 被装 / 跑次数
- decisions.jsonl 里"批准"占比 (越高 = 员工越信)
- 鲶鱼让员工节省的时间 (员工反馈)

---

## 结语

本手册不是"鲶鱼有 100 个功能"的功能 list. 那种 list 见 `CAPABILITY-MATRIX.md`. 本手册是"打开 Companion 一天怎么用", 选了 6 个有代表性的工作场景, 让客户真感受到 AI 不是浮在工作上面的对话框, 而是融进员工每天具体工作流的工具.

售前 / demo 时按这份手册走, 客户能在 30 分钟内对鲶鱼有完整工作流认知, 比 PPT 翻 50 页有效率得多.

对接员工试点期间, 鼓励客户的员工自己跟参谋随便聊, 不要刻意按 demo 脚本. 真用起来后, 员工自己会找到鲶鱼对他工作里最有价值的那 2-3 个场景, 留下来. 客户 IT 看 SOM 数据, 也能看出哪些场景被高频用, 决定下一步推广方向.

---

*手册版本 2026-06-10. 后续更新跟 Companion ship 周期同步.*
*技术细节 / 部署配置 / 合规白皮书等内部 reference 见 docs/ 其他文件.*
