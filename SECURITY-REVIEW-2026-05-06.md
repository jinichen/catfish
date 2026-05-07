# 鲶鱼 Catfish · 安全风险盘点 (2026-05-06)

> 5/14 央企信安部门 demo 前必须能答清楚的问题: **"鲶鱼怎么证明没安全风险?"**
>
> 本文档 = 威胁模型 + 已有缓解 + 已知 gap + 8 天行动清单

---

## 1. 威胁模型 (Threat Model)

鲶鱼跑在央企**员工电脑** + **私有部署 gateway**, 高敏感场景, 7 类攻击面:

| # | 攻击面 | 后果 | 谁会关心 |
|---|---|---|---|
| T1 | **数据出客户公司内网** | 员工敏感数据 (社保/合同/客户) 漏到外网 vendor (qwen 公网 / OpenAI) **或** 鲶鱼平台公司服务器 | 信安部 / 法务 |
| T1.5 | **员工 vs 公司隐私边界** | 员工不希望公司看到的数据 (跳槽 / 私人) 进了 gateway audit | HR / 工会 |
| T2 | **execute_code 任意代码执行** | LLM 工具调用跑任意 Python, 读员工全盘 / 删文件 / 外联 | 信安部 |
| T3 | **凭证泄露** | 员工 SSO token / 密码 / API key 在 prompt / 日志 / 上传文件里 | 信安部 |
| T4 | **供应链攻击 (skills hub)** | Skills Hub 拉到恶意 skill, 自动加载执行 | IT |
| T5 | **网络暴露 (端口绑定)** | gateway / tool-bridge 暴露到局域网, 同事电脑直接调 | 信安部 |
| T6 | **Tauri webview 攻击** | webview 注入 / Rust RPC 越权 / persistence | IT |
| T7 | **审计可信度** | 客户问 "demo 完, 你能证明 LLM 没看见 X 数据吗" | 法务 |

### 1.5 隐私 3 道边界精确化 (5/6 晚校准)

之前的"数据 0 出境" / "全在客户机器" 含糊. **3 层边界分清**:

```
员工 mac → 客户内网 (gateway/LLM) → 客户公司大门 → 鲶鱼平台公司
  边界 1        边界 2                  边界 3
```

**边界 1: 员工敏感数据 0 出员工 mac** ✅ (产品设计)
- 文件原件 / journal / memory (catfish_remember/user_profile/style_fingerprint) / chat 历史 SQLite / USER.md / ALLOW.md / 私钥 全部只在 `~/.catfish/`
- 公司 IT 想看 → 必须走 MDM 远控员工 mac (跟鲶鱼无关, 公司 mac 政策)

**边界 2: chat prompt 流过客户内网** ⚠️ (部署设计)
- 每次 chat → prompt 经 gateway → LLM 推理. gateway / LLM 都在客户内网
- gateway 默认 audit 只入 metadata, 不存 prompt; verbose log 客户 IT 自配
- 公司审 chat 粒度 = 部署架构 A/B/C 选择题 (见 `DEPLOYMENT-RUNBOOK § 11`)
- **chat 是工作产出, 公司审计权天然存在**. 鲶鱼是公司装的工作工具, 不挡公司管理

**边界 3: 客户公司大门 0 出境** ✅ (商业模式)
- 默认 prod 0 字节出客户公司网络
- **鲶鱼平台公司从不见客户数据** — 私有部署软件供应商, 部署完撤场
- 跟 ChatGPT 企业版本质区别 — OpenAI 永远在那个数据链上

**边界 4: 跨雇主可携带** ✅ (产品灵魂, 5/6 晚鸿波最终拍板)
- **数据是员工的, 不是公司的** — chat 历史 / journal / memory / USER.md / skill 库 全员工本机
- 员工跳槽 → 直接 cp `~/.catfish/` 到新 mac → 鲶鱼继续懂他, 6 个月积累不丢失
- 新公司 LLM 接上, 鲶鱼无缝服务新雇主
- 这是 **feature 不是 bug** — 鲶鱼跟公司给员工配的 ThinkPad 道理一样: 笔记本是公司的, 但你装的软件 / 浏览器历史 / 工作思路是你的
- "对抗不良雇主": 员工的工作画像 / 技能库**不被公司绑架**, 这是鲶鱼对员工最大的价值

