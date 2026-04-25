# 鲶鱼 · 开源 + 商业化战略

> **版本**：v1，2026-04-25
> **拍板人**：陈鸿波（@jinichen）
> **状态**：拍板生效，3 个月内不动；Phase 1 启动前回看一次

---

## 一句话定调

**Open Core 模式** —— 基础设施层开源（Apache 2.0）建立行业事实标准 + 招人 + 信任；商业差异化层闭源支撑 SaaS / 私有部署收入。

---

## 为什么不是别的路

| 候选路线 | 为什么放弃 |
|---|---|
| 全闭源 | B2B 客户要源码可见 / 招人难 / 没社区 / 跟开源竞品打不过 |
| 全开源（含 cloud 多租户） | 客户全自托管 = 你没什么可卖 / race-to-bottom |
| 仅开源 SDK / 客户端 | catfish 真技术深度在 gateway / policy / tool-bridge，全藏起来反而看不出技术力 |
| **Open Core ✓** | GitLab / Sentry / Posthog / Supabase / HashiCorp / Mattermost 验证过的最优解 |

---

## 开闭分界（这是核心决定，改这条之前先回来读一遍）

### 永远开源（Apache 2.0）

每个组件**写得像独立项目**：自带 README / CHANGELOG / 测试，不依赖 monorepo 上下文。

| 组件 | 路径 | 开源理由 |
|---|---|---|
| LLM Gateway | `central/llm-gateway` | 越早成为事实标准，"中央管理"层的客户越多 |
| Tool Bridge | `edge/tool-bridge` | 通用 IPC 桥接，任何 agent 都能用 |
| Local Search | `edge/local-search` | 通用本地 FTS，单独拿出来都有价值 |
| Identity (SOUL 模板) | `edge/identity` | 人格定义模板，品牌不在这层 |
| catfish 命令包装 | `edge/branding` | 员工工作流工具 |
| Hermes-fork patches | `edge/hermes-fork` | 上游 hermes 的品牌补丁，开源是义务回馈 |
| catfish-policy | `plugins/catfish-policy` | 安全规则越透明越建立信任 |
| 公开 connectors | `connectors/*` | 各种 SaaS 集成 |

### 永远闭源（公司核心 IP）

这些是你卖钱的差异化点。**绝不开源，永远 private 仓库**。

| 组件 | 路径 | 闭源理由 |
|---|---|---|
| 中央分发管理 | `central/distribution` | SaaS 多租户的命脉 |
| MCP 注册中心 | `central/mcp-registry` | 平台核心，竞品壁垒 |
| Secret Broker | `central/secret-broker` | 安全关键，企业卖点 |
| Skills 市场 | `central/skills-hub` | 商业模式核心，类比 GitHub Marketplace |
| 中央 Telemetry | `central/telemetry` | 数据分析，企业付费功能 |
| Companion App | `edge/companion-app` | UX 护城河，"鲶鱼是工具还是产品"的关键 |
| 飞书集成 | `edge/feishu-monitor` | 中国 to-B 刚需，差异化 |
| **Future**: catfish-cloud / catfish-admin / catfish-billing / catfish-sso / catfish-audit | (尚未实现) | SaaS 企业版功能集 |

### 灰区（Phase 1 拍板）

| 组件 | 当前状态 | 候选方案 |
|---|---|---|
| catfish-gateway-auth (plugin) | private | 开源（auth 协议是基础设施） |
| catfish-telemetry (plugin) | private | 闭源（业务数据） |
| browser-agent | private | 开源 SDK，闭源 enterprise SDK |

---

## License 决策

**Apache 2.0**（catfish 根 LICENSE 已加）。

**为什么 Apache 2.0 而不是 MIT**：
- 多了 **patent grant**：贡献者不能事后用专利反咬商业用户
- 适合 B2B：企业法务团队对 Apache 2.0 接受度高于 GPL，等同 MIT
- 兼容上游：hermes-agent 是 MIT，Apache 2.0 与 MIT 兼容（单向）

**为什么不是 AGPLv3**：
- 强 copyleft 适合"防止云巨头白嫖"（MongoDB/Elastic 当年走的路），但对中小企业 SaaS 不友好
- 我们的护城河应该是产品 + 服务 + 速度，不是法律枷锁

**为什么不是 GPL**：
- 企业法务普遍恐惧 GPL"传染性"
- B2B 销售卡审查

**第三方依赖 attribution**（开源时必须做）：
- hermes-agent: Nous Research, MIT
- Tauri / FastAPI / litellm / React / Vite 等: 各自 license（多数 MIT/Apache）
- 在每个开源 repo 的 README 加 "Built on:" 段

---

## 三阶段时间线

### Phase 0 · 现在 → 1 个月（持续 private，但写组件化代码）

**目标**：代码继续滚出来，但写得**未来能切独立 repo**。

具体动作:
- [x] 加 Apache 2.0 LICENSE（已完成 2026-04-25）
- [ ] 每个**计划开源**的组件下加自己的 README、CHANGELOG（不依赖 monorepo）
- [ ] 关键 hard-code 全部环境变量化（特别是内网 IP `10.10.40.102` / 飞书 endpoint / 公司 SSO url）
- [ ] `central/llm-gateway` 的 `.env.example` 文档化每个 env var
- [ ] 写 `docs/contributors/CODE_OF_CONDUCT.md` 草案
- [ ] 写 `docs/SECURITY.md` 漏洞披露流程

**不做**：拆 repo、open source 公告、社区建设。

