# 5/14 demo · 客户 Q&A 题库 (30+ 问)

> 基于 5/6 SECURITY-REVIEW + DATA-FLOW-DIAGRAM 整理. 5/13 rehearsal × 2 用.
>
> **使用法**: 打印, 鸿波 + 我各一份. 客户问到大致对得上的, 直接照念关键句; 没对上的临时组合.
> **铁律**: 不知道的不要硬编, 答 "这个我们带回去 24h 内书面回复".

---

## A · 安全 (8 问)

### A1. "鲶鱼有没有过安全审查?"
- 内部: 5/6 完成 7-gap 安全盘点 (T1 数据出境 / T2 任意代码 / T3 凭证 / T4 供应链 / T5 网络暴露 / T6 webview / T7 审计) 全部修了
- 自动化: cargo audit (1067 advisory 0 vulnerability) / pip-audit (4 组件 0 CVE) / npm audit (0 CVE) / gitleaks (源码 0 secret hit), 都进 CI 每次 PR 跑
- 给客户: 拿 `SECURITY-REVIEW-2026-05-06.md` + `audit-summary.md` 直接看
- 第三方渗透测试 5/22 后会做 (人选 TODO 鸿波)

### A2. "员工电脑跑这个会不会被局域网扫到端口?"
- 5/6 修了 — gateway / skills-hub 默认绑 `127.0.0.1` (只本机), 之前是 `0.0.0.0` 暴露所有网卡
- 私有部署服务器 (集中 LLM 网关) 才显式 `HOST=0.0.0.0`, 配套防火墙规则
- 员工电脑跑 = 同事电脑扫不到端口, 不能蹭 quota / 越权调用

### A3. "LLM 调 execute_code 能读员工电脑全盘吗?"
- 鲶鱼有 dispatcher 层守卫拦 25 类危险操作:
  - 凭证: `~/.ssh` / `~/.aws/credentials` / `Keychain` / `/etc/shadow` / `~/.netrc` / `~/.docker/config.json`
  - 外联: `curl http` / `wget` / `requests.get/post` / `urllib.urlopen` / `httpx` / 原始 socket
  - 危险 shell: `rm -rf /` / fork bomb / `dd if=` / `mkfs` / `chmod 777 /`
- 命中 → 立即拒绝执行 + 写 audit log "exec_guard"
- 5/22 加 nsjail/sandbox-exec 真技术沙箱

### A4. "员工把密码不小心粘 chat 里怎么办?"
- `prompt_security.py` 检测 ≥40 种 secret 模式 (api key / SSH / JWT / 数据库连接串 / 密码)
- 检测到的密码值**不写进 audit log** (只标记 `prompt_credential_detected`)
- 给员工弹 banner: "改用 keychain://xxx 引用"
- prompt 本身**仍发上游 LLM** (技术拦不掉, 但 audit 看不到密码值)

### A5. "skills hub 拉下来的 skill 会不会被人投毒?"
- 5/6 修了 — 客户端 `_install_from_hub` 强制 sha256 校验
- hub 元信息含 `files_sha256: {file: hash}`, 客户端拉文件后算 hash 比对, 不匹配 → 拒装 + 报 "中间人攻击或 hub 被篡改"
- 严格模式 `CATFISH_HUB_REQUIRE_HASH=1`: 没 hash 的 skill 直接拒装
- skills-hub 自己只在客户内网部署 (不接外网), 双层防护

### A6. "你们仓库里有没有写死的 api key?"
- gitleaks 扫了 8 类正则 (AWS / GitHub PAT / OpenAI / Google / Anthropic / JWT / 私钥 PEM / 字面量密码) 全过, 源码 **0 hit**
- 真凭证走环境变量注入 (`INTERNAL_LLM_BASE_*` / IdP credentials), 不入版本库
- CI 每次 PR 跑 gitleaks, 带 secret 的 commit 直接 fail

### A7. "Tauri webview 有没有 XSS 风险?"
- 5/6 修了 CSP — 之前 `csp: null` (开发期默认), 现在显式收紧:
  - `default-src 'self'`
  - `script-src 'self' 'wasm-unsafe-eval'` (rehype-highlight 需要 wasm)
  - `connect-src 'self' http://127.0.0.1:* tauri:` (只允许本机 + tauri 协议)
  - `object-src 'none'` / `base-uri 'self'`
- 阻断外部脚本注入 + 跨域 fetch

### A8. "鲶鱼自己会不会有 backdoor?"
- 全开源 (员工 `cd ~/person_task/catfish && git log` 查所有 commit)
- CI 跑 cargo/pip/npm audit + gitleaks, 任何 commit 带漏洞或 secret 自动 fail
- 客户可以要求 SBOM (CycloneDX) — 5/12 前出
- 客户可以做独立审计 (代码全可见)

---