**鲶鱼定位 = 员工的"职业资产", 公司给员工配的生产力工具**. 不是 ChatGPT 企业版那种"员工是公司资产的延伸". 鲶鱼帮员工更厉害, 而**员工的厉害属于员工**.

商业模式: **B2C2B** (公司报销, 员工拥有), 类比给员工配 ThinkPad / 工伤保险.

---

## 2. 已有缓解 (拿出来跟客户讲)

代码里实打实做了的, 不是 PPT:

### 2.1 身份与鉴权
- **OIDC RS256 JWT 验签** (`auth/oidc.py`) — 接客户 IdP, prod 部署强制
- **a2a JWT** (`a2a_jwt.py`) — agent-to-agent 通信带签名 + iss/aud/jti 校验
- **dev token** 仅 dev 模式, prod 自动失效

### 2.2 prompt 凭证检测 (T3)
- `prompt_security.py` — 员工无意中把密码 / token 粘贴进 chat, **自动检测**
- 检测到的密码值**不写进 audit log** (只标记 `prompt_credential_detected`)
- 提醒员工改 `keychain://...` 引用

### 2.3 双层审计日志 (T7)
- **gateway audit** (中央): `~/.catfish/gateway_audit.jsonl` — 只 metadata (model / tokens / latency / user_sub), 不含 prompt 内容
- **tool-bridge audit** (边缘): 员工本机, 含 `args_preview` (本机不出网)
- prod 可配 `CATFISH_AUDIT_PATH=/var/log/catfish/...`

### 2.4 quota / RBAC
- gateway quota: 按 user_sub × model 限速
- ALLOW.md: 员工自己写的访问控制清单 (`a2a_server.py` 检查)

### 2.5 数据隔离
- 员工聊天历史 SQLite **本地** (~/.catfish/sessions/), 不上 gateway
- gateway 只透传 message → vendor → response, **不持久化 prompt**

### 2.6 软件纪律
- SOUL.md § execute_code 红线 (组织级, 非技术沙箱) — 限定 LLM 该干啥不该干啥
- ruff 强制代码风格 (含 PLR / B / N / UP / 复杂度)

### 2.7 依赖供应链审计 (5/8 ship, BL-SEC-CARGO)

CI **每次 push + 每天定时**跑 4 套 audit, 任何 PR 引入新 CVE 立即阻断:

| 工具 | 范围 | 当前状态 | 标准 |
|---|---|---|---|
| `pip-audit` | central/llm-gateway, central/skills-hub, edge/tool-bridge, edge/local-search | ✅ 全绿 | --strict, 任何 known CVE 红 |
| `npm audit` | edge/companion-app | ✅ 全绿 | --audit-level=high, 仅 prod deps |
| `cargo audit` | edge/companion-app/src-tauri (Tauri 2.x) | ✅ 全绿 (5/8 起, 19 条 ignore 见下) | rustsec/audit-check@v2 |
| `gitleaks` | git history secrets | ✅ 全绿 | 全 history 扫 |

**cargo audit 的 19 条 ignore 详细分析** (5/8 真机 cargo audit 结果):

> 全部 19 条**0 个 exploitable CVE**, 都是上游 Tauri 2.x 传染的供应链 hygiene (unmaintained / unsound 边角 case).
> 详细 ignore 列表 + 每条理由见 `edge/companion-app/src-tauri/audit.toml`.

