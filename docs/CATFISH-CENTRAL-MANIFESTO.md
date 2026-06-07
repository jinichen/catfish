# Catfish 中央服务设计宣言 (Manifesto)

> 这是 catfish 战略 / 战术系列的**第 5 份, 也是最重要的一份** — 它把 "员工主权 / 中央 0 控制 / 数据零出端" 从 ideology **形式化到 API surface 设计**.
>
> 跟其他 4 份的关系: PATENT (法律 moat), MOAT-ASSESSMENT (战略 moat), CAPABILITY-GAPS (战术 must-have), EXAMINER-AUDIT (patent 概率). 本份是**产品 manifesto + 架构边界**.
>
> 关键 decision (鸿波 2026-06-07 确认): 走严格 "无强制中央" 路线, **不留 soft 强制口子**. 这份 doc formalize 这个 decision.

---

## 0. 4 条核心公理 (Axioms)

### 公理 1 (员工主权 / Employee Sovereignty)

员工 own 自己跟 catfish 产生的**所有内容**:
- 完整对话 (prompt / response / tool args / tool result)
- 屏幕录制
- 自录 skill (RecMode freeze 产物)
- 个人 wiki / 笔记
- 本地配置

这些内容是**员工个人资产**, 不是公司财产. 类似:
- 员工拿公司发的电脑写的私人文档 — 是员工的
- 员工用公司 Slack 发的工作消息 — Slack 在中间, 但内容主权在员工
- catfish 走最强姿态: **不只是员工有主权, 公司物理上拿不到**

### 公理 2 (数据零出端 / Data Stays at Edge)

公理 1 的技术实现. 员工内容**永远不离开员工本机**, 包括:
- 不上传中央 (catfish-gateway / identity-server / 第三方 LLM API 流量除外)
- 不上传到任何企业 SaaS (cloud backup / sync / 协作 服务)
- 不通过 catfish 中央服务**反向取回** (即使员工同意, catfish 中央服务的 API 也不提供这条路径)

例外条款 (经员工**主动操作**触发):
- 员工自己 `catfish_skill_publish` 发布 skill 到团队 marketplace (内容是脱敏后的 skill, 不含原始对话)
- 员工自己 LLM 调用走 cloud model (Anthropic / OpenAI 调用流量必经第三方, 这是不可避免, 但 catfish 中央服务**不接触**这部分流量)

### 公理 3 (中央 0 控制 / Zero Central Control)

中央服务 (catfish-gateway / identity-server / metering / advisory) **不能强制员工设备做任何事**:
- 不能远程 wipe 员工本机数据
- 不能远程禁用 / 卸载 skill
- 不能强制推送配置 / 策略
- 不能强制升级版本
- 不能远程禁止 catfish 启动
- 不能远程查看员工本机状态 (设备清单 / 在线状态 / 已装 skill 列表 / 等等)

### 公理 4 (API surface 物理无能 / Architectural Powerlessness) ⭐

**这条最关键**. 公理 3 不是"我们承诺不做" (基于信任), 而是**catfish 中央服务的 API 物理上不提供这些能力**.

形式化定义: **catfish-gateway / identity-server 的全部 HTTP / RPC API surface, 没有任何 endpoint 能 (a) 强制员工本机执行操作, (b) 反向查询员工本机状态**.

后果:
- catfish 中央服务**审计可证明** — 国资委 / 等保 / 第三方安全审计**通过查 API surface 就能验证**, 不需要信任 catfish 团队的口头承诺
- 跟主流 fleet (CrowdStrike / Microsoft Intune) 区分开 — 他们的 API 设计上**就能** push / wipe / inventory, 哪怕"我们承诺不做"也是不可审计的
- 这是 catfish 的**结构性 moat** (counter-positioning) — Anthropic Enterprise / Microsoft Copilot 的 business model 依赖 fleet, 让他们做"物理无能"自杀

---

## 1. 现状审视 — 现有 API surface 跟 4 公理对照

基于 grep `central/identity-server/src/` + `central/llm-gateway/src/` 的 actual endpoint:

### ✓ 完全合规 (28 个)

