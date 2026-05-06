# 鲶鱼 Catfish · 给客户决策人 / IT 看的

> 央企 / 大型组织的 **AI 员工副手** — 100% 私有部署, 数据不出客户内网.
> 1 页快速了解, 5 分钟知道是不是适合你们.

## 一句话

每个员工自己专属的 AI 副手, 跑在客户内网, 数据零出境, 全开源可审计, 接客户已有的 LLM 不绑死。

## 鲶鱼定位: 员工的"职业资产", 公司给员工配

> 鲶鱼是**员工拥有的**私人 AI, 公司给员工配 (类比给员工配 ThinkPad). 数据 / 工作画像 / 技能库**完全属于员工本人**, 跳槽带走, 跨雇主可携带.

### 跟人类同事 / 工具的对比

| | ChatGPT 企业版 | 鲶鱼 |
|---|---|---|
| 谁拥有员工的 chat 历史 | OpenAI | **员工本人** (在员工 mac, 跳槽带走) |
| 员工的工作画像 | OpenAI 训练数据汪洋 | **员工本人** 持有 (`~/.catfish/`) |
| 员工跳槽后 | 失去访问权 | **直接 cp 到新 mac, 鲶鱼继续懂他** |
| 公司监控员工 | OpenAI 后台 | 客户 IT 部署架构选 (默认不监控) |
| 谁是产品的客户 | 公司 IT | **员工** (B2C2B: 公司报销, 员工拥有) |

### 为什么这定位对所有人都好

- **员工爱用**: 鲶鱼真懂他, 而且这懂他不会被公司绑架. 跳槽不丢失 6 个月积累
- **公司愿意配**: 员工产能 ↑ 30% 是公司收益. 员工带走的是**自己的画像**, 不是公司核心数据 (公司机密在公司服务器)
- **HR / 法务合规**: 尊重员工对自己工作产出的所有权 (跟劳动法一致)
- **信安放心**: 鲶鱼平台公司 / OpenAI 都见不到. 公司核心数据 (财务 / 合同 / 客户库) 走另外的系统, 不在员工鲶鱼里

### 跟"工作同事"定位区别

之前叫"工作同事", 听着是公司装的. **真定位**: 员工自己拥有的"数字大脑延伸". 鲶鱼帮员工更厉害, 而**员工的厉害属于员工**, 不属于公司.

## 数据隐私 3 道边界 (精确版)

> 客户最常问 "数据安不安全". 央企信安会问深问细, 精确 3 层边界:

### 边界 1: 员工敏感数据 0 出员工 mac ✅
- **文件原件** (`~/.catfish/uploads/`) / **journal** (`~/.catfish/employee_journal.md`) / **memory** (catfish_remember / user_profile / style_fingerprint) / **chat 历史 SQLite** (`~/.catfish/sessions/`) / **USER.md** / **ALLOW.md** / 私钥 / Keychain 引用值
- 这些**默认 0 离开员工 mac**, 公司 IT 想看也得通过员工 mac 的 MDM (跟鲶鱼无关, 是公司 mac 政策)

### 边界 2: chat prompt 流过客户内网 ⚠️ 部署设计
- chat → prompt → gateway (架构 B/C 下到部门 / 公司内网服务器) → LLM 推理 (客户内网 GPU)
- gateway / LLM 都在**客户内网**, 看得到 prompt 文本 (推理本质)
- gateway 默认不持久化 prompt; 客户 IT 想留全文改 verbose log 即可
- "公司能审员工 chat 内容到什么粒度" 看部署架构: A 员工本机 (减小集中信任面) / B 部门 / C 公司
- **chat 是工作产出, 公司有审计权天然存在**, 鲶鱼不挡也不该挡

### 边界 3: 客户公司大门 0 出境 ✅
- 默认 prod 配置 0 字节出客户公司网络
- **鲶鱼平台公司从不见任何客户数据** — 软件供应商, 部署完撤场
- 跟 ChatGPT 企业版本质区别: 那个 OpenAI 永远在数据链上

## 跟 ChatGPT 企业版区别

| | 鲶鱼 | ChatGPT 企业版 |
|---|---|---|
| 部署 | 客户内网 100% 私有 | SaaS, 美国服务器 |
| 数据出客户内网 | **0** | 全部走 OpenAI |
| 鲶鱼/OpenAI 公司能看? | ❌ 永远不能 | ✅ OpenAI 看 |
| 客户 IT 能否完整 audit | ✅ jsonl 接 SIEM | ⚠️ OpenAI 后台权限申请 |
| 长期记忆 | 跨 session 跨月级 (catfish_remember) | session 级 |
| 主动性 | 主动找员工 (桌宠 / 9:30/14:00/17:30 自动开口) | 被动等问 |
| 个性化 | USER.md / SOUL.md 员工自配 | 通用 |
| 开源 | 全开源, 客户可独立审计 | 闭源 |
| 模型 | 客户自己的 LLM (Hermes / qwen / 任意) | 绑死 OpenAI |
| 工具 | 工具调用本地驱动 (skills + tool-bridge) | 平台限制 |

