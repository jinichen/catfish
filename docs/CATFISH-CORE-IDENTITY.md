# catfish · 核心定位 + 商业模式 + 部署原则

> **写于 2026-06-02 凌晨, 鸿波在我反复盲猜后说"先记下来, 以后才不会乱"**.
>
> 这是 **catfish 的产品 / 商业 / 工程的 anchor**. 任何新 session / 新工程师 /
> 新销售开始 catfish 工作前, **先读这个文档**. 不要每次重新讨论这些已经定的事.
>
> 维护人: 鸿波
> 状态: 锚定 (要改请专门 review session)

---

## 一、catfish 不是 SaaS, 是**on-prem hybrid 企业 AI 助手**

类比对象:
- ✅ 类似: **商汤行业模型 / 科大讯飞行业 AI / 文心一言企业版**
- ❌ 不是: GitHub Copilot SaaS / ChatGPT Team / Notion AI / Cursor 个人版

**核心识别**:
- 数据**留在客户企业** (员工本机 + 客户机房中央服务), catfish 不持有客户数据
- 客户**自部署**, catfish 给安装包不直接 host 服务
- 私有 LLM 可在客户内网部署 (catfish-private-main / Qwen 122B 等)

---

## 二、商业模式: **一次性部署费 + 年度升级维护费**

经典企业软件 license 模式 (Oracle / VMware / 用友 / 金蝶 同款). 不是 SaaS.

| 项 | 估算价格 (中国市场) | 说明 |
|---|---|---|
| 一次性部署 | 80-300 万 | 装中央服务 + SSO 集成 + 培训 IT + 培训种子员工用 RecMode |
| 年度升级维护 | 15-60 万 (部署费 20%) | bug fix + 跟 hermes upstream 升级 + 季度小迭代 + 客户 IT 答疑 |
| 私有 LLM GPU 部署服务 | 30-80 万 一次性 (不含 GPU 硬件) | 装 qwen 122B / 配置 base_url / 验证 vision |
| 客户 PoC 大改 | 50-200 万 按工程量 | 接新 IM / 行业垂直微调 / 等架构性改造 |

**不收**:
- ❌ 按 seat 月费 (与企业采购流程冲突, 政企不接受)
- ❌ 按 token 用量分成 (客户自带 LLM key 或客户自部署 LLM, catfish 不承担 token 费)
- ❌ 客户特定 skill 服务费 — **员工自助 RecMode 录, catfish 团队 0 工作量**

**续约价**: 第 1-N 年都按部署费 15-25% (Oracle / VMware 标准).

---

## 三、5 个差异化卖点 (真技术支撑)

| 卖点 | 真技术实现 | SaaS 厂商有没有 |
|---|---|---|
| **数据零出端** | 中央 0 红线 — 中央 PG 永不存 chat content, 只存 audit metadata (邮箱/时间/模型/token) | ❌ 没有 |
| **隐私可验证** | PrivacyCard (员工自己看到本机存啥, 中央存啥) | ❌ 没有 |
| **员工自助学习** | RecMode 录屏 → 自动生成 skill / `catfish_propose_skill` LLM 主动 propose | ❌ Mem0/Zep 都没有 |
| **客户 IT 零接入** | Browser Agent (Playwright + CDP) 通用覆盖 90%+ 内部 web 系统 (OA/EIS/wiki), 走员工真 chrome SSO session | ❌ SaaS 给不了员工 chrome 访问 |
| **可全本地** | hermes 跑员工 mac + catfish-gateway 跑客户机房 + 私有 LLM (qwen 122B) 跑客户机房, 可全离线 | ❌ SaaS 必须联网 |

---

## 四、目标客群

✅ **核心**:
- 政府 (省市级 / 部委)
- 央企 / 国企子公司
- 金融 (银行 / 证券 / 保险)
- 军工 / 国防工业
- 大型制造业 / 央国资关联企业

❌ **不卖**:
- 长尾 SMB 自助 SaaS 用户 (那是 Notion AI / Cursor 个人版的活)
- 纯互联网公司 (他们用 OpenAI / Anthropic API 即可, 不需要数据合规)
- 创业公司 (买不起一次性部署费, 对合规需求弱)

**规模指标**: **客户企业数**, 不是单客户员工数. 1 个客户 1000 员工 vs 10 个客户每家 100 员工 — 后者 catfish 团队工作量 10 倍 (10 次部署 + 10 次客户 IT 培训).

---

## 五、部署模型

### 5.1 Companion (员工端)
- ✅ **员工自助安装**: catfish 团队 build .dmg (mac) / .msi (win), 上传客户内部 wiki / 邮件群发链接
- ✅ 员工像装 Chrome / VSCode 一样自己装
- ✅ 第一次启动 → Onboarding wizard 7 步 (welcome / 起名 / SSO / 模型 / Curator / 文书目录 / 试聊) 5 分钟体验完
- ❌ **不用 MDM (Jamf / Intune)** — 员工反感, 跟 catfish "员工主权" 哲学冲突
- ❌ 不强推升级 — auto-update 自检 + 提示, 员工自己点"现在升级"

### 5.2 中央服务 (客户 IT 端)
- ✅ docker-compose 一份, 客户 IT 一次性 `docker-compose up -d`
- ✅ 6 个中央服务: `catfish-gateway` (LLM 路由) / `identity-server` (SSO) / `mcp-registry` / `skills-hub` / `secret-broker` / `web` (中央门户 UI)
- ✅ 配置: SSO (OIDC) 跟客户 IdP 对接 + DB connection + 私有 LLM endpoint
- 一次性装好后, catfish 团队不再上门, 只远程支持