| Endpoint | 服务 | 合规理由 |
|---|---|---|
| `GET /.well-known/openid-configuration` | identity-server | OIDC metadata (公开 spec) |
| `GET /.well-known/jwks.json` | identity-server | JWKS public keys (公开) |
| `GET /authorize` + `POST /authorize` | identity-server | SSO 鉴权 (员工主动) |
| `POST /token` | identity-server | OAuth token exchange (员工主动) |
| `GET /userinfo` | identity-server | 员工查自己 info |
| `POST /registry/register` | identity-server | agent 自愿注册 |
| `GET /registry/lookup` + `/list` + `/agents/:sub/jwks.json` | identity-server | agent 公开查询 (类似 DNS) |
| `GET /healthz` | both | healthcheck (无业务数据) |
| `GET /api/quota/me` | llm-gateway | 员工查**自己** quota |
| `GET /api/me` | llm-gateway | 员工查**自己** identity |
| `POST /api/learn/start_recording` | llm-gateway | 员工**主动**启动录屏 (录屏数据本地, 不上传) |
| `GET /admin/me-as-admin` | identity-server | admin 自查身份 |
| `GET /admin/departments` + `/departments/:name` + `PUT /departments/:name` | identity-server | 部门组织结构 (跟员工设备无关) |
| `GET /admin/users-audit` | identity-server | **元数据**审计 log (登录次数 / 时间, 不含对话) |
| `GET /skills` + `/skills/:ns/:name` + `/skills/:ns/:name/:version` + `/skills/.../files/:file_path` | skills-hub | **Pull-based** skill marketplace (员工自己 pull) |
| `GET /mcp_registry/registry` + `/manifest/:connector_id` | mcp-registry | **Pull-based** MCP 公开列表 |
| `POST /mcp_registry/subscribe` + `DELETE /subscribe/:id` + `GET /subscribed` | mcp-registry | 员工**自己** subscribe MCP |
| `GET /v1/audit/department` | llm-gateway | 部门**聚合**审计 (metadata only) |

### ⚠ 灰色 — 需要 reframe 或加 employee consent (6 个)

| Endpoint | 当前实现 | 问题 | reframe 建议 |
|---|---|---|---|
| `GET /admin/users` | admin 列所有用户 | 中央可见用户列表 | OK — 跟员工设备无关. 用户列表 = 企业 license seat 清单, 类似 GitHub Org members |
| `POST /admin/users` | admin 创建用户 | 强制给员工分配 SSO seat | OK — 这是企业分发 license seat, 不是 push 到员工设备. 员工不接也行 (员工 SSO 没绑就用不了中央 LLM, 但 catfish 本机能跑) |
| `PUT /admin/users/:email` | admin 改用户资料 | 改 email / 部门可能影响 SSO 关联 | OK — admin 改的是中央 license seat 元数据, 不影响员工本机 |
| `DELETE /admin/users/:email` | admin 删用户 | 撤销 SSO seat | OK — 员工不能再用公司 LLM 配额, **但员工本机 catfish 数据 100% 不受影响**. 跟"远程 wipe"完全不一样 |
| `POST /skills/:namespace` | 员工 upload skill 到 hub | 员工自愿上传, 但需要 IT 审定流程? | OK — 但要明确"员工自愿 publish" 不是"自动 sync" |
| `DELETE /skills/:ns/:name/:version` | 删 skill | 谁能删? 中央能删别人的? | reframe: **员工只能删自己 upload 的 skill**. Admin 可以**下架 (unlisted)** 不可见但不能删除. 删除等于篡改员工 contribution history, 违反公理 1 |

### ❌ 违反公理 — 必须 reframe 或废弃 (3 个)