### Phase 1 · 1-2 月（拆 repo 准备）

**目标**：把要开源的组件拆成独立 repo，但仍 private 直到 Phase 2。

具体动作:
- 用 `git subtree split` 抽取 `central/llm-gateway` 等到独立 repo（**保留 commit history**）
- 主 `catfish` repo 变成 monorepo 索引：private 部分 + 通过 git submodule 引用 open 部分
- 给每个开源 repo 加 CONTRIBUTING / CODE_OF_CONDUCT / SECURITY / .github/ISSUE_TEMPLATE
- 测试覆盖 70%+
- 设 docs.catfish.ai 域名（或 catfish 子页面）做文档站
- 准备 Phase 2 公告文案：HackerNews 帖 + 知乎 + X 一篇

### Phase 2 · 2-3 月（开源公告 + 商业化启动）

**目标**：开源公布 + 拿到第一个付费客户。

具体动作:
- 把 `catfish-gateway` / `catfish-policy` / `catfish-tool-bridge` / `catfish-local-search` / `catfish-identity` 改 visibility public
- 公告："Why we're open-sourcing catfish" 博客 + HN 提交 + 转发到知乎/X
- 同时启动商业化:
  - **catfish-cloud beta 内测**（免费 trial，30 天 → 转付费）
  - **私有部署报价单**：50 人以下企业 / 50-500 / 500+ 三档
  - 至少 3 个客户 demo

**KPI**:
- GitHub stars: 500+
- Hacker News 上首页（任意排名）
- 第一个付费客户合同

### Phase 3 · 3-12 月（双轮驱动）

**目标**：商业化 + 社区并行扩张。

具体动作:
- catfish-cloud 正式上线（catfish-distribution 上 Phase 2 私有 dogfood，Phase 3 SaaS）
- 企业销售（中国大陆 to-B 销售有特殊路径，需要拍板：自销 vs 渠道）
- 社区:
  - GitHub stars: 5000+
  - 50+ contributors
  - 20+ third-party catfish-skills 在 Skills Hub
- 团队:
  - 1-2 位全职工程师
  - 1 位社区 / DevRel

**KPI**:
- ARR：100 万人民币（保守）/ 500 万（aspirational）
- catfish-cloud 月活租户：50+
- 私有部署客户：10+

---

## 决策原则（写代码时遵守）

1. **写每一行代码时问自己：这是开源层还是闭源层？**
2. **闭源层不依赖开源层的内部 API**（防 Phase 1 拆 repo 时反向依赖打结）
3. **开源层不写"鲶鱼公司专属"逻辑**（pure infrastructure，company-agnostic）
4. **新组件默认开源**，除非 review 后认定有商业差异化价值
5. **永远不在公开仓库提交**：内网 IP / 公司域名 / 客户名 / 真实 token
6. **License 头**：每个 .py / .rs / .ts 文件未来开源前要加 SPDX header `// SPDX-License-Identifier: Apache-2.0`

---

## 反向触发器（什么时候回头看这个决定）

回到这份文档重新评估的触发条件：

- **3 个月**：定期 review（Phase 1 启动前）
- **拿到第一个企业客户合同**：客户的具体诉求可能改变开闭分界
- **拿到融资**：投资人会有意见（特别是要不要更激进开源 / 走 cloud-only）
- **大型竞品出现**：catfish-cloud 类对标产品（如 Anthropic 自己出 catfish-like 桌面客户端）
- **法规变化**：中国监管对 LLM agent 类项目变化时

---

## FAQ

**Q: 为啥不直接全开源？社区力量不是无敌吗？**
A: 社区是杠杆不是商业模式。Linux 这种基础设施可以纯开源因为有 RedHat 这种生态商。我们没法等十年才商业化，需要立即收入。Open Core 是"现在能挣钱 + 未来能挣更多钱"的现实选择。

**Q: 闭源部分会不会被人 reverse engineer？**
A: 会。但 catfish 的真护城河不在代码，在 (a) 客户数据 (b) SaaS 多租户运营 know-how (c) 服务响应速度。代码反推得出，运营反推不了。

**Q: hermes-fork 的 license 状态？**
A: hermes 上游是 MIT，我们改了它（通过 apply_brand_patch.py）。catfish-fork 的 patches 是我们写的、不是 hermes 源码，所以可以 Apache 2.0 重新许可。但 README 必须 attribution Nous Research。开源前需要法务 review。

**Q: 中国国内这种 LLM agent 项目开源会不会有合规风险？**
A: 当前监管对 to-B 工具类比较友好（不像 to-C 内容生成）。开源代码本身不需备案；如果 catfish-cloud 在国内运营，需要算法备案 + ICP/EDI。Phase 2 公告前法务咨询一次。

**Q: Companion App 真不开源？**
A: Phase 0-2 闭源（保 UX 护城河 + 建立用户群）。Phase 3 后看竞争格局：如果有人复刻就**开 Community Edition**（基本功能开源，cloud sync / team / 企业 SSO 闭源 = "Companion Pro"），保住差异化。

**Q: 现在公开 GitHub repo `jinichen/catfish` 算 Phase 0 还是 Phase 1？**
A: Phase 0。它是 private 仓库，不算 public 公告。等 Phase 2 才把"要开源"的组件 visibility 改 public。

---

## 决策签名

> 此文档代表 2026-04-25 的战略拍板。
>
> 修改这份文档需要主理人（@jinichen）显式同意 + 在 git log 留下变更原因。
>
> 不要在写代码的过程中无意识违反此处的开闭分界。