## 给员工的 4 件事

1. **chat** — 跟 ChatGPT 一样, 但带员工身份 (USER.md) 和历史 (引用上周的对话, 引用 journal)
2. **文件** — 拖入 PDF/Excel/Word, 鲶鱼调本地 skill 出报告, 文件原件不出员工电脑
3. **桌宠** — 屏幕角落驻留, 鲶鱼有事主动找员工 (主动闲聊 / 工具完成 / 任务提醒)
4. **跨 session 记忆** — 新对话能引用 30 天前的事, 不只是 32K context window

详细见 [demo 5 场景脚本](./MAY-DEMO-SCRIPT-2026-05-14.md)

## 给客户 IT 的 4 件事

1. **完全在客户内网** — gateway / tool-bridge / hub / chat 历史全在客户机器
2. **接客户已有 LLM** — Hermes 自建 / qwen 私有部署 / 任意 OpenAI-compatible API
3. **SSO 直接对接** — 客户 IdP RS256 JWT 验签, 现成对接, 不用换登录
4. **审计完整** — gateway/tool-bridge 双层 jsonl, 标准 SIEM 能接

详细见 [部署 runbook](./DEPLOYMENT-RUNBOOK.md)

## 给信安部门的 6 件事

1. **依赖 CVE 全 0** — Python 4 组件 / npm prod / Rust 1067 advisory 全过, [扫描报告](./.security/2026-05-06/audit-summary.md)
2. **CI 自动安全门** — 每次 PR 跑 cargo audit + pip-audit + npm audit + gitleaks, 任何 commit 带漏洞或 secret 自动 fail
3. **execute_code 双层沙箱** ★ 5/7 ship — L1 字符串规则 25 类 + L2 macOS sandbox-exec OS 级隔离 (默认 allow + 5 类关键 deny). **34 测试全绿** (13 shell + 15 unit + 6 e2e). 5/14 demo 现场演 LLM 偷 /etc/passwd / curl 外联被拦 + audit 留痕. 5/19 加 Linux nsjail + Docker fallback 三层
4. **prompt 凭证检测** — 员工不小心粘密码进 chat, 自动检测 + 不入 audit log
5. **数据流向图清晰** — [DATA-FLOW-DIAGRAM.md](./DATA-FLOW-DIAGRAM.md) 一页讲清出境 4 路径 + 不出境 7 类数据
6. **沙箱 audit 透明** — 每次 execute_code 写 `~/.hermes/.catfish_audit.jsonl` 含 `sandbox_used` / `sandbox_kind` 字段, 客户 SIEM 一行命令 `jq 'select(.sandbox_used)'` 全捞

详细见 [安全审查](./SECURITY-REVIEW-2026-05-06.md)

## 客户预期问答 30 问

[DEMO-CUSTOMER-QA-2026-05-14.md](./DEMO-CUSTOMER-QA-2026-05-14.md) — A 安全 8 / B 数据出境 5 / C 部署 5 / D 性能 4 / E 商业模式 4 / F 技术 4

## 价格 / 商务

私有化部署: 一次性 license + 年维护, 详细联系鲶鱼平台团队 (鸿波).
中央企业 100% 私有化, 鲶鱼公司端不见任何客户数据.

## 5/14 后

- **5/14 demo 当天**: macOS 真技术沙箱 (sandbox-exec) 已 ship 在演 (★ BL-S29 5/7 起 12 天 sprint, 鸿波"一次性做完"拍板)
- **5/19**: BL-S29 全部收口 (macOS sandbox-exec + Linux nsjail + Docker fallback + 50 case 测试矩阵)
- 5/22 后: 桌宠拖拽 / Hermes 升级 Phase C
- 6 月: macOS Notarization + 公证 / 第三方渗透测试 / SBOM 自动化
- Phase 3: 跨员工 federation (员工自愿互助 + 跨雇主可携带, BL-FED2 5/15 起 6 周)

## 联系

- 演示 / 试用: 鸿波 (鲶鱼平台团队)
- 安全报告: 见 SECURITY-REVIEW.md 末尾
- 漏洞报告: 走 GitHub Security Advisory, SLA 24h triage / 7d fix