| Endpoint | 当前实现 | 违反 | reframe |
|---|---|---|---|
| **`POST /admin/users/:email/lock`** | admin lock 员工登录 | 违反公理 3 (强制) | **reframe**: 改为 "撤销 SSO seat". 实质是 — 员工**不能再用公司中央 LLM 配额**, 但本机 catfish + 员工自己的 BYO API key 仍能用. lock != disable, **lock 是"撤销公司福利", 不是"强制禁用员工**" |
| **`POST /admin/users/:email/reset-password`** | admin 重置员工密码 | 违反公理 3 (强制改员工凭证) | **reframe**: 改为 "**员工自助密码重置 + admin 协助**". 员工触发 reset flow (email link), admin 帮忙发 magic link, **不能直接 set 新密码**. 类似 GitHub: 你 admin 也不能改别人密码, 只能 disable account 然后通知用户 reset |
| **`POST /tool_archive/read` + `GET /:ref` + `POST /gc`** | 工具结果归档到中央 | **严重违反公理 2 (数据零出端)** | **重大决策**: 这套 tool result 中央归档**完全废弃**. 工具结果应该全部留员工本机 (catfish SQLite). 如果有团队共享需求, 走员工自愿 publish 走 facts router |

### 现状评分

- ✓ 完全合规: 28 / 37 = **75.7%**
- ⚠ 灰色 reframe: 6 / 37 = **16.2%**
- ❌ 违反: 3 / 37 = **8.1%**

**好消息**: catfish 中央服务从一开始就走 employee-pull / metadata-only 设计, 大部分合规. **少数 endpoint 需要 reframe 或废弃, 工程量 1-2 周**.

---

## 2. API 边界 formal spec — "无强制中央" 的 catfish-gateway

### 允许 (provide service)

中央服务**可以做**这些事 (员工自愿 / pull-based):

```
✓ SSO 鉴权 (员工主动 login)
   POST /token, GET /authorize, GET /userinfo
   
✓ License 颁发 / 校验 (员工 / admin 拿 license)
   POST /admin/users (admin 颁), GET /api/me (员工查)
   
✓ Metering 接收 (员工 catfish 自愿上报 metadata)
   POST /v1/metering/usage (含: email, ts, model, tokens, cost; 不含: prompt, response, tool args, tool result)
   
✓ Quota 查询 (员工查自己 / 部门 admin 查部门聚合)
   GET /api/quota/me, GET /v1/audit/department
   
✓ Advisory feed (中央 publish, 员工自己 pull)
   GET /advisory/feed.json
   含 skill / MCP / catfish 版本的安全建议 + 推荐配置
   员工 catfish 客户端定期 GET, 决定是否提示员工
   
✓ Pull-based skill / MCP marketplace (员工自己装)
   GET /skills, GET /skills/:ns/:name, GET /mcp_registry/registry
   POST /mcp_registry/subscribe (员工自己 subscribe)
   POST /skills/:namespace (员工自愿 publish 自己的 skill)
   
✓ 部门组织结构 / 用户列表 (admin 看 license seat 清单)
   GET /admin/users, GET /admin/departments
   含 email / 部门 / SSO 关联状态; 不含本机设备状态
```

### 禁止 (architectural powerlessness)

中央服务**不能做**这些事 (API 物理不实现):

```
✗ 远程 wipe 员工本机数据
✗ 远程禁用 / 卸载员工已装 skill
✗ 强制 push skill / MCP / 配置 / 策略到员工设备
✗ 强制升级 catfish 版本
✗ 远程查看员工本机设备状态 (在线 / 离线 / 装哪些 skill / 哪个版本)
✗ 远程查看员工对话内容 / 屏幕录制
✗ 远程查看员工本机 wiki / 笔记
✗ 远程触发员工本机 RPC (远程操作员工电脑)
```

**这些操作 catfish 中央服务的 API surface 永远不提供**. 不是"我们承诺不做", 是 "API 不存在".

### 灰色 (员工自愿 + 数据脱敏)

中央服务可以做这些, **但必须满足 (a) 员工 explicit consent + (b) 数据脱敏**:

```
⚠ 接收员工自愿提交的崩溃报告 (minidump)
   POST /telemetry/crash
   - 员工 catfish 崩了, 弹"是否上报?" 默认**关闭**
   - 上报时**自动 scrub PII** (Sentry/GlitchTip PII scrubber)
   - 不含对话内容, 不含本机文件路径
   
⚠ 接收员工自愿提交的诊断信息
   POST /telemetry/diagnostic
   - 类似 macOS "向 Apple 发送反馈" — 员工填表单 + 附 log, 自己决定附哪些
   
⚠ 接收员工自愿发布的 skill 到 marketplace
   POST /skills/:namespace
   - 员工触发 publish, 不是自动 sync
   - 含 skill 名 + body + 自动化脚本 (不含原始对话 / 录屏)
```