## B · 数据隐私 (7 问, 5/6 晚最终定位)

> **核心口径** (鸿波 5/6 晚拍板): 鲶鱼是**员工的职业资产**, 公司给员工配 (B2C2B 模式). 数据完全属于员工, 跳槽带走. 不是公司控员工的工具.
>
> **隐私 4 道边界**:
> 1. **员工敏感数据 0 出员工 mac** ✅ — 文件原件 / journal / memory / chat 历史 / USER.md 在 `~/.catfish/`
> 2. **chat prompt 流过客户内网** ⚠️ — gateway / LLM 都在客户内网, 部署架构 A/B/C 选粒度
> 3. **客户公司大门 0 出境** ✅ — 鲶鱼平台公司从不见客户数据
> 4. **跨雇主可携带** ✅ — 员工跳槽 cp `~/.catfish/` 带走, 跟公司给员工配 ThinkPad 道理一样

### B1. "员工社保 PDF 上传, 320 个人身份证号会发到哪?"
- 默认 prod 配置: 全走客户内网 LLM (Hermes), **不出客户公司网**
- PDF 原件**永远只在员工本机** (`~/.catfish/uploads/`)
- gateway 只见 preview (~5K 字), 完整内容靠 LLM 调 execute_code 在员工本机读
- 5/6 PDF 结构化模式触发后, 完整 320 条 JSON 在员工本机 `/tmp/`, gateway 只见路径
- **鲶鱼平台公司**: 从不见, 我们部署完撤场, 触不到

### B2. "员工 chat 跟外网 LLM 通信吗?"
- 看配置档位:
  - **档位 1 (推荐 prod)**: 全部内网 (Hermes / 客户私有 qwen), **0 字节出客户网**
  - **档位 2 (开发期)**: 内网主 + 外网 fallback, 内网炸时走外网, chat 顶部 banner 提示
  - **档位 3 (不建议)**: 全外网, 内部测试用
- 详见 `DATA-FLOW-DIAGRAM.md` § 0 + § 4

### B3. "我作为信安部门, 怎么实时审计?"
- 双层 audit:
  - gateway 中央 (metadata): 谁 / 什么时间 / 哪个 model / 多少 token / latency, **默认不含 prompt 内容**
  - tool-bridge 边缘 (含 args_preview): 员工本机, 不出网
- 都是 jsonl, 接 SIEM (`CATFISH_AUDIT_PATH=/var/log/catfish/...`, fluent-bit 配置见 `DEPLOYMENT-RUNBOOK § 6`)
- **真要审 prompt** 客户 IT 改 gateway verbose log 即可 (在客户内网, 自己控)

### B4. "鲶鱼平台公司会不会偷偷上传员工聊天?"
- **不会, 我们触不到**. 鲶鱼是私有部署软件, 部署完撤场, 后续维护走客户提供通道
- chat 历史**只存员工本机** SQLite (`~/.catfish/sessions/`), gateway 不持久化 prompt
- 上传文件原件只在 `~/.catfish/uploads/`, 不发出去 (除非 LLM 主动调 execute_code 读)
- local-search 索引 / catfish_remember / user_profile / style_fingerprint / Keychain 引用 全员工本机

### B5. "员工的 chat 公司能看到吗?" ★ (5/6 晚最终定位)

**先讲鲶鱼定位**: 鲶鱼是**给员工配的生产力工具, 不是公司控员工的工具**. 类比公司给员工配 ThinkPad — 笔记本是公司的, 但员工装的软件 / 工作思路是员工的.

**默认部署 = A 员工本机**, 公司 IT 默认看不到 chat (除非通过 MDM 远控员工 mac, 跟鲶鱼无关).

**如果公司就要集中监管员工 chat** (传统央企监管思路): 改成 B 部门 gateway / C 公司 gateway, 公司 IT 看 audit metadata + 配 verbose log 看 prompt. 但这把鲶鱼降级成 ChatGPT 企业版式的集中工具, **失去鲶鱼"员工拥有 / 跨雇主可携带" 的灵魂**. 想集中管的客户, 直接买 ChatGPT 企业版可能更对路.

**鲶鱼是给信任员工的公司用的**. 你给员工配生产力工具, 员工产能 ↑, 而员工的工作画像和经验属于员工 — 跟工伤保险 / 劳动合同的逻辑一致.

### B6. "员工的身份认证走哪?"

(原 B6, 内容不变)

### B7. "员工离职带走鲶鱼数据怎么办?" ★ 新 (5/6 晚)

**这是 feature 不是 bug**. 鲶鱼定位: 员工的职业资产, 跳槽可携带.

