# Catfish 商业化必备能力 Gap 分析

> 跟 `MOAT-ASSESSMENT.md` 配对: moat assessment 是**战略** (要打什么仗), capability gap 是**战术** (具体要 build 什么). 不补这些 gap, 拿不到一个国企客户.
>
> 本报告基于 agent 实际 grep + 读代码 audit, 不靠猜. 用 `~/person_task/catfish/` 全仓审计.
>
> 审计日期: 2026-06-07

---

## TL;DR (一页摘要)

audit 11 个商业化必备能力点, 结论:

| Tier | 数量 | 现状 | 修复时间 / 成本 |
|---|---|---|---|
| **P0 阻塞器** (没这个一个客户都签不下来) | **5** | 几乎完全空白 | **6-12 月 + 30-200 万 RMB** |
| **P1 关键** (50+ 人规模必备) | **3** | 部分有但不全 | 2-4 月 + 5-20 万 RMB |
| **P2 锦上添花** | **3** | 部分缺失 | 1-2 月 + 1-5 万 RMB |
| **意外的好消息** (已 ready) | **2** | 出乎意料完整 | — |

**5 大 P0 阻塞器**:

1. **数字签名 + auto-updater 完全 0** — macOS Gatekeeper / Windows SmartScreen 直接拦, **POC 都进不去**
2. **国密 SM2/SM3/SM4 + 等保 2.0 三级 0%** — 国企/金融招标过不了**采购围栏**, 没法投标
3. **License key 系统完全 0** — dual licensing 没法收钱, 字面意义上的"商业化不存在"
4. **OIDC 是 demo 级 + SAML/LDAP/钉钉/飞书 0** — 99% 中国国企 SSO 接不上, 客户接入卡在第一步
5. **Telemetry + Fleet 配置 0** — 员工电脑崩了 IT 不知道, 50 人以上**运维不下去**

**意外的好消息**:
- 离线 LLM 集成已**完整** (catfish-private-main 内网 Qwen 122B + Ollama/llama.cpp stub)
- Quota / Budget 4 层 (默认/per-user/per-model/per-department) 已 ready, AdminPage 已建

**修正后的优先级 (跟 MOAT-ASSESSMENT 联动)**:

| Phase | 任务 | 跟哪个 moat 配对 | 时间 | 投入 |
|---|---|---|---|---|
| Phase 0 (现在) | de-risk 职务发明 + 法律先行 | (前置条件) | 1 月 | < 1 万 |
| Phase 1 (1-6 月) | P0 阻塞器 #1, #3 (签名 + license) | brand / counter-positioning | 6 月 | 5-10 万 |
| Phase 2 (6-12 月) | P0 #2, #4 (国密 + SAML/钉钉) | brand (等保合规) | 6 月 | 100-200 万 |
| Phase 3 (12-18 月) | P0 #5 + P1 全套 (fleet + telemetry) | switching cost (运维 lock-in) | 6 月 | 20-50 万 |
| Phase 4 (18-24 月) | P2 (i18n + 文档 + SQLCipher) | brand 国际化 + 合规审计 | 6 月 | 5-15 万 |

**资金总需求 24 月**: 130-275 万 RMB. **个人扛不起**, 必须 **找联合创始人 / 找投资 / 找国企 sponsor**.

---

## 现状审计表 (来自实际 grep)