---

## 3. 5 个企业场景的 catfish 哲学 reframe

### 场景 1: 员工 A 离职, IT 想 wipe catfish 数据

**传统 fleet**: IT 在 admin 面板点 "remote wipe", 员工电脑上 catfish 数据立即消失.

**catfish 哲学**:
- catfish 中央服务**没有 remote wipe API** (API 不存在)
- catfish 在员工 UI 提供 **"重置我的所有 catfish 数据"** 自助按钮 — 员工自己点
- 公司想保证员工离职后 wipe? **这是劳动合同 / HR 问题**, 不是产品功能
  - 员工入职签 BYOD 合同: "我同意离职前一周内自助 wipe catfish 数据"
  - HR 离职流程加一项: "员工出示 catfish 已 wipe 截图"
  - 员工不配合? HR / 法务跟员工**人对人**处理, catfish 不介入
- 公司担心员工恶意带走对话内容? **跟担心员工带走 Word 文档 / Slack 截图同性质** — 这不是工具的责任, 是制度的责任

**白皮书写法**:
> catfish 中央服务**物理上**不能 wipe 员工本机数据. 数据归属员工, 离职数据处理通过劳动合同约束员工自助 wipe + HR 验证. 这是 catfish 的设计选择.

### 场景 2: 某 skill 发现漏洞, IT 想紧急禁用全员

**传统 fleet**: IT 点 "disable skill X", 全员 catfish 立即卸载.

**catfish 哲学**:
- catfish 中央服务 publish **Security Advisory** (类似 npm advisories, GitHub Security Advisory):
  ```json
  {
    "id": "CATFISH-ADV-2026-001",
    "severity": "high",
    "skill": "eis-login",
    "version": "0.1.0-frozen",
    "description": "skill 在特定输入下会泄漏 SSO token",
    "recommendation": "立即卸载并升级到 v0.2.0",
    "published": "2026-06-07T10:00:00Z"
  }
  ```
- catfish 客户端定期 pull `GET /advisory/feed.json`, 检测员工本机已装 skill 是否命中, 命中则**显著提示**:
  - dashboard 顶部红色 banner: "⚠ 你装的 skill eis-login v0.1.0 有高危漏洞, 建议立即卸载"
  - 默认显眼, 员工不能 dismiss (但**可以决定不操作**)
- 员工**主动**点 "卸载" — catfish 本机自己卸载
- 公司想强制? **跟 macOS "您的系统有安全更新" 一样** — 强烈提示但不强制. 真要全员强制? **HR/IT 制度**层面约束员工"必须在 7 天内处理 advisory", 违反者纪律处分. catfish 不强制.

**好处**:
- catfish 提供完整 advisory 数据, **IT 可以审计 "advisory 发出后多少员工未处理"** (聚合 metadata 自愿上报)
- 员工有选择 (e.g. 我下周要用这个 skill 做关键 demo, 我延后处理), 但有 visibility (我知道有风险)
- 中央**物理无能**, 员工最终决策权

### 场景 3: catfish 新版本发布, IT 想强制全员升级

**传统 fleet**: IT 点 "force upgrade", 全员 catfish 自动升级 + 重启.

**catfish 哲学**:
- catfish auto-updater **默认开**, 跟 Chrome / VSCode / macOS 一样, 后台静默升级
- 员工**可以关闭** auto-update (在设置里 toggle), 选 "手动检查"
- 中央 publish 新版本到 release feed, catfish 客户端定期 pull, 自动下载 + 安装
- IT 担心员工不升级? advisory 提示 + 聚合统计 (有 X 个员工还在用 old version, IT 可以**人对人**通知) — 但**不强制**

### 场景 4: 财务想控制 LLM 调用成本

**传统 fleet**: IT 设置 hard quota — 部门 X 月度 100K tokens, 超了 catfish 拒绝调用.