### 5.3 私有 LLM (客户 IT 端, 可选)
- 客户 IT 自己买 GPU 服务器 (e.g. 4xH100 跑 qwen 122B)
- catfish 给配置文档 + base_url 写法
- catfish 不维护 GPU, 不监控 LLM 服务状态 (那是客户硬件)

---

## 六、客户特定接入 — **员工自己做, 不是 catfish 服务费**

| 客户特定场景 | 谁做 | 用 catfish 哪个功能 |
|---|---|---|
| 客户 OA 登录 + 操作 | **员工自己录 RecMode** | RecMode → 自动生成 `<客户>-oa-login` skill |
| 客户 EIS / 内部 wiki 接入 | **员工自己录** | 同 RecMode (5/14 demo `eis-login` 是范本) |
| 客户邮件接入 | **员工本机已登录的 Outlook / Foxmail / Apple Mail** | Email Agent 桌面客户端集成 |
| 客户 IM 接入 (微信/飞书/钉钉/企微) | **员工自己扫码绑** | WeChatBindingCard (5/26 ship) 扫码 → ClawBot |
| 跨 OA / EIS 复合任务 | **员工跟 LLM 聊** | LLM 主动调 `catfish_propose_skill` (BL-MM9) 提议固化 |

**关键认知**: catfish **不预先接死任何客户系统**. 真生产 catfish 团队**不写客户特定 skill**, 不收"每 skill 5-20 万" 服务费. 员工自助 RecMode 录一次, catfish 自动学.

---

## 七、catfish 真扩展瓶颈

❌ **不是**:
- 单客户员工数 (天然分布式, 员工自部署, 0 catfish 团队压力)
- LLM 调用量 (客户自带 key 或自部署, catfish 不付 token 费)
- 中央服务 scale (标准 web service 水平扩, PG 分库, 跟 SaaS 一样)

✅ **真是**:
- **客户企业数** × 每客户接入成本 (一次性部署 + 持续 SLA)
- 跟 hermes upstream 升级跟进速度 (每次 hermes 升级要测全链路, 6/1 plugin 适配 0.15.1 就是案例)
- 销售周期长 (政企采购 6-18 个月)

---

## 八、销售物料口径 (按这个写)

**对**:
- "数据不出企业的私有 AI 助手"
- "员工自助安装, IT 无需推送"
- "员工自己录一次, AI 自动学技能"
- "私有 LLM 内网部署, 数据 100% 本地"
- "一次性买断 + 年度维护, 跟 Oracle 同模式"

**错**:
- ❌ "登录即用" (不是 SaaS, 客户 IT 要装中央端)
- ❌ "MDM 批量推装"
- ❌ "按 seat 月费"
- ❌ "按 token 用量分成"
- ❌ "厂商写客户特定 skill"
- ❌ "无限智能" (catfish 不做 SaaS scale 飞轮, 不卖 mem0/letta/zep 那套)

---

## 九、catfish 当前阶段不该做的事

(基于"员工 / 客户 / 商业模式" 三者一致性的判断)

❌ **不上 MDM** — 跟员工主权冲突
❌ **不催员工升级** — 提示但不强推
❌ **不写客户特定 skill 收费** — RecMode 让员工自己做
❌ **不上 SaaS scale feature** (smart router / mem0 / 全 fan-out 等) 在 50-500 员工阶段 — YAGNI
❌ **不按 seat / token 量计费** — 跟一次性 license 模式冲突
❌ **不接长尾 SMB SaaS 客户** — 我们卖政企 license

---

## 十、catfish 当前阶段该做的事 (周一 review 优先)

1. **部署运维自动化** — 中央端 docker-compose 真生产可用 + Companion auto-update 自检完善
2. **跟 hermes upstream 升级跟进自动化** — 6/1 plugin 适配 0.15.1 是 1 次性事件, 但每次 hermes 升级都要做 — 写成 CI / 监控 + 升级 runbook
3. **销售物料按上面"销售口径"重写** — one-pager / 演讲稿 / 演示视频 / 安全说明 PDF
4. **客户 IT 培训文档** — 装中央 docker-compose 一步步 + 集成 SSO + 配置 LLM endpoint
5. **种子员工培训文档** — Companion 自助安装 + RecMode 录技能 + propose_skill 接受
6. **真第一个客户 demo PoC** — 找 1 个高合规客户 (政府 / 国企) 试点, 验证整套流程

---

## 十一、反盲猜清单 (我 6/1 整天 35 次反思教训提炼)

**任何 catfish 工程 / 商业 / 销售判断前必做**:

| 决策类型 | 必做检查 |
|---|---|
| 工程量估算 | `grep` 现有功能, 看是否已 ship (避免重复造轮子) |
| 代码路径判断 | `Read` 真函数, 不用 mental model |
| git / 系统状态 | `git log` / `git status` / `ps` / 日志真证据, 不推断 |
| 商业判断 | 看 catfish 已实现的护城河 (RecMode / Browser Agent / PrivacyCard) |
| 销售话术 | 按本文 §三 + §八 写, 不按 SaaS 模板 |
| 部署模型 | 按本文 §五, 不重新讨论 |

---

## 十二、本文档维护规则

- **不轻改**: 任何修改需要专门 review session, 不在日常工程对话里悄悄改
- **改了要明确**: 改文档前先在 git commit 里说为啥改 / 跟谁讨论过
- **新 session 第一步**: 任何接 catfish 的新人 (Claude / 工程师 / 销售) 第一件事**读完本文档**, 不重复讨论这些已定的事

---

*创建: 2026-06-02 凌晨, 鸿波拍板*
*关联: docs/FEATURE-TRACKS.md (工程进度) · docs/BACKLOG.md (战略 backlog) · docs/SECURITY-REVIEW.md (合规细节)*