- 员工离职 → 直接 cp `~/.catfish/` 到自己的新 mac
- 新公司 LLM 接上 (新公司部署的 Hermes 或员工自己买的 LLM 服务) → 鲶鱼继续懂员工, 6 个月画像不丢
- **公司啥都没失去** — 公司核心数据 (财务 / 合同 / 客户库 / 商业机密) 在公司服务器, 不在员工鲶鱼里. 员工带走的是**自己的工作画像 + 经验记忆**, 这本来就是员工的智力资产
- 类比员工离职带走自己的笔记本本子 / 多年工作经验 — 公司不会觉得是泄密

**对比 ChatGPT 企业版**: 员工离职就**完全失去访问权**, 6 个月跟 ChatGPT 积累的工作上下文清零. 这其实是公司绑架员工的设计, 央企 HR / 法务实际**反对**这种设计 (劳动法不支持公司持有员工智力资产).

**给客户的话**:
> "鲶鱼是员工的'数字大脑延伸', 跳槽带走是产品设计. 你公司给员工配鲶鱼是给员工'装配数字工作能力', 类比给员工配 ThinkPad / 工伤保险. 员工产能 ↑ 30% 是公司收益, 员工带走自己的画像跟公司核心数据无关."

### B6. "员工的身份认证走哪?"
- 客户 IdP (内网) 签 RS256 JWT, gateway `auth/oidc.py` 验签
- token 仅 in-memory, 不落盘
- a2a (agent-to-agent) 通信也带签名 + iss/aud/jti 校验
- dev token 仅开发模式启用, prod 自动失效

---

## C · 部署 (5 问)

### C1. "需要客户买什么硬件?"
- 员工电脑: 16GB+ RAM (Companion + Chrome MCP), Apple Silicon 或 Intel x86 macOS 13+ (Tauri 2 minimum)
- 客户内网 LLM 服务器: 推荐 4×A100 / 8×A6000, 跑 Hermes 122B 主力 + qwen-flash backup
- gateway / tool-bridge: 员工本机或客户共享内网服务器都行 (轻量, 1 vCPU + 2GB 够)
- skills-hub: 客户内网共享一台

### C2. "部署多久?"
- 单员工 dev token 模式 (内部测试): 30 分钟 (装 Companion .app + 配 gateway env)
- 全公司 prod 模式 (含 SSO 接 IdP / 内网 LLM 接好 / Hub 部署): 1-2 周, 含联调
- 5/12 前我们出**部署 runbook** (一步一步)

### C3. "升级怎么走?"
- Companion: Tauri auto-updater (员工无感, 后台拉 .dmg + 下次启动应用)
- gateway / tool-bridge: 客户 IT 跑 `git pull && docker compose up -d --build`
- skills: skill_watcher 监听 `~/.catfish/skills/` 文件变化, 自动 reload tool-bridge
- 大版本 (1.x → 2.x): 客户 IT 配合, 客户预审 changelog

### C4. "员工不会用怎么办?"
- 第一次启 Companion 有 onboarding (`setup-cowork` skill 5 步引导)
- 员工有问题直接问鲶鱼自己 ("帮我设置打字声", "记一下你应该叫我老李") — 鲶鱼自己改自己
- 客户 IT 如果接管理: catfish-tool-bridge admin 接口给汇总数据 (员工活跃度 / 最常用 skill / 失败率)

### C5. "断网了能用吗?"
- 不能 LLM 调用 — 但 Companion + tool-bridge + 本地 skills 全本机, 历史记录能看
- 员工**真要断网用** = 走客户内网 LLM (内网不依赖外网), Hermes 自己内部 GPU 服务跑
- 真物理离线 (笔记本带去外面没 WiFi) — 接不上内网 gateway, chat 报"上游连接失败", 文件功能仍可用 (本地 skill)

---

## D · 性能 (4 问)

### D1. "一个 122B 模型跑得动吗?"
- Hermes 私有 122B (qwen v3.5 122b A10b): 4×A100 推理, 单 token 30-50ms, 首 token 1-2s
- 公网 fallback (qwen-flash 30b moe): 切换时单 token 10-20ms, 但走公网延迟 +200-500ms
- 主流 chat 场景: 员工等首响应 ~2s, 流式后 30 token/s 体感跟 ChatGPT 差不多

### D2. "100 人公司同时用扛得住吗?"
- 限速: gateway quota.py 控制 user_sub × model 速率 (默认每分钟 60 req / 100K token)
- 真大并发瓶颈在 GPU 推理 — 4×A100 同时跑大概 30-50 人 active. 超了走 fallback 链 (qwen-flash 256K 上下文支援)
- 客户 IT 监控 GPU 使用率, 80%+ 加机器
- gateway 自己 bottleneck 极少 (Python uvicorn + asyncio, 单核 1000+ qps)

### D3. "上传一个 100MB 的 Excel 多久?"
- parse_file.py preview-only: 写 tmp + 拿前 20 行 + 总行数, ~2-3s
- LLM 看到 preview + meta + 完整文件路径, 自己调 execute_code 用 pandas 读完整, 30-60s 出分析
- 我们不传完整 Excel 给 LLM, 完整文件留员工本机