**catfish 哲学**:
- catfish quota 现状已经支持 (4 层 quota), 但 reframe 为 **"报销规则"** 而不是 **"硬性禁止"**:
  - 公司账户预算: 部门 X 月度 100K tokens (含报销)
  - 超额? catfish **不阻止员工调用**, 而是:
    - 弹通知 "你部门预算超了, 下面调用走 **员工个人账户** (你自己付钱)"
    - 员工可以**绑定自己的 OpenAI / Anthropic API key** 作 fallback
    - 或者员工选择"等下个月预算 refresh"
- 实质: catfish 把 quota 从"权限控制"变成"财务约束" — 公司钱报销有限, 员工自己钱无限. 跟"公司报销出差打车 200 元封顶, 超了你自付"同性质.
- 实现:
  - `GET /api/quota/me` 返回 `{used, limit, remaining, overage_policy: "byo_key_required"}`
  - catfish 客户端检测 overage → switch to BYO key (员工本机绑的 key)
  - **中央服务不限制员工调用**, 中央只负责"算账"

**好处**:
- 员工高需求 (e.g. 关键项目要密集用 LLM) 不被 hard cap 卡住
- 公司财务可控 (中央 metering 知道哪些是公司账户, 哪些是 BYO)
- 员工感觉被信任 (不是被监管)

### 场景 5: 等保审计 / 内审要查员工 X 上个月跑了哪些 dangerous_command

**传统 fleet**: IT 在 admin 面板 query 员工 X 全部 audit log, 含完整对话.

**catfish 哲学**:
- 中央 audit log 只有 **metering metadata** (`email, ts, model, tokens, cost, tool_name`)
- **对话内容 + tool args / result 全部留员工本机** (catfish SQLite)
- 等保 / 内审需要查具体调用细节? 走 **员工自助提供 + 法律程序**:
  - 中央 audit 显示 "员工 X 在 2026-05-15 调用了 dangerous_command (e.g. shell_exec)" — 是 tool_name metadata
  - 想看 args / result? **员工自己**在 catfish UI 点 "生成审计摘要 (脱敏)" 自愿提交
  - 员工拒绝提供? 走法律程序 (类似公司想拿员工手机数据, 必须法庭授权 + 物理拿到手机)
  - 但公司**通过 catfish 中央服务拿不到**

**白皮书 framing**:
> catfish 的设计哲学借鉴 BYOD (Bring Your Own Device) — 公司提供工具 + 报销, 员工保留数据主权. 内审 / 等保 / 法律调查需要员工内容时, 走员工 voluntary disclosure + 法律程序, 而不是 IT 远程取证. 这跟 macOS / iOS 的 enterprise management 同哲学 — Apple 也不允许公司管理员远程查看员工 iMessage 内容, 必须员工自己同意.

---

## 4. 员工自助工具集 (catfish 客户端 UI 内)

为了让上面 5 个场景 work, catfish 客户端必须提供完整的**员工自助工具**. 这是 "员工主权" 哲学的**产品落地**:

| 工具 | 触发场景 | 实现 |
|---|---|---|
| **一键 wipe 我的所有 catfish 数据** | 员工离职 / 换电脑 / 重置 | 删 SQLite + 录屏 + wiki + skill, 跳过中央 (中央 metering 老 record 保留, 但新数据全无). 弹"确认对话框 + 输入"我确认"防止误操作 |
| **一键导出我的所有数据 (备份)** | 员工换电脑 / 离职带走 / 定期备份 | 打包 SQLite + wiki + skill 成 .catfish-export.zip, 员工自己保管 |
| **一键导入 / 还原** | 员工从备份恢复 | 解压 .catfish-export.zip + 还原 |
| **一键诊断 / 报错** | catfish 崩了 / 出 bug | 生成 local minidump + 诊断报告 (PII scrubbed), 弹"是否上报?" 默认**关闭** |
| **一键升级 / 一键回滚** | 新版本不稳 | auto-update 失败时回滚到上一版本 |
| **一键合规审计摘要** | 等保 / 内审需要时 | 员工自愿生成"过去 N 天调用了哪些 dangerous tool, 不含 args" 摘要, 自愿提交 |
| **一键查看 advisory** | 员工想知道是否安全 | dashboard 顶部 advisory feed |
| **一键解绑 / 重新绑定 SSO** | 员工切换公司账户 / 换部门 | 不破坏本机数据, 只换中央 license seat |
| **一键 BYO LLM key** | 公司 quota 超 / 员工想私用 | 员工绑自己的 OpenAI / Anthropic / DeepSeek key, catfish 自动 fallback |
| **一键查看"我跟 catfish 中央服务的数据交换日志"** | 员工想验证 catfish 没偷传数据 | 显示所有发出去的 HTTP 请求 + payload (员工可以审计 catfish 是否符合数据零出端) |