| # | 能力 | 现状 | 关键证据 | 缺什么 |
|---|---|---|---|---|
| 1 | **数字签名 + auto-updater** | **没有** | `tauri.conf.json` 无 signing/updater 段; Cargo.toml 无 `tauri-plugin-updater`; `.github/workflows/` 无 release.yml | macOS Apple notarization, Windows authenticode, tauri-plugin-updater + pubkey 签名分发 |
| 2 | **License key 系统** | **没有** | grep `license_key/enterprise_license/subscription_tier/paid_tier` 全仓 0 命中 | 整个 license server + 客户端校验 + 试用/正式区分 |
| 3 | **SSO** | **部分 (只 OIDC, demo 级)** | identity-server 自标 "dev / demo server"; 缺 PKCE/refresh rotation/CSRF/redirect 白名单/rate limit; docs/SSO-CUSTOMER-INTEGRATION.md 明说"钉钉/企微 → Phase 2 (Q3 2026)" | SAML 2.0, LDAP/AD, 钉钉/飞书/企微 OAuth, OIDC 加固 |
| 4 | **Admin / Fleet 配置** | **部分 (admin 有, fleet 没)** | AdminPage 有 user CRUD + lock + reset-password; **但** grep `fleet/central_config/push_config/policy_distribute` 全 0 命中 | Fleet management: 边缘机器盘点、远程 skill / MCP / upgrade 推送、设备策略禁用 |
| 5 | **国密 SM2/SM3/SM4** | **没有** | grep `国密/gmssl/sm2/sm3/sm4` 业务代码 0 命中; JWT 走 RS256; bcrypt 存密码 | SM2 替代 RS256, SM3 替代 SHA-256, SM4 替代 AES, GM/T 等保 2.0 材料 |
| 6 | **数据加密 (SQLite/传输)** | **几乎没有** | `rusqlite = { features = ["bundled"] }` **未启用 `bundled-sqlcipher`**, 即本地 SQLite 明文; grep `sqlcipher/PRAGMA key` 业务 0 命中 | SQLCipher 启用, 本地备份加密, KMS / 客户 HSM 集成 |
| 7 | **离线 LLM** | **完整** ⭐ | `models.yaml` 有 `catfish-private-main` (内网 Qwen 122B); chain fallback "内网优先 → 公网兜底"; app.py 有 ollama_stub / llamacpp_stub | 已 ready |
| 8 | **Quota / Budget** | **部分 (完整度高)** ⭐ | `quotas.yaml` 4 层 (defaults/per_user/per_model/per_department); AdminPage 有 /admin/quota + /admin/quota/events | 缺金额/账单/发票/月度成本中心结算 |
| 9 | **Telemetry / 崩溃** | **几乎没有** | grep `sentry/opentelemetry` 业务 0 命中; `central/telemetry/README.md` 是 P1 占位稿, 无实现 | Sentry/GlitchTip self-host, minidump 上传, front-end error boundary |
| 10 | **i18n** | **没有** | grep `i18next/useTranslation` 0 命中; `zh-CN` 多处硬编码 | i18n 框架, en/中英资源文件, locale 动态 |
| 11 | **文档完整性** | **部分 (开发文档多, 客户文档薄)** | docs/ 90+ 篇, 绝大多数内部 BL/RCA; 客户面向只 SSO/DEPLOYMENT/QUICKSTART 5 篇; **无独立用户手册 / 管理员手册 / OpenAPI / 灾备 SOP** | 用户手册, 管理员手册, OpenAPI, 灾备 SOP, SLA 模板, 合规白皮书 |

---

## 5 大 P0 阻塞器 (详细)

### P0 #1: 数字签名 + auto-updater 完全 0

#### 后果

- **macOS**: Gatekeeper 拦 "无法验证开发者", 员工双击 .app 就 fail. 即使绕过去 (右键打开), 大部分国企 IT 不允许员工绕 Gatekeeper.
- **Windows**: SmartScreen 标 "未知发布者" + Microsoft Defender / 360 / 火绒 杀毒软件直接隔离 / 删除 .exe.
- **没 auto-updater**: 升级 = IT 重新分发安装包 + 员工手动卸载重装, **不可接受**.

**这是 catfish 卖国企的 absolute show-stopper**.

#### 修复路径

| 任务 | 时间 | 成本 |
|---|---|---|
| Apple Developer Program 注册 | 1 周 | $99/年 |
| Apple Developer ID + notarytool 配置 | 1 周 | (含上) |
| Windows EV Code Signing Certificate (DigiCert/Sectigo) | 2-3 周 | $300-500/年 |
| 加 tauri-plugin-updater + pubkey 签名分发 | 1 周 | — |
| GitHub Actions release.yml (build + sign + notarize + publish) | 1 周 | — |
| (可选) Linux .deb / AppImage 签名 (GPG) | 1 周 | — |
| **小计** | **5-7 周** | **~$500/年** (3.5 千 RMB) |