### D4. "桌宠 + 主动闲聊会不会卡机?"
- 桌宠副窗: NSPanel 透明 alwaysOnTop, 200×200 图层, 桌宠 idle 时基本不占 CPU
- pet_hover tracker (鼠标穿透): 80ms 一次轮询全局鼠标位置, < 0.1% CPU
- pet_pop_bubble polling (气泡通信): 300ms 一次轮询 Mutex, ms 级
- 主动闲聊调度: 1 分钟 1 次 tick + 每天 3 次 LLM call, 几乎无感

---

## E · 商业模式 / 合规 (4 问)

### E1. "私有化部署 vs SaaS 价格?"
- 私有化 (推荐央企 / 高敏感): 一次性 license + 年维护 (具体看合同)
- SaaS (鲶鱼云): 中小企业用, 走鲶鱼云 LLM, 数据出境到我们这, 不推荐央企
- 中央企业 100% 私有化, 客户 IT 内网托管, 鲶鱼提供镜像 + 文档 + 维护

### E2. "数据所有权?"
- 全归客户. 员工 chat 历史 / 上传文件 / audit log / skills 全在客户机器
- 鲶鱼公司不见任何客户数据
- 合同条款明确

### E3. "下线鲶鱼员工数据会留下吗?"
- 卸载 Companion: 员工本机 `~/.catfish/` 整个删掉就干净 (含 chat / uploads / memory / audit)
- 客户内网 gateway / hub 服务器: 客户 IT 自己控
- 鲶鱼公司端: 没存任何客户数据

### E4. "这跟 ChatGPT 企业版区别在哪?"
- 鲶鱼: 100% 私有部署 / 内网闭环 / 全开源代码可审计 / 接客户自己的 LLM 不绑死 / 上下文工具调用本地驱动 (skills + tool-bridge)
- ChatGPT 企业版: SaaS 走 OpenAI 美国 / 数据出境 / 闭源 / 绑死 OpenAI 模型
- 央企必须 100% 内网闭环 — ChatGPT 企业版不达标

---

## F · 技术细节 (4 问)

### F1. "为什么用 Tauri 不用 Electron?"
- 体积: Tauri ~10MB vs Electron ~150MB
- 内存: Tauri 用系统 webview (macOS WKWebView) ~80MB vs Electron ~300MB
- Rust 后端: 跨进程 + 文件 IO 性能 + 安全 (内存安全 / 类型系统)
- Tauri 2 多窗口 + 系统集成 (托盘 / 通知 / 全局快捷键) 都成熟
- 缺点: 多 webview 跨窗口事件协议有坑 (5/6 桌宠气泡踩过, 用 Rust polling fallback 解决)

### F2. "桌宠的'人格'是怎么实现的?"
- LLM 调用前注入 system prompt: USER.md (员工自填爱好 / 工作风格) + SOUL.md (鲶鱼角色定位 + 红线)
- catfish_remember 工具: LLM 主动记关键事实 (员工口音 / 喜欢的措辞 / 上次提过的人), 下次调用自动注入
- BL-MM 系列: 双层记忆 (硬事实 + 影响), Dashboard 卡显示员工能改

### F3. "skill 的开发门槛?"
- 员工自己写: SKILL.md (markdown frontmatter, 描述 skill 干啥 + 输入参数 schema) + script.py (一个 render() 函数)
- 5 行就能起一个最简 skill, 复杂的 (weekly-report) ~200 行
- skill_watcher 实时 reload, 不用重启
- 客户 IT 集中维护: 部署到 skills-hub, 全公司员工一键装

### F4. "为什么用 markdown 不用 JSON / yaml 配 skill?"
- markdown frontmatter 可读性高, 员工不用学新格式
- description 字段用自然语言, LLM 看着像 docstring 一样直观
- script.py 跟 SKILL.md 同目录, 整体 git 友好
- 已经有 ChatGPT skills / Anthropic Skills 类似设计, 业界正在收敛

---

## G · 兜底句 (无法答上时)

> "这个细节我现场答不严谨, 24h 内书面给你正式回复, 怕错了反而误导."

> "我们今天 demo 演示重点是 [X], [Y] 这块的具体实现你列个清单我们带回去技术对接."

> "客户信息安全部门有具体要求, 我们出一份合规说明对照交你们审."

---

## 5/13 rehearsal 流程

1. 鸿波念问题, 我答 (照本宣科)
2. 我念问题, 鸿波答 (临场感)
3. 互相挑硬骨头 (D2 性能 / B1 数据出境 / A3 execute_code 这种最容易翻车的)
4. 不顺的标记 ⚠️, 5/14 前再过

每轮 30 分钟, 共 2 轮.