**关键设计**: 这些工具都是**员工自己操作**, IT 无法远程触发. IT 想拿数据? 跟员工 "请你点一下这个按钮然后把文件给我".

---

## 5. 商业化 sell 角度 (跟传统 fleet 区分)

### 卖给谁?

**不卖给传统 IT 部门** — 他们的 KPI 是 "管控员工, 防泄密". catfish 哲学跟他们的 KPI 反向, 卖不动.

**卖给业务部门** — 他们的 KPI 是 "员工效率, 业务产出". catfish 给员工**真正的工具**, 不被 IT 卡住.

具体客户类型:
- **央企科技子公司 / 研究院** (中科院系 / 中国移动研究院 / 国家电网研究院) — 员工是博士 / 高端工程师, 反感被 IT 监控, 业务部门 buyer 强势
- **大型互联网 / 金融科技** (蚂蚁金服 / 招商银行金科 / 京东科技) — 高端技术团队信任员工是文化前置
- **国企招的高端人才团队** — 人才 retention 是 priority, 信任员工建立 employer brand
- **新能源 / 新材料 / 生物科技** — 相对市场化, 员工流动大

### sell 角度

把 IT 重新定位:

| 传统 fleet 的 IT 角色 | catfish 的 IT 角色 |
|---|---|
| **控制员** — 强制员工行为 | **审计员** — 看 metering, publish advisory |
| **保安** — 防泄密 | **基础设施** — 提供 SSO / quota / 报销 |
| **守门员** — 卡员工权限 | **服务员** — 帮员工解决问题 |

**白皮书 selling points**:

1. **"我们的中央服务在 API 层面就不能控制员工设备"** — 国资委 / 等保审计可直接查 API surface 验证. 比 "我们承诺" 更可信
2. **"员工数据主权让你避免 PIPL / GDPR / 等保 2.0 数据主体权利合规风险"** — 员工数据在员工自己手里, 公司不需要扛"数据处理者"全套责任
3. **"BYOD 哲学 + LLM 工具效率"** — 你们公司原本就接受 BYOD (员工带自己手机来上班), catfish 是 BYOD 在 LLM 时代的产品形态
4. **"高端人才招聘 / retention 卖点"** — "我们公司用 catfish, 你的 catfish 数据归你, 离职带走"

### 反 anti-pattern: 客户要求加 fleet 强制, 怎么办?

**绝对 hard line — 不加**.

可能的客户请求:
- "我们要远程 wipe 离职员工设备" → **NO**. catfish 提供员工自助 wipe, 走劳动合同约束.
- "我们要禁止员工用某个 model" → **NO**. catfish 走报销规则 (公司报销/不报销), 不走硬禁.
- "我们要查所有员工的对话内容" → **NO**. 走员工自愿提供 + 法律程序.
- "我们要强制全员升级版本" → **NO**. catfish 提供 advisory + auto-update, 员工选择.

提供替代方案:
- "你们想 wipe 离职员工 → 我们给你 BYOD 合同模板, HR 流程加 catfish 自助 wipe 验证"
- "你们想限制 model → catfish 报销规则可以做到 90% 效果, 员工超额用自己 key"
- "你们想查对话 → catfish metering audit log 给你 metadata, 具体内容走法律程序"
- "你们想强制升级 → catfish advisory + auto-update 默认开. 实际 95% 员工会更新, 没更新的走 HR 沟通"

**关键**: 客户跑了也不要妥协. catfish 走的是 "**结构性反 fleet**" 路线, 一旦加 soft 强制就**碎了整个 moat**.