#### 推荐先做这个 — ROI 最高 (3.5 千 RMB / 解锁 100% 客户)

---

### P0 #2: 国密 SM2/SM3/SM4 + 等保 2.0 三级 0%

#### 后果

国企 / 金融招标常用条款:

- **等保 2.0 三级认证**: 必查国密算法实现, JWT RS256 / bcrypt / AES 全部要换 SM2/SM3/SM4
- **密评 (商用密码应用安全性评估)**: GM/T 0054-2018 要求密码服务必须用国密
- **采购围栏**: 中央国资委 / 银保监会 招标公告通常写 "供应商必须取得等保三级 / 密评通过"

**没等保认证 → 招标过不了形式审查, 简称"白送都不要"**.

#### 修复路径

| 任务 | 时间 | 成本 |
|---|---|---|
| 选国密库 (Python: `gmssl-python` / Rust: `crypto-bigint` + 国密支持库) | 1 周 | — |
| JWT RS256 → SM2 签名 (identity-server jwt_signer.py 重写) | 2 周 | — |
| 密码哈希 bcrypt → SM3 (要兼容历史 bcrypt 密码 → 双轨过渡) | 2 周 | — |
| 数据加密 AES → SM4 (SQLCipher → 国密自实现, 或挑国密版 SQLite) | 3 周 | — |
| KMS 集成 (客户的 HSM, e.g. 卫士通 / 三未信安 / 沃通) | 2 周 | — |
| **等保 2.0 三级认证流程** | 6-12 月 | **100-200 万 RMB** |
| 密评二级认证 | 6 月 | 50-100 万 RMB |
| **小计** | **6-12 月** | **150-300 万 RMB** |

#### 这是 catfish 进国企的 ticket price, 但**个人扛不起**

实际路径选项:

**Option A (理想)**: 找一个中型国企 sponsor partner, 让他们出认证费用 (作为 pilot 一部分), catfish 拿 license fee 分成
**Option B**: 找投资, 国资背景 VC (e.g. 中关村创投 / 上海科创基金) 对等保认证不陌生
**Option C**: 跳过等保, 只卖给私企 (但商业化天花板就低了 10x)

---

### P0 #3: License key 系统完全 0

#### 后果

dual licensing 商业化的**字面前提**:
- 社区版 (Apache 2.0 / SSPL): 免费, 给个人开发者 / 小公司
- 企业版: 收 license fee, 跑商业 license server 校验

**现在 catfish grep `license_key` 全仓 0 命中** — 字面意义上 "想收钱也没法收".

#### 修复路径

| 任务 | 时间 | 成本 |
|---|---|---|
| License server (自建): 颁发 / 撤销 / 查询 license | 2 周 | — |
| Edge 客户端校验 (启动时 + 定期联网 + 离线 grace period 30 天) | 1 周 | — |
| License file 格式 (RSA / SM2 签名的 JWT-like) | 1 周 | — |
| Admin UI 加 license 管理 (颁发 / 续期 / 撤销 / 查看用量) | 1 周 | — |
| License 跟 admin RBAC 配合 (企业 admin 给员工分配 seat) | 1 周 | — |
| **小计** | **6 周** | **0 (开源工具栈)** |

#### 这是 catfish 必须自己写的, 6 周内可做完, 跟 P0 #1 签名同步推

---

### P0 #4: SSO 升级 (OIDC 加固 + SAML + LDAP + 钉钉 + 飞书)

#### 后果

OIDC 现在是 demo 级 (identity-server 自己标 "dev server"), 缺:
- PKCE (RFC 7636) — 公开客户端必备
- refresh token rotation
- CSRF state 校验
- redirect URI 白名单 (现在估计是 wildcard 或宽松匹配)
- Rate limit / brute force protection
- `/.well-known/openid-configuration` 完整 metadata