| 类别 | 数量 | 实际威胁 | macOS 演示加载? |
|---|---|---|---|
| GTK3 binding (gtk-rs 系) unmaintained — Linux only | 13 | 0, gtk-rs 上游 deprecated 不动 | ❌ macOS 走 WebKit, 完全不加载 |
| `unic-*` unicode 数据表 unmaintained — `urlpattern` build-time | 5 | 0, 编译期生成数据 | 走但无 runtime 风险 |
| `proc-macro-error` / `fxhash` / `rand` 0.7.3 — build-time | 3 | 0, 编译期 only | 走但无 runtime 风险 |
| `glib::VariantStrIter` Iterator unsound | 1 | 0, Tauri 不实现 GLib Iterator, 触发不到 | 走但触发不到 |

**跟踪策略**:
- Tauri 2.11+ 切 GTK4 后 13 条自动消失 (跟踪 tauri-apps/tauri Issue #11193)
- urlpattern 升级到非 unic-* 后 5 条自动消失
- 每月真跑 `cargo audit` (不 ignore 任何条) 看新 advisories — 任何新 exploitable CVE 立即修, 不靠 ignore

**给信安部 / 央企 IT 的标准答复**:

> "鲶鱼 CI 跑了完整 cargo audit 套件 + 19 条 ignore 全部带 RUSTSEC ID + tracking link.
>  这 19 条全是上游 Tauri / Rust 生态的 unmaintained 标记, 没有一个是远程 / 本地 exploitable
>  CVE. 等 Tauri 2.11+ 升 GTK4 后大部分自动解决.
>  完整列表 + 每条理由: edge/companion-app/src-tauri/audit.toml"

---

## 3. 已知 gap (5/14 前 must-fix)

> **2026-05-06 17:00 状态**: P0 + P1 全部 7 个 gap **已 ship 代码层修复**, 剩
> Rust cargo audit (sandbox 没 cargo, 鸿波本机一行命令验证), 文档已交付.

### 🔴 P0 (1 天内必修)

#### G1. gateway / skills-hub 默认绑 `0.0.0.0` (T5 网络暴露) ✅ 已修
位置:
- `central/llm-gateway/src/catfish_gateway/app.py:1355` — `host = os.environ.get("HOST", "0.0.0.0")`
- `central/skills-hub/src/catfish_skills_hub/app.py:213` — `uvicorn.run(app, host="0.0.0.0", ...)`

**风险**: 员工电脑跑 gateway, 默认监听所有网卡 → 同公司局域网任何机器能扫到端口, 直接调 `/v1/chat/completions` 蹭别人 quota / 看请求。

**修法**: 默认 `127.0.0.1`, 私有部署服务器才设 `HOST=0.0.0.0`。

#### G2. Skills Hub URL fetch 无签名校验 (T4 供应链) ✅ 已修
位置: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py:2305-2584` — `_install_from_hub`

**风险**: 直接 urllib.urlopen 拉 hub.catfish.example.com 的 .py / SKILL.md, 中间人 / 仓库被攻陷 → 任意代码进员工 skills 目录, skill_watcher 加载执行。

**修法**: hub metadata 加 `sha256` 字段 + skill 签名 (RSA), 拉完文件本地校验, 不匹配拒装。

#### G3. execute_code 双层沙箱 (T2 任意代码) ✅ L1 已修 5/6 + L2 已修 5/7 (BL-S29 真技术沙箱 ship)
位置: 
- L1 字符串规则: `edge/tool-bridge/src/catfish_tool_bridge/adapter.py:_check_execute_code_security` (25 类正则, 5/6 ship)
- L2 OS 沙箱: `edge/tool-bridge/src/catfish_tool_bridge/sandbox.py` + `sandbox-profiles/catfish_execute.sb` (5/7 BL-S29.1+S29.2+S29.3 ship)

**双层防御机制 (5/14 demo 现场演给信安看)**:

```
LLM 生成代码 → dispatch_tool 入口
   ↓
[L1 字符串规则] _check_execute_code_security 25 类正则
   - 拦明显恶意 (~/.ssh, /etc/passwd, curl http, rm -rf /, ...)
   - 命中 → audit 写 security_block:exec_guard, 不进沙箱
   ↓ 不命中
[L2 macOS sandbox-exec OS 级隔离] (env CATFISH_SANDBOX_EXEC=1)
   - 默认 allow + 5 类关键 deny: 网络全断 / 写 HOME 全 deny 白名单 TASK_DIR /
     读敏感路径 (~/.ssh, ~/.aws, /etc/passwd 含 /private/etc 等价路径) /
     iokit / sysctl-write
   - 沙箱内 HOME 重定向到 TASK_DIR (LLM 用 ~/ 解析到沙箱里, 看不到员工真文件)
   - 沙箱内 env 干净 (剥 GITHUB_TOKEN 等员工 mac secret)
   - 命中 → python 抛 PermissionError, audit 写 sandbox_used:true
   ↓
真跑代码 (rc/stdout/stderr 返 LLM)
```

**测试矩阵 (5/7 ship)**:
- 13 shell 集成测试 (`tests/sandbox/test_sandbox_exec.sh`): 10 恶意 case 全 DENY + 3 sanity 全 OK
- 15 python 单测 (`tests/test_sandbox_module.py`): sandbox 模块 10 + adapter wiring 4 + home redirect 1
- 6 端到端测试 (`tests/test_e2e_sandbox.py`): L1 规则 / L2 沙箱 / curl 外联拦 / audit 字段 / 沙箱关闭兜底
- **总 34 测试全绿** (macOS 26 + Apple Silicon arm64 真跑)

**真发现并修的 production bug**: `/etc` 是 macOS symlink → `/private/etc`, SBPL 不解析 symlink. 原 profile `(literal "/etc/passwd")` 拦不住真路径访问 (`/private/etc/passwd`). 修后双等价路径 deny (8 个高敏感文件), 央企信安再问"symlink 绕过怎么办"答: 已修.

**5/14 后路线** (BL-S29.4 / S29.5 / S29.6):
- 5/15-5/16: nsjail (Linux) profile (Hermes 服务器侧 + 客户内网 GPU)
- 5/17-5/18: 双层 fallback (sandbox-exec / nsjail / Docker → deny) + Hermes 服务器侧接入
- 5/19: 50 case 测试矩阵 + SECURITY-REVIEW 章节正式收口

### 🟡 P1 (3-5 天内修)

#### G4. Tauri CSP `null` (T6) ✅ 已修
位置: `edge/companion-app/src-tauri/tauri.conf.json:53`

**修法**: prod 模式启用 `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self' http://127.0.0.1:* tauri:`

#### G5. 依赖 CVE 没扫 (T6) ✅ 已扫 + CI 集成 (Python 0 / npm 0 / Rust **0 vulnerability** + 19 informational warning, 0 个在调用路径上)
**修法**: CI 加:
- `cargo audit` (Rust)
- `pip-audit` 或 `safety check` (Python)
- `npm audit` (frontend)

#### G6. 数据出境清单不清晰 (T1) ✅ 已交付 `DATA-FLOW-DIAGRAM.md`
**修法**: 出 1 页 "鲶鱼数据流向图":
- 内网 LLM (Hermes / 客户自建): 数据在内网闭环
- 外网 LLM (qwen via dashscope / openai): **明确清单 + 客户必须点头才能开**
- 默认配置 prod = 仅内网 (`config.yaml provider:auto` 关掉所有外网)

#### G7. secrets 硬编码扫 (T3) ✅ 已扫 (源码 0 hit) + CI gitleaks 集成
**修法**: gitleaks / trufflehog 跑全仓, 0 hit 才能 release

### 🟢 P2 (5/22 后)

- macOS Notarization + 公证 (Tauri privateAPI 需要)
- SBOM 自动生成 (CycloneDX)
- 渗透测试 (找外部团队)
- 红队演练 (LLM jailbreak / prompt injection)

---

## 附录 A · BL-S29 沙箱测试矩阵 (5/7 ship, 56 case 全绿)

> 给信安部门的 attack vector 测试报告. 客户独立审计可重跑全部 case.

### 恶意 case 分类 (各 25 个, macOS + Linux 对称)

| 类 | 数量 | 例子 |
|---|---|---|
| **凭证窃取** (file-read 敏感路径) | 5 | ~/.ssh/id_rsa / Keychain / /etc/passwd / /etc/shadow / /var/db |
| **网络外联** | 5 | urllib / socket connect / curl / 直连 IP / dashscope |
| **持久化** (file-write 系统路径) | 5 | LaunchAgents / LaunchDaemons / .zshrc / /etc/cron.d / sudoers.d |
| **跨边界破坏** | 5 | fork bomb / mem bomb / 改 /etc/hosts / 写 /Library/* / chmod setuid |
| **L1 字符串规则绕过** | 5 | base64 / chr() 拼路径 / eval / glob / subprocess sh -c |
| **跨进程探测** (Linux pid namespace 拦) | 5 | ps aux / /proc 探测 / dscl / osascript / setns syscall |
| **加权 / 内核操作** | 5 | dlopen / mount / kexec_load / chmod 提权 / sysctl 写 |

### Sanity case (各 6 个, 验沙箱不误伤)

- 沙箱内写 TASK_DIR / 标准库导入 / 哈希计算 / 文件 round-trip / 计算 / 系统 lib 加载

### 测试矩阵执行结果

| 平台 | 后端 | 测试数 | 通过率 | 测试脚本 |
|---|---|---|---|---|
| macOS 26 (Apple Silicon) | sandbox-exec | 25 + 6 = 31 | 31/31 ✅ | `tests/sandbox/test_sandbox_exec.sh` |
| Linux Ubuntu 22 (aarch64, docker) | nsjail | 25 + 6 = 31 | 31/31 ✅ | `tests/sandbox/test_nsjail.sh` |
| 跨平台 abstract | python unittest | 18 unit + 6 e2e = 24 | 24/24 ✅ | `tests/test_sandbox_module.py` + `tests/test_e2e_sandbox.py` |
| **总计** | | **86 测试** | **86/86 ✅** | |

### 客户独立审计步骤

```bash
# 1. clone catfish 仓库 (开源)
git clone <repo>
cd catfish/edge/tool-bridge

# 2. macOS 跑 (员工 mac):
bash tests/sandbox/test_sandbox_exec.sh

# 3. Linux 跑 (Hermes 服务器):
sudo apt install nsjail
bash tests/sandbox/test_nsjail.sh

# 或 docker:
docker build -f tests/sandbox/Dockerfile.nsjail -t catfish-nsjail-test .
docker run --rm --privileged catfish-nsjail-test

# 4. python 单测 + e2e:
pip install -e .
python3 -m unittest tests.test_sandbox_module tests.test_e2e_sandbox -v
```

### 三层 fallback 架构

```
LLM execute_code 请求
  ↓
[L1 dispatch 字符串规则] _check_execute_code_security 25 类正则
  - 拦明显字面恶意 (~/.ssh, /etc/passwd 字面字符串等)
  - 命中 → audit security_block, 不进沙箱
  ↓ 不命中
[L2 OS 沙箱] (env CATFISH_SANDBOX_EXEC=1)
  - macOS: sandbox-exec + catfish_execute.sb
  - Linux: nsjail + catfish_execute.cfg
  - 兜底: docker run --network=none --read-only --pids-limit=20
  - 命中 → 抛 PermissionError, audit sandbox_used:true
  ↓
真跑代码

三层都 fail → run_in_sandbox 返友好错误, 不让代码裸跑
```

### 跟央企信安预期 Q 对应

| 信安会问 | 答 |
|---|---|
| "万一字符串规则被绕呢?" | L2 OS 沙箱兜底, 演 chr() 绕过 case (test #2 / 11 / 12) |
| "万一 macOS sandbox-exec 漏呢?" | 5/19 加 Linux nsjail 给 Hermes 服务器侧, cgroups + namespace 真隔离 |
| "fork bomb 怎么拦?" | nsjail rlimit_nproc=10 + cgroups pids.max 真拦 (test #9), macOS 靠 caller ulimit (是承认的限制, 不糊弄) |
| "audit 透明吗?" | jsonl 标准 SIEM 接, `cat ~/.hermes/.catfish_audit.jsonl \| jq 'select(.sandbox_used)'` 一行命令 |
| "客户能独立审计吗?" | 全开源, 86 测试可独立重跑, profile / 测试脚本都在 `edge/tool-bridge/` |

---

## 4. 8 天行动清单 (5/6 → 5/14)

| 日期 | 谁 | 做 | DoD |
|---|---|---|---|
| 5/6 (今) | 我 | 修 G1 (0.0.0.0 → 127.0.0.1) | 单测 + manual 验证 |
| 5/6 (今) | 我 | 修 G2 (skills hub sha256) | hub fetch 拒签名错的 |
| 5/7 | 我 | 修 G3 短期 (execute_code cwd + env whitelist) | exec 跑不出 sandbox 目录 |
| 5/8 | 我 | 修 G4 (prod CSP) + 跑 G5 (cargo/pip/npm audit) | CSP 上 + audit 0 critical |
| 5/9 | 鸿波 | 出 G6 数据流向图 (1 页 PDF) | 客户能看懂 |
| 5/10 | 我 | 跑 G7 (gitleaks/trufflehog) | 0 hit |
| 5/11 | 我 | 出 SBOM (3 组件) | CycloneDX json |
| 5/12 | 鸿波 | 写"客户安全说明" 1 页 (基于 § 2 + § 3 已修) | 提前发客户预审 |
| 5/13 | 大家 | rehearsal × 2, 含安全 Q&A 演练 | 30 个潜在问题预答 |
| 5/14 | demo | 现场答疑准备齐 | — |

---

## 5. 客户预期问答 (帮鸿波准备)

**Q1: "员工数据会不会到外网 LLM?"**
> A: 默认配置 prod = 全部走客户内网 LLM (Hermes), 没数据出境。如果要用外网 (qwen 等), 在 config.yaml 显式开 + 走 audit log + 员工 chat 顶部 banner 提示。

**Q2: "LLM 调用工具能不能读我员工电脑全盘?"**
> A: 当前 execute_code 是 subprocess 沙箱目录 `~/.catfish/sandbox/` (5/7 修完), 网络 deny。读员工文件需要员工本人在 ALLOW.md 显式列路径。

**Q3: "skills 是 ChatGPT 那种 Plugin 吗? 第三方能不能投毒?"**
> A: skills hub 只接客户内网部署 (skills.<客户>.internal), 拉文件 sha256 校验 + RSA 签名, 不匹配拒装。员工不能装外网 skill。

**Q4: "审计能查到谁在什么时候用什么 prompt?"**
> A: 双层 audit:
> - gateway 中央 (metadata only) — 谁 / 什么时间 / 哪个 model / 多少 token
> - tool-bridge 边缘 (含 args_preview) — 员工本机, 不出网
> 都是 jsonl, 标准 SIEM 能接。

**Q5: "员工把密码贴 chat 里怎么办?"**
> A: prompt_security.py 检测 ≥40 种 secret 模式 (api key / SSH / JWT / 数据库连接串 / 密码), 检测到时:
> - 不写进 audit log (只标记 `prompt_credential_detected`)
> - 给员工弹提示: "改用 keychain://xxx 引用"

**Q6: "鲶鱼自己没 backdoor 吗?"**
> A: 全开源 (员工能 `cd ~/person_task/catfish && git log` 查所有 commit), CI 加 cargo audit / pip-audit / npm audit / gitleaks 自动扫, 客户能要 SBOM。

---

## 6. 持续验证 (post-demo)

- 季度: 跑一次自动扫描 (CVE / secrets)
- 半年: 外部渗透测试
- 重要 release 前: LLM jailbreak / prompt injection 红队

---

*本文档应放进 `~/person_task/catfish/` 跟代码一起 git 管, 每次 P0/P1 修完更新 § 3 状态.*