---

## 6. 法律 / 合规配套

### BYOD 劳动合同模板 (推荐企业跟员工签)

```
catfish 数据归属与处理协议 (员工版)

1. 数据主权: 员工通过 catfish 产生的所有对话 / 录屏 / wiki / skill 等数据,
   归员工个人所有, 留员工本机. 公司不上传, 不远程访问.

2. 离职处理: 员工离职前 7 个工作日内, 应自助完成以下:
   (a) 在 catfish 客户端 "导出我的所有数据", 自行保管;
   (b) 在 catfish 客户端 "重置我的所有 catfish 数据";
   (c) 截图提供给 HR 作为离职流程一部分.
   
   未按时完成者按劳动合同违约处理.

3. 工作内容声明: 员工在 catfish 中处理工作相关任务时, 工作产出 (e.g.
   交付给公司的报告 / 代码) 归公司所有, 适用职务作品 / 职务发明条款.
   员工与 catfish 的对话过程 (e.g. 问 LLM 怎么写报告) 归员工所有.

4. 安全 advisory 响应: 员工应在收到 catfish 安全 advisory 后 7 个工作日
   内处理 (升级 / 卸载 / 配置调整). 未处理且导致安全事件者, 按公司安全
   制度处理.

5. 合规审计配合: 如遇等保审计 / 内审 / 法律调查, 员工应在公司法务正式
   请求下, 自助生成 catfish 审计摘要 (脱敏), 提交给法务 / 审计员.
   员工有权咨询律师后决定提供范围.
```

### 等保 / 密评合规 边界

等保 2.0 三级的某些条款要求 "管理员能远程禁用账户 / 强制策略下发" — catfish 跟这些条款**直接冲突**. 处理方法:

| 等保要求 | catfish 应对 |
|---|---|
| "管理员能远程禁用员工账户" | catfish 重定义"禁用"为"撤销中央 license seat", 满足 letter (员工不能用公司中央 LLM 配额), 不满足 spirit (员工本机仍能跑 BYO key). 跟评估机构沟通 reframe |
| "管理员能强制策略下发" | catfish 用 advisory + 报销规则替代, 跟评估机构沟通 "policy through HR contract, not through technical enforcement" |
| "审计能调取员工详细操作" | catfish metadata 全有, 详细内容走法律程序. 跟评估机构沟通 "audit through metadata + voluntary disclosure" |

**实际策略**: 找一家**支持员工主权理念的等保评估机构** (e.g. 偏向互联网 / 科技公司方向的评估所), 一起 frame "catfish 在等保 spirit 上合规但实现路径不同". 不行就**接受 catfish 拿不到传统等保认证**, 走"密评合规 + 员工主权白皮书 + 第三方法务背书" 替代认证路径.

这意味着 catfish **可能拿不下传统国企** (要传统等保), 但能拿下**新派国企 + 互联网科技公司**.

---

## 7. 跟 patent + moat + capability gap 的联动

### 跟 patent 1 (客户端零外泄 + 中央 metering 双盲)

manifesto 的公理 4 (API surface 物理无能) 是 patent 1 的**最佳具体实施例**:

> patent claim 1: "...服务器接收的 metering payload 经过设计使得即使获取全部服务器数据也无法重建 prompt / response 文本..."
>
> manifesto 公理 4: "...catfish 中央服务的全部 HTTP / RPC API surface, 没有任何 endpoint 能 (a) 强制员工本机执行操作, (b) 反向查询员工本机状态."

manifesto 把 patent 从"密码学不可重建对话"升级到"**整个中央服务架构都不可越界**" — 这让 patent 1 的 claim 更具体, 更难被绕过.

**建议**: file patent 1 时, 把 manifesto 公理 4 作为 dependent claim 加进去. 这样 claim 不只是"对话不上传", 而是"整个中央服务 API surface 不允许越界控制" — 这是 architectural claim, 比单一 feature claim 强很多.

### 跟 moat (counter-positioning)

之前 MOAT-ASSESSMENT 说 counter-positioning 是 "本地优先反 SaaS" — 太浅. manifesto 把它升级为:

> **"中央 API surface 物理无能" 是 catfish 跟 Anthropic / Microsoft / CrowdStrike 的结构性反向. 这些大厂的 business model 依赖 fleet 控制能力, 让他们做"物理无能"= 砍掉他们卖给 IT 的 value proposition.**

这才是真正的 counter-positioning moat. 写进 brand manifesto, 是 catfish 跟所有 LLM agent / RPA 工具的**根本区分**.

### 跟 capability gap

之前 CAPABILITY-GAPS.md 列了 "fleet management" 作为 P0 gap. manifesto 后**这条 reframe** 为:

| 旧 P0 gap | manifesto 后 reframe |
|---|---|
| Fleet management (远程 wipe / push / 禁用) | **员工自助工具集 + advisory feed + 报销规则 quota** (上述第 4 节) |
| Telemetry (Sentry) | **opt-in PII-scrubbed self-host GlitchTip + 员工自愿上报** (公理 4 灰色区) |

工程量类似 (3-4 月), 但**方向完全反向**:
- 不做 fleet API (省工程)
- 多做员工自助工具 (catfish UI 内 10 个新按钮)
- 多做 advisory feed (中央 publish, 客户端 pull)
- 多做 quota 重设计 (从 hard cap → 报销规则 + BYO key fallback)

---

## 8. 优先级 + 24 月 Roadmap (修正后)

### 立即做 (1 周内)

- [ ] 写 catfish 公开 manifesto (本份, 公开版精简 1000 字), 放 GitHub README + 官网首页
- [ ] **重命名** capability gaps 里的 "fleet management" → "员工自助工具集 + advisory feed"
- [ ] catfish-gateway 现有 ❌ 违反 endpoint reframe:
  - `POST /admin/users/:email/lock` → 加注释明确 "撤销中央 license seat, 不影响员工本机"
  - `POST /admin/users/:email/reset-password` → 改 "员工自助 + admin 协助"
  - `POST /tool_archive/*` → **废弃** (1 周工程量)

### 1-3 月

- [ ] file patent 1 (含 manifesto 公理 4 作 dependent claim)
- [ ] 客户端加 "一键 wipe / 一键导出 / 一键诊断" 3 个员工自助工具 (3 周)
- [ ] advisory feed 中央 API (`GET /advisory/feed.json`) + 客户端 polling + dashboard banner (2 周)

### 3-6 月

- [ ] BYO LLM key fallback (员工绑自己 key, catfish 自动 quota 超额 fallback) (3 周)
- [ ] 重设计 quota 为 "报销规则" (软限制 + BYO key fallback) (2 周)
- [ ] 完善员工自助工具集 (10 个 button 全做完) (4 周)

### 6-12 月

- [ ] BYOD 劳动合同模板 + 法律审核 (跟劳动法律师合作)
- [ ] 找 1-2 个新派国企 pilot 客户 (跟传统 IT 反向卖业务部门)
- [ ] 写 catfish 等保 reframe 白皮书 (员工主权下的合规路径)

### 12-24 月

- [ ] 拿下 3-5 国企客户 + 100 万 RMB ARR
- [ ] 跟 1-2 家评估机构合作 catfish 哲学下的合规认证
- [ ] PCT 国际申请 patent 1 (含 manifesto 公理)

---

## 9. 最后一句

catfish 的真正不同, **不在技术细节, 在哲学**.

**"中央 API surface 物理无能"** 是 catfish 跟所有 LLM agent / RPA / fleet 产品的**结构性分水岭**. 这一条做实了, 之前所有报告 (patent + moat + capability) 的逻辑才真正成立.

这份 manifesto 是 catfish 战略系列的**收口** — 把 4 条公理 formalize 到 API 边界, 把哲学落地到具体产品 architecture + 商业 model + 法律合规.

之前我用主流 fleet mental model 思考, 走偏了. 谢谢你 (鸿波) 把我拉回来.

**接下来 ship 这份 manifesto 公开版** (1000 字精简) + ship 客户端**员工自助工具集** — 这两步落地后, catfish 真正变成 **"产品 = 哲学的具体实现"** 不只是 demo.

— 报告完