99% 中国国企用:
- **SAML 2.0** (Active Directory Federation Services / Azure AD)
- **LDAP / AD** (内网 LDAP server)
- **钉钉** (字节跳动 / 阿里系)
- **飞书** (字节跳动 / 字节体系)
- **企业微信** (腾讯系)

**catfish 一个都不支持** → 客户接入卡在第一步.

#### 修复路径

| 任务 | 时间 | 成本 |
|---|---|---|
| OIDC 加固 (PKCE + state + nonce + JWKS + 白名单) | 2 周 | — |
| SAML 2.0 (用 `python3-saml` lib) | 2 周 | — |
| LDAP / AD bind (用 `ldap3` lib + 部门 / 组同步) | 1.5 周 | — |
| 钉钉 OAuth2 adapter (https://open.dingtalk.com/) | 1.5 周 | — |
| 飞书 OAuth2 adapter (https://open.feishu.cn/) | 1.5 周 | — |
| 企业微信 OAuth2 adapter | 1.5 周 | — |
| Edge 客户端 SSO 流程 (Tauri 弹浏览器 → callback) | 2 周 | — |
| **小计** | **12 周** | **0 (开源 lib)** |

#### 国密版本 + 等保认证完成后再做, 否则做了也没法卖

---

### P0 #5: Fleet management + Telemetry 完全 0

#### 后果

50 人以上规模:
- 员工 A 电脑崩了 → IT 不知道 (没崩溃报告)
- 想给所有员工统一推一个新 skill / MCP → 没办法 (无中央配置下发)
- 想统一禁用某个有漏洞的 skill → 没办法
- 想看哪些员工没装最新版 → 没办法
- 想给特定员工配 GPT-4o 而其他人用 Qwen → 部分有 (quotas.yaml per-user 可控)

**50 人以上, IT 拒绝采购**.

#### 修复路径

| 任务 | 时间 | 成本 |
|---|---|---|
| Telemetry: self-host GlitchTip (开源 Sentry) | 1 周 | — |
| sentry-rust + sentry-react 集成 (尊重数据零出端 → 配置不上报对话内容) | 1 周 | — |
| Fleet API: 边缘机器盘点 + 在线 / 离线状态 | 2 周 | — |
| Fleet API: skill / MCP / 配置远程下发 | 3 周 | — |
| Fleet API: 远程禁用员工设备 / 强制升级 | 2 周 | — |
| Admin UI 加 Fleet dashboard (机器列表 / 状态 / 推送) | 2 周 | — |
| Edge agent: 跟 Fleet API 通信的常驻进程 | 2 周 | — |
| **小计** | **13 周** | **0** |

#### 跟数据零出端 reconcile

Telemetry 是个**理念矛盾点** — "数据零出端"vs"telemetry 上报错误信息". 解决:
- 上报**仅崩溃 stack trace + minidump** (无对话内容)
- 上报前**员工 opt-in** (默认关闭, 出错时弹"是否上报?")
- minidump 自动 scrub PII (用 sentry-rust 自带的 PII scrubbing)
- Telemetry server **企业自托管** (GlitchTip on-premise, 不发 catfish 中央)

这样既保持 "数据零出端" 哲学, 又让 IT 能维护.

---

## 3 个 P1 (50+ 人规模必备, 但不是签约前置)

### P1 #1: SQLCipher 启用 (本地数据库加密)

**现状**: `rusqlite = { features = ["bundled"] }` 未启用 `bundled-sqlcipher`, **本地 SQLite 明文存储**.

**后果**: 等保审计会记 finding "本地敏感数据未加密". 不是 show-stopper 但扣分.

**修复**: 1 周. 改 `Cargo.toml: features = ["bundled-sqlcipher"]` + 加 `PRAGMA key = '...'` + 跟 keyring crate 集成 (key 存 keychain). 历史数据迁移要小心.

### P1 #2: 客户文档完善 (用户手册 / 管理员手册 / OpenAPI / 灾备 SOP)

**现状**: docs/ 90+ 篇但绝大多数是内部 BL/RCA. 客户面向只 SSO + DEPLOYMENT + QUICKSTART 5 篇.

**后果**: 国企投标技术分扣分. 客户 IT 上手成本高.

**修复**: 2-3 月.
- 用户手册 (员工怎么用 catfish, 每个 tab 详细说明)
- 管理员手册 (IT 怎么部署 / 配 SSO / 配 quota / 故障排查)
- OpenAPI / Swagger (catfish-gateway + identity-server 全 API 文档)
- 灾备 SOP (备份 / 恢复 / 数据迁移)
- SLA 模板 (响应时间 / RTO / RPO)
- 合规白皮书 (数据零出端架构图 + 国密说明 + 等保对照表)

### P1 #3: 金额账单 + 月度成本中心结算

**现状**: Quota 单位是 tokens, 没金额映射. 没账单 / 发票 / 部门成本分摊.

**后果**: 国企 IT 不会接受 "300M tokens", 要看"3.2 万 RMB / 月". 部门成本分摊是国企财务硬需求.

**修复**: 1 月. 加 model_pricing.yaml + monthly_billing endpoint + PDF 发票生成 (用 `weasyprint`).

---

## 3 个 P2 (锦上添花)

### P2 #1: i18n 国际化

**现状**: UI 全中文硬编码. `zh-CN` 多处硬编码.

**修复**: 2-3 周用 react-i18next + 抽中文文案 + 加英文资源. 但**短期不影响国企**, 等出海再做.

### P2 #2: 跟 IDE 集成 (VSCode / Cursor / IntelliJ 插件)

**现状**: catfish 是独立桌面 app, 没 IDE 集成.

**修复**: 4-6 周每个 IDE. 但**不是商业化前置**, 锦上添花.

### P2 #3: Voice / OCR / 多模态

**现状**: 部分录屏, 没语音 / OCR.

**修复**: 6-8 周. 但**不是商业化前置**.

---

## 意外的好消息

### ⭐ 离线 LLM 集成 — **已完整**

`central/llm-gateway/config/models.yaml:26` 有 `catfish-private-main` (内网 Qwen 122B). chain fallback 顺序 "内网优先 → 公网兜底". app.py 有 ollama_stub / llamacpp_stub. **catfish 已经 ready 给军工 / 涉密客户**.

这是 catfish 跟 Anthropic / OpenAI Desktop 比的真护城河 — 他们一台 air-gapped 机器都跑不了, catfish 可以.

### ⭐ Quota / Budget — **部分已完整**

`config/quotas.yaml` 4 层 (defaults / per-user / per-model / per-department). AdminPage 有 `/admin/quota` + `/admin/quota/events` (筛选 + 分页 + CSV). `metrics.py:267` 有 `dept_filter`.

只缺金额映射 / 账单 / 发票 / 月度成本中心结算 → P1 1 月可补.

---

## 24 月 Roadmap (按个人/合作 3 种路径)

### 路径 A: 个人坚持 (5+ 年磨)

- catfish 一直 stay 个人开源项目
- P0 #1 (签名) + P0 #3 (license key) 自己做, 6 月内能搞定 (1.5 万 RMB Apple/EV 费)
- P0 #2 (国密 / 等保) **跳过** — 个人扛不起 200 万认证费
- 只卖给私企 / 小团队, 商业化天花板 50-100 万 RMB ARR
- **现实**: 5 年可能磨出来一个 "中国版 Cursor", 但跟 Anthropic 等大厂正面竞争输

### 路径 B: 找联合创始人 (推荐)

跟 catfish 配合的联合创始人:
- 一个**法务 / 销售 / 商业**背景 (跟你互补, 你是技术, 他/她是商业)
- 一个**安全 / 合规**专家 (懂等保 / 密评 / 国密)

12-18 月内:
- 走 Y Combinator 中国分院 / 真格基金 / 红杉中国 种子轮 (200-500 万 RMB)
- P0 全套做完
- 拿 2-3 国企 pilot

### 路径 C: 找国企 sponsor partner

跟一个**中型国企** (e.g. 中国移动研究院 / 国家电网科技部 / 大型央企 IT 子公司) 谈:
- 他们给 catfish 200 万启动资金 + 第一个客户 + 等保认证支持
- catfish 给他们 license 优惠 + 优先 feature
- 类似 Confluent + Kafka + LinkedIn 模式

12-18 月内:
- 第一个客户跑通, catfish 拿到等保认证
- 之后用这个认证去其他国企开拓

---

## 建议优先级 (modified from PATENT-LANDSCAPE + MOAT-ASSESSMENT)

### 1 月内 (现在做)

- [ ] **优先 de-risk 职务发明** (法律先行 — 跟单位 HR 邮件留痕)
- [ ] **审计现状 docs/CAPABILITY-GAPS.md 给联合创始人 / 投资人看**
- [ ] **决定路径 A/B/C** (个人 / 找联合创始人 / 找国企 sponsor)

### 1-3 月

- [ ] **P0 #1 数字签名 + auto-updater** (5-7 周, 3.5 千 RMB) — 解锁安装
- [ ] **P0 #3 License key 系统** (6 周, 0 RMB) — 解锁收钱
- [ ] **file patent 1 (客户端零外泄 双盲)** — 见 PATENT-LANDSCAPE
- [ ] **file US provisional 方向 2 (SSE inline approval)** — 抢 AeneasSoft 时间窗

### 3-6 月

- [ ] **找 1-2 国企 pilot 客户** (路径 B/C)
- [ ] **加 P1 #1 SQLCipher** (1 周)
- [ ] **catfish GitHub public** (patent file 后 1 月)
- [ ] **加 P1 #2 客户文档完善** (2-3 月)

### 6-12 月

- [ ] **P0 #2 国密 + 等保认证流程启动** (路径 B/C 必备)
- [ ] **P0 #4 SSO 升级 (OIDC 加固 + SAML + LDAP + 钉钉 + 飞书)** (12 周)
- [ ] **P0 #5 Fleet management + Telemetry** (13 周)
- [ ] **加 P1 #3 金额账单** (1 月)

### 12-18 月

- [ ] **等保 / 密评认证完成**
- [ ] **第一个国企客户商业 license 收入**
- [ ] **catfish Foundation 启动 (跟 MOAT-ASSESSMENT 配)**

### 18-24 月

- [ ] **P2 i18n / IDE 集成 / Voice (锦上添花)**
- [ ] **3-5 国企客户 + 100 万 RMB ARR**
- [ ] **PCT 国际申请 (如 catfish 跨过美国 / 欧盟门槛)**

---

## 4 份报告总览 (catfish 战略 / 战术包)

| 文件 | 视角 | 用途 |
|---|---|---|
| `PATENT-LANDSCAPE.md` | advisor (file patent) | 决定 file 哪几个方向 |
| `PATENT-EXAMINER-AUDIT.md` | 审查员 (default reject) | 校准 patent 授权概率预期 |
| `MOAT-ASSESSMENT.md` | 战略 (7 Powers) | 真护城河 vs 表面 feature 区分 |
| **`CAPABILITY-GAPS.md`** (本份) | **战术 (要 build 什么)** | **具体补能力 + 24 月 roadmap** |

---

## 最 honest 的一句

catfish **作为个人 marathon 项目 6 个月沉淀**, 是个**完整 demo + 部分商用基础**, 但**离"商业产品"还差一个完整 pipeline** (签名 / 国密 / SSO / fleet / 等保, 200+ 万 RMB / 12-18 月).

**你最大的资产是 catfish 这个产品 + 你这个人 + 国企 expertise — 而不是某一个具体 feature**. 想商业化, 必须把"个人 marathon"升级为"organization", 一个人扛不动.

**最有可能的成功路径是路径 B (找联合创始人) 或路径 C (找国企 sponsor)**, 不是路径 A (个人坚持). 路径 A 也 OK, 但天花板低.

— 报告完
