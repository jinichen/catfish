# 鲶鱼 · 部署 runbook (给客户 IT)

> 客户内网部署 gateway / skills-hub / 接 SSO. 全程 1-2 周, 含联调.
> 任何步卡了 → 鲶鱼平台团队 IT 联系人 24h 回复.

---

## 0 · 前置 (鲶鱼平台团队 + 客户 IT 联合确认)

部署前你们 IT 要把这 5 个东西给我们 (或者我们给你模板, 你填):

| # | 信息 | 例 | 谁填 |
|---|---|---|---|
| 1 | 内网 LLM URL (OpenAI-compatible) | `https://hermes.<客户>.internal/v1` | 客户 |
| 2 | 内网 LLM 模型清单 | `qwen-v3-5-122b-a10b`, `qwen-flash-30b-a3b` | 客户 |
| 3 | 客户 IdP OIDC discovery URL | `https://sso.<客户>.com/.well-known/openid-configuration` | 客户 |
| 4 | 部署目标机器 (gateway 跑哪) | `gateway-prod.<客户>.internal:8000` | 客户 |
| 5 | 是否需要 skills-hub | yes/no (跨员工 skill 共享时 yes) | 客户 |

---

## 1 · gateway 部署 (核心组件)

### 1.1 docker compose 方案 (推荐)

```bash
# 在 gateway-prod.<客户>.internal 上 (Linux x86_64 / ARM64)
git clone https://<内网 git>/catfish.git
cd catfish/central/llm-gateway

cp config/models.yaml.example config/models.yaml
cp config/quotas.yaml.example config/quotas.yaml
cp .env.example .env
```

编辑 `.env`:

```bash
# 必填
HOST=0.0.0.0                                    # 服务器部署绑所有网卡 (员工电脑跑别这么干!)
PORT=8000
INTERNAL_LLM_BASE_QWEN_MAIN=https://hermes.<客户>.internal/v1
INTERNAL_LLM_API_KEY=<你们 hermes 的 api key>

# OIDC (SSO)
OIDC_DISCOVERY_URL=https://sso.<客户>.com/.well-known/openid-configuration
OIDC_CLIENT_ID=catfish-prod
OIDC_AUDIENCE=catfish

# audit log 接 SIEM
CATFISH_AUDIT_PATH=/var/log/catfish/gateway_audit.jsonl

# Skills Hub URL (如果部署了 hub)
CATFISH_HUB_URL=http://skills-hub.<客户>.internal:8997
CATFISH_HUB_REQUIRE_HASH=1                      # 严格模式: skill 没 sha256 拒装

# DB (audit / quota / sessions 写哪)
DATABASE_URL=postgresql+asyncpg://catfish:<密码>@db.<客户>.internal:5432/catfish
```

编辑 `config/models.yaml`:

```yaml
default_provider: catfish-private-main
models:
  - name: catfish-private-main                  # 主力
    display_name: "qwen 122B (主)"
    upstream:
      provider: openai-compatible
      model: openai/qwen_v3_5_122b_a10b
      api_base: ${INTERNAL_LLM_BASE_QWEN_MAIN}
      timeout_s: 180
    use_cases: [chat, summarizer, proactive_starter, a2a_aux]

  - name: catfish-private-flash                 # 快速 fallback
    upstream:
      provider: openai-compatible
      model: openai/qwen_v3_5_flash_30b_a3b
      api_base: ${INTERNAL_LLM_BASE_QWEN_FLASH}
    use_cases: [summarizer, proactive_starter]

# 默认 prod = 内网闭环, 不启外网 fallback
fallback_chain: []
```

启动:

```bash
docker compose up -d
docker compose logs -f gateway
# 应该看到:
#   [catfish] starting uvicorn on 0.0.0.0:8000 ...
#   [catfish] alembic migration ok ...
```

健康检查:

```bash
curl http://localhost:8000/healthz
# {"ok":true,"version":"0.1.0"}
```

### 1.2 裸 systemd 方案 (没 docker)

```bash
cd /opt/catfish/llm-gateway
python3.12 -m venv venv && source venv/bin/activate
pip install -e .
pip install psycopg[binary] asyncpg

# alembic 跑迁移
alembic upgrade head
```

`/etc/systemd/system/catfish-gateway.service`:

```ini
[Unit]
Description=Catfish LLM Gateway
After=network.target postgresql.service

[Service]
Type=simple
User=catfish
WorkingDirectory=/opt/catfish/llm-gateway
EnvironmentFile=/opt/catfish/llm-gateway/.env
ExecStart=/opt/catfish/llm-gateway/venv/bin/python -m catfish_gateway
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now catfish-gateway
systemctl status catfish-gateway
```

---

## 2 · SSO (OIDC) 接客户 IdP

### 2.1 在客户 IdP 注册 catfish 应用

| 字段 | 值 |
|---|---|
| Client ID | `catfish-prod` |
| Client Type | confidential / public 都行 |
| Redirect URIs | `http://catfish-companion-app/oauth/callback` (Tauri deep link) |
| Scopes | `openid profile email` (鲶鱼只要这 3 个) |
| Token Endpoint Auth Method | client_secret_post 或 client_secret_basic |
| Signing Algorithm | RS256 |

把 client_id / client_secret / discovery URL 填回 gateway `.env`。

### 2.2 验证 SSO

```bash
# gateway log 应该看到 OIDC 配置加载
docker compose logs gateway | grep -i oidc
# 期望: "OIDCProvider: discovery loaded, jwks_uri=..., signing_alg=RS256"

# 员工首次登录:
# 员工电脑 Companion 点"用 SSO 登录" → 浏览器跳客户 IdP → 回调拿到 token
# gateway 验签 RS256 JWT → audit log 第一行
tail -f /var/log/catfish/gateway_audit.jsonl
# {"ts":"...","user_sub":"emp001","action":"login","model":null,"tokens":0}
```

---

## 3 · quota / 限速 (防滥用)

`config/quotas.yaml`:

```yaml
# 每个用户每模型每分钟上限
default:
  per_user_per_model_rpm: 60                    # 每分钟 60 次
  per_user_per_model_tpm: 100000                # 每分钟 10 万 token

overrides:
  # VIP 员工放宽
  emp_admin_*:
    per_user_per_model_rpm: 200
  # 大模型成本高, 限严格
  catfish-private-main:
    per_user_per_model_rpm: 30
```

超限 gateway 返 429 + audit 标记 `quota_exceeded`. 员工 chat 顶部 banner 提示"今日额度已满"。

---

## 4 · ALLOW.md (员工 RBAC)

每个员工本机 `~/.hermes/ALLOW.md`, 控制 LLM 能调哪些工具 / 读哪些路径:

```markdown
# 老李 ALLOW.md

## 工具
- catfish_browser_navigate: 允许 (任何内网 URL)
- catfish_browser_navigate (外网): 拒绝
- read_file: 仅允许 ~/Documents/work/ 下面
- catfish_skill: 全部允许

## 数据
- 客户清单 ~/Documents/work/clients/: 允许 LLM 读
- 财务报表 ~/Documents/finance/: 拒绝

## a2a (跨员工)
- 老李问小赵: 仅工作时段 (9:00-18:00)
```

模板从 [`docs/ALLOW.md.template`](./docs/ALLOW.md.template) 抄。

---

## 5 · skills-hub 部署 (可选)

跨员工共享 skill (eg "weekly-report" 全公司一份):

```bash
cd catfish/central/skills-hub

# 部署到客户内网 (类似 gateway):
docker compose up -d
# 默认 host=127.0.0.1, 服务器部署改 HOST=0.0.0.0
```

`.env`:

```bash
HOST=0.0.0.0
PORT=8997
CATFISH_HUB_ROOT=/var/lib/catfish-hub
HUB_ADMIN_TOKEN=<32 字节随机>                   # publish/delete 用
```

发布一个 skill:

```bash
curl -X POST http://hub.<客户>.internal:8997/publish \
  -H "Authorization: Bearer $HUB_ADMIN_TOKEN" \
  -F "namespace=department" \
  -F "files=@SKILL.md" \
  -F "files=@script.py"
```

员工电脑 tool-bridge 自动从 hub URL 拉 (sha256 校验). 详见 [Skills Hub 协议](./central/skills-hub/README.md).

---

## 6 · audit log 接 SIEM

gateway / tool-bridge 都写 jsonl (一行一事件). 标准 SIEM (Splunk / Elasticsearch / Loki) 直接吃:

```bash
# fluent-bit 配置示例
[INPUT]
  Name    tail
  Path    /var/log/catfish/*.jsonl
  Tag     catfish.*
  Parser  json

[OUTPUT]
  Name    es
  Match   catfish.*
  Host    elastic.<客户>.internal
  Port    9200
  Index   catfish
```

事件 schema (gateway):
```json
{
  "ts": "2026-05-14T09:30:00Z",
  "user_sub": "emp001",
  "model": "catfish-private-main",
  "tokens_in": 1234,
  "tokens_out": 567,
  "latency_ms": 890,
  "security_concern": null
}
```

字段含义见 [`central/llm-gateway/src/catfish_gateway/metrics.py`](./central/llm-gateway/src/catfish_gateway/metrics.py).

---

## 7 · 监控

### 7.1 健康检查

```bash
curl http://gateway.<客户>.internal:8000/healthz
curl http://hub.<客户>.internal:8997/healthz
```

### 7.2 Prometheus metrics

```bash
curl http://gateway:8000/metrics
# catfish_chat_requests_total{model="catfish-private-main"} 1234
# catfish_chat_latency_seconds_bucket{le="1"} 1100
# catfish_quota_exceeded_total{user="emp001"} 5
```

接 Prometheus + Grafana, 配模板见 `docs/grafana-dashboard.json`.

### 7.3 watchdog (子进程死活)

员工电脑 Companion 自带 watchdog: 每 5s 检查 gateway / tool-bridge 是不是还在, 死了 5s 内 respawn. 不需要 IT 管。

---

## 8 · 升级

```bash
# docker compose 方案
cd /opt/catfish
git fetch && git checkout v0.2.0
docker compose pull && docker compose up -d
docker compose logs -f gateway     # 看 alembic 跑没跑迁移

# systemd 方案
cd /opt/catfish/llm-gateway
git pull && pip install -e .
alembic upgrade head
systemctl restart catfish-gateway
```

升级前看 changelog: [`CHANGELOG.md`](./CHANGELOG.md). 大版本 (1.x → 2.x) 我们会提前 1 个月通知。

---

## 9 · 排错 checklist

### 员工反馈 chat 没响应

1. `curl http://gateway:8000/healthz` 返 ok 吗?
2. `tail /var/log/catfish/gateway_audit.jsonl` 看那个员工最近一次调用记录, 找 latency / error
3. 看 gateway 容器 / systemd log: `docker compose logs gateway --tail 100` 找 ERROR

### 员工反馈 SSO 登录失败

1. gateway log 看 OIDC 错: `grep -i oidc /var/log/catfish/...`
2. 客户 IdP 的 redirect URI 跟 Companion 配一致吗? (`tauri://localhost/oauth/callback` 或 deep link)
3. 客户 IdP 的 token signing algorithm 是 RS256 吗? (HS256 不支持)

### 员工反馈工具调用失败 (skill 跑不起来)

1. tool-bridge 在员工本机, 看员工 `~/Library/Logs/catfish/tool-bridge.log`
2. skill 装在 `~/.catfish/skills/`, 缺的话 hub 上有? 装一下: `catfish_skill_install hub_skill="department/weekly-report@latest"`
3. 跑 skill_lifecycle 测试 验证: `cd edge/tool-bridge && pytest tests/test_skill_lifecycle.py`

### gateway / hub 部署后端口扫不到

1. `HOST=0.0.0.0` 设了吗 (默认是 127.0.0.1, 客户服务器要显式开)
2. 防火墙允许 8000 / 8997 内网入站吗?
3. 内网 DNS `gateway.<客户>.internal` 解析对吗?

---

## 10 · 关键路径速查

| 路径 | 干啥 | 谁在乎 |
|---|---|---|
| `/var/log/catfish/gateway_audit.jsonl` | gateway audit log | 信安 / IT |
| `/var/log/catfish/tool-bridge.log` | tool-bridge 运行 log | IT |
| `~/.catfish/sessions/*.sqlite` | 员工 chat 历史 | 员工本机 |
| `~/.catfish/uploads/<ts>-<name>` | 员工上传文件原件 | 员工本机 |
| `~/.catfish/output/<日期>/<topic>/` | 鲶鱼生成的报告 | 员工本机 |
| `~/.hermes/USER.md` | 员工身份信息 | 员工本机 |
| `~/.hermes/ALLOW.md` | 员工 RBAC | 员工本机 |
| `~/.hermes/state.db` | hermes 内部 sessions | 员工本机 |
| `gateway/.env` | gateway 配置 | IT (服务器) |
| `gateway/config/models.yaml` | LLM 模型清单 | IT (服务器) |
| `gateway/config/quotas.yaml` | 限速规则 | IT (服务器) |

---

## 11 · 选哪种 Plan D 架构 (跨员工协同部署)

> 5/6 加, 鸿波点播. 协议层支持 3 种部署 (详见 [`docs/PLAN-D-PROTOCOL.md` § 9.5](./docs/PLAN-D-PROTOCOL.md)).
> 客户 IT 按本表选, demo 后 PoC 期间联系鲶鱼平台团队对接.

### 决策表 (5/6 晚 最终定位 — A 是 default)

> 鲶鱼定位: **员工的职业资产, 公司给员工配的生产力工具**. 默认 A 架构 (员工本机), 让员工拥有数据 + 跨雇主可携带.
> B/C 是**公司选择集中控制**时的部署选项, 不是 default 推荐.

| 你的场景 | 选 | 理由 |
|---|---|---|
| **绝大部分公司 — 给员工配生产力工具** | **★ A 员工本机 (default)** | 员工拥有数据, 跳槽带走, 公司给员工配 ThinkPad 模式 |
| 公司想集中管理员工 chat (传统监管思路) | B 部门 gateway | 部门 IT 集中审计 (但跟鲶鱼"员工拥有"灵魂相反) |
| 大集团统一 audit 平台 | C 公司单点 | 全公司 SIEM 接口, 跟鲶鱼定位最远 |
| 混合: 高敏感岗 (财务/战略) 走 B 减小集中信任面 + 普通员工走 A | A + B 混合 | 平衡员工拥有 vs 公司管控 |

**关键认知**: B/C 把鲶鱼降级成"ChatGPT 企业版式"集中工具, 失去鲶鱼"员工拥有数据 / 跨雇主可携带"的灵魂优势. 客户 IT 想集中管的话, 直接买 ChatGPT 企业版可能更对路 — 鲶鱼是给**信任员工的公司**用的.

### 部署对照

| | 架构 A 本机 gateway | 架构 B 部门 gateway | 架构 C 公司单 gateway |
|---|---|---|---|
| **gateway 跑哪** | 每员工 macOS | 部门内网 1 台 Linux | 公司 IDC 1 台 Linux |
| **资源** | 100 员工 = 100 个 gateway | 1 部门 = 1 gateway | 全公司 = 1 gateway |
| **配置文件数** | 100 份 .env | 1 份 .env | 1 份 .env |
| **Companion 跑啥** | 全套 (含 gateway) | 只 Companion + tool-bridge (轻) | 同 B |
| **员工 chat 出本机** | ❌ 不出 (除上游 LLM) | ✅ 出 (HTTPS 到部门 gateway) | ✅ 出 (HTTPS 到公司 gateway) |
| **a2a 流量谁能看** | 自己的 gateway | 本部门 gateway | 公司 gateway |
| **audit 落哪** | 员工本机 jsonl | 部门 gateway jsonl | 公司 gateway jsonl |
| **单点故障影响范围** | 1 员工 | 1 部门 (跨部门 a2a 不动) | 全公司 (所有 chat 全挂) |
| **升级窗口** | 100 台轮 (慢) | 1 台 5 分钟 | 1 台 5 分钟 (但风险高) |

### 切换路径 (协议向后兼容)

**A → B (从本机迁部门 gateway)**:
1. 部门内网起 1 个 gateway (用 § 1 流程)
2. `users.yaml` 加这部门所有员工 sub + keypair (从员工本机 `~/.catfish/keypair.json` 拷)
3. catfish-identity registry 改员工 endpoint 指向部门 gateway
4. 员工 Companion 改 `CATFISH_GATEWAY_URL=https://gateway.dept.<客户>.internal:8000`
5. 关员工本机 gateway, 跑通后删

**B → C** (部门合并到公司单点) 同流程, registry 改 endpoint 指向公司 gateway。

**协议层不动** — 同一份代码, 切配置就行, 0 停机。

### 我们 demo 怎么答客户

> "Plan D federation 协议层支持员工本机 / 部门共享 / 公司单点 3 种部署. 高敏感员工跑本机 gateway, 普通员工部门共享, 大集团过渡期先单点, 客户 IT 按敏感度自己分. 都走同一份代码 (`gateway/users.yaml` 多 user_sub 配置), 任何时候能切换不停机."

### 5/14 demo 演示策略

单机 mock 演 — demo 机子起 2 个 gateway (port 8998 + 8999) 模拟 Alice + Bob, 跑通 a2a_ask. 客户问"100 员工怎么办" → 翻这页给他看 3 种架构。

---

## 12 · 联系 (出问题第一步)

- **gateway / hub 部署问题**: 鲶鱼平台 IT 联系人 (鸿波)
- **SSO 集成问题**: 客户 IdP 团队 + 鲶鱼平台 IT 联合 debug
- **员工本机问题**: 员工自己看 [`QUICKSTART-EMPLOYEE.md`](./QUICKSTART-EMPLOYEE.md) § 5 排错表
- **安全 / 漏洞**: 走 GitHub Security Advisory, SLA 24h triage / 7d fix
- **CVE 紧急 patch**: [SECURITY-REVIEW.md](./SECURITY-REVIEW-2026-05-06.md) 联系方式

---

## 13 · Hermes service token 配置 (BL-HERMES-SERVICE-TOKEN, 5/24)

### 13.1 为啥要这个

hermes daemon 调 catfish-gateway 历史用员工 OAuth access_token (1h TTL). hermes 启动时抓一份进内存，**不会自动续期**。一小时后 token 过期 → gateway 验签拒 → 401 storm → 必须手动 `hermes gateway restart` 才恢复。

修法: 给 hermes 一个**服务身份** (sub=`client:hermes-cli`, 30 天 TTL) 的 token, OAuth 2.0 client_credentials grant 拿。跟员工 OAuth 完全解耦, 用户没登录 hermes 也照常跑。

### 13.2 部署步骤

**生产**：

1. **生成 client_secret** (16-32 字节随机)：
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **更新 catfish-identity** `config/clients.yaml`：用 bcrypt hash 这个 secret 替换 hermes-cli 段的 `client_secret_hash`。重启 identity-server 生效。

3. **员工电脑 / 部署机** 设 env：
   ```bash
   export CATFISH_HERMES_CLI_SECRET="YOUR_NEW_SECRET"  # 加到 ~/.zshrc 或 launchd plist
   ```

4. **触发 mint** (任选其一)：
   ```bash
   catfish login              # 员工登录顺手 mint (推荐路径)
   catfish refresh            # 续 user OAuth 顺手 mint
   catfish refresh-hermes     # 独立 mint, 不需要员工登录态 (cron 用)
   ```

5. **重启 hermes 读新 token**：
   ```bash
   hermes gateway restart
   ```

**Dev**：不需要任何配置。`CATFISH_HERMES_CLI_SECRET` 不 set 时用 demo secret (`hermes-dev-secret-2026-please-change`)，跟 `identity-server/config/clients.yaml` 里 demo hash 已经配对。直接 `catfish login` 就 work。

### 13.3 30 天后续期

| 方案 | 操作 | 适合 |
|---|---|---|
| **手动** | 30 天前跑 `catfish refresh-hermes && hermes gateway restart` | 小团队 |
| **launchd cron** | 每月 1 号 4am 自动跑（plist 见下） | **推荐**, 零运维 |
| **Companion 自动检查** | 后续可在 Companion 启动时检查 exp，剩 <7d 自动 mint | 未来 |

launchd plist 模板：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.catfish.hermes-service-token-refresh</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string><string>-lc</string>
        <string>catfish refresh-hermes &amp;&amp; hermes gateway restart</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Day</key><integer>1</integer><key>Hour</key><integer>4</integer></dict>
    <key>StandardOutPath</key><string>/tmp/catfish-hermes-refresh.log</string>
    <key>StandardErrorPath</key><string>/tmp/catfish-hermes-refresh.err</string>
</dict>
</plist>
```

放 `~/Library/LaunchAgents/com.catfish.hermes-service-token-refresh.plist`，跑 `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.catfish.hermes-service-token-refresh.plist`。

### 13.4 验证

```bash
catfish refresh-hermes
# 期望:
#   hermes-svc: ✓ 已 mint service token (sub=client:hermes-cli, 还有 30.0 天有效)
#   ✓ hermes config 已更新 provider 'Local (localhost:8999)'

# 解码 hermes config 里 api_key 验证:
python3 <<'PY'
import json, base64, yaml, os, time
cfg = yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')).read())
tok = cfg['model']['api_key']
payload = tok.split('.')[1]; payload += '=' * (-len(payload) % 4)
c = json.loads(base64.urlsafe_b64decode(payload))
print('sub:', c.get('sub'))   # 应该 'client:hermes-cli'
print('aud:', c.get('aud'))   # 应该 'catfish-gateway'
print('exp 后(天):', (c['exp'] - time.time()) / 86400)  # ~30
PY
```

---

## 15 · 外部工具 key 中央派发 (BL-EDGE-TOOL-KEY, 5/24 ship)

### 15.1 干啥用

hermes 边缘工具 (`web_search` / `web_extract` / `web_crawl` / ...) 依赖第三方
backend (Tavily / Firecrawl / ...) 的 API key. 这块 key 的派发哲学:

> "所有第三方 API key 中央 admin 管理, 50 台员工机器不直接持有 key 来源, 改 key 不用 ssh 全跑一遍."

实现 = catfish-gateway 持 key + per-tool RBAC + endpoint 派发 → catfish-cli
拉 + 写员工 `~/.hermes/.env` + `~/.hermes/config.yaml`.

### 15.2 数据流

```
admin (改这里, 改完 reload gateway)
   │
   ▼
central/llm-gateway/.env              ← admin 改 TAVILY_API_KEY=tvly-...
   │
   ▼
gateway GET /v1/edge/tool-config/{tool_name}
   ├─ JWT 验 (service token 或 user token 都行)
   ├─ RBAC: user.can_use_tool(tool_name) ← effective_allowed_tools claim
   └─ 200 {tool_group, env_vars, yaml_block}
   │
   ▼
catfish login / catfish refresh-hermes  ← 员工跑 (或 launchd 月跑)
   │
   ▼
~/.hermes/.env       ← per-key update (TAVILY_API_KEY=tvly-... + 旁边的行不动)
~/.hermes/config.yaml ← 合并写 web.backend: tavily (旁边 sibling key 不动)
   │
   ▼
hermes gateway restart  ← hermes 重 load .env, web_search 真的能用了
```

### 15.3 配置 (admin 部署时)

**Step 1 — 申请 key**

- Tavily (推荐, 国内通): https://app.tavily.com — 1000 搜索/月免费
- Firecrawl: https://firecrawl.dev — 500 credits/月免费 (国内访问偶尔慢)
- 其他备选见 https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search

**Step 2 — 写 gateway .env**

```bash
# central/llm-gateway/.env (生产是 systemd EnvironmentFile / k8s Secret)
TAVILY_API_KEY=tvly-生产真 key
```

**Step 3 — Reload gateway**

```bash
systemctl reload catfish-gateway          # 或
docker compose restart catfish-gateway    # 或
hermes gateway restart                    # dev 单机
```

**Step 4 — 跑 alembic migration (一次性)**

```bash
cd central/identity-server
alembic upgrade head
# 应跑到 20260524_008_rbac_edge_web_tools, 给所有部门 allowed_tools
# 加 web_search + web_extract
```

注: engineering / ops 部门 `allowed_tools = []` (全允许), migration 不动它们.
sales / legal 之前是显式收紧 list, 这次把 web 工具显式追加进去.

### 15.4 员工侧 (员工跑一次, 之后自动)

```bash
catfish login            # 或 catfish refresh-hermes 单独 refresh hermes
```

输出应该看到:

```
  hermes-svc: ✓ 已 mint service token (sub=client:hermes-cli, 还有 30 天有效)
  hermes:   ✓ 已更新 ~/.hermes/config.yaml provider 'Local (localhost:8999)'
  edge-tool: ✓ 同步 1 个 env key + 1 个 yaml 段 (~/.hermes/.env, ~/.hermes/config.yaml)
```

`edge-tool` 那行 = 中央派发生效。**之后还要**:

```bash
hermes gateway restart   # hermes 重 load .env, 不然进程内缓存还是旧的 (或空)
```

### 15.5 验证 (端到端)

```bash
# 1. 看 .env 真有 key
cat ~/.hermes/.env | grep TAVILY

# 2. 看 yaml 真有 web.backend
grep -A 2 "^web:" ~/.hermes/config.yaml

# 3. 真发一个搜索, 看 hermes emit web_search tool_call + 真返结果
echo "搜一下今天上海新闻头条" | hermes  # 或 Companion 新会话

# 4. (admin) 看 gateway audit 有 user.X 发起的 /v1/edge/tool-config/web_search 请求
```

### 15.6 RBAC 操作 (admin)

**关某部门的 web 搜索权限** (例如法务 demo 后想恢复 005 的"合规不外联"):

```sql
UPDATE departments
SET allowed_tools = allowed_tools - 'web_search' - 'web_extract'
WHERE name = 'legal';
```

或走 admin UI 部门编辑器 (/admin → 部门 → 法务 → allowed_tools 去掉 web_search/web_extract → 保存).

**给某员工单独开** (即使部门没批):

```sql
UPDATE users
SET allowed_tools = COALESCE(allowed_tools, '[]'::jsonb) || '["web_search"]'::jsonb
WHERE email = 'special-user@x.com';
```

### 15.7 死代理自动清理 (BL-EDGE-TOOL-PROXY, 5/25)

**问题**: 员工 shell 普遍配 `HTTPS_PROXY=http://127.0.0.1:7890` 指向 Clash / Mihomo, 但代理常没开。`hermes gateway restart` 起的 hermes 进程**继承**这条死代理 → Tavily / Firecrawl 等外网 HTTP 全 timeout → 模型学会"web_search 不能用", 退化到 `catfish_browser_*` 抓页面 (慢 30 倍 + 贵 30 倍 token)。

**修法**: `catfish refresh-hermes` 默认在末尾**自动检测**当前 shell 的 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY (含小写版) 的 TCP 可达性, 死了 → 警告 + 给手动 unset 命令。加 `--restart-hermes` flag 后, 直接帮员工用 sanitized env 拉 `hermes gateway restart`, 不需要手动 unset。

```bash
# 默认: 只警告
catfish refresh-hermes
# 输出 (有死代理时):
#   ⚠ 检测到死代理 (TCP 端口连不上):
#       HTTPS_PROXY=http://127.0.0.1:7890
#     hermes 进程继承这条会让 web_search / Firecrawl 等外网工具撞墙 timeout.
#     → 推荐用这两条手动重启 (跳过死代理):
#       unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy
#       hermes gateway restart
#     → 或下次 `catfish refresh-hermes --restart-hermes` 自动帮你做.

# 自动: refresh + restart, 一条命令搞完
catfish refresh-hermes --restart-hermes
# 输出:
#   ✓ hermes 已用 clean env 重启, web_search 等工具应可正常调
```

**注**: `--restart-hermes` 只在**检测到死代理时**才真起 restart, 没死代理就不动 hermes (员工自己想啥时重启就啥时)。

**cron 推荐**: 30 天 token 续期任务直接 `catfish refresh-hermes --restart-hermes`, 死代理 + 续 token 一锅炖。

### 15.8 故障排查

| 症状 | 原因 | 修法 |
|------|------|------|
| `edge-tool: ⏭ web_search — gateway HTTP 503` | gateway `TAVILY_API_KEY` env 没配 | admin 在 gateway .env 加 + reload |
| `edge-tool: ⏭ web_search — 部门 RBAC 没批` | identity-server 部门 allowed_tools 不含 | 跑 008 migration 或 admin UI 加 |
| `edge-tool: ⏭ web_search — gateway HTTP 401` | hermes-cli service token 没 mint / 已 revoke | `catfish refresh-hermes` 重 mint |
| hermes 重启后还是说 "我没工具" | 重启前 .env 没改完 | 检查 `~/.hermes/.env` 真有 `TAVILY_API_KEY=tvly-...`, 然后 hermes gateway restart |
| `~/.hermes/config.yaml` 文件不存在 → 跳过 yaml 同步 | 员工没跑 `hermes model` setup | 跑一次 `hermes model` 配 Custom endpoint, 然后再 `catfish refresh-hermes` |
| **模型说"搜索断了" 退到 browser 抓页面** | hermes 进程继承死 HTTPS_PROXY | `catfish refresh-hermes --restart-hermes` (§15.7) |

### 15.9 后续 sprint 加新 tool 流程

(给未来加 `image_generate` / `x_search` 等用)

1. gateway `.env` 加新 key (e.g. `STABILITY_API_KEY`)
2. `central/llm-gateway/src/catfish_gateway/edge_tool_config.py` `EDGE_TOOL_REGISTRY` 加一行
3. `central/identity-server/alembic/versions/` 加新 migration seed 部门 RBAC
4. CLI **不用改** — 它自动从 `/v1/edge/tool-config` list 拿支持的 tool 名

---

## 14 · 部署后 checklist (上线前最后过)

- [ ] gateway 健康 (`curl /healthz` ok)
- [ ] hub 健康 (如果部署了)
- [ ] 1 个测试员工 SSO 登录成功 + chat 收到响应
- [ ] gateway_audit.jsonl 有第一条记录
- [ ] quota 配置生效 (尝试触发限速看 429 + audit 标记)
- [ ] 上传 1 个 PDF + 1 个 Excel 验证 parse_file
- [ ] catfish_remember 能在新会话引用历史
- [ ] 桌宠主动闲聊 (员工 9:30/14:00/17:30 收到, 或 Dashboard "测一下 ▶" 验证)
- [ ] SIEM 接收 gateway audit 流量正常
- [ ] Prometheus / Grafana 看到 metrics
- [ ] 监控告警配好 (gateway 挂了通知谁 / quota 大量超限通知谁)
- [ ] 备份策略: gateway DB 每天备份 + audit log 30 天保留
- [ ] 灾难恢复演练 1 次 (gateway 服务器宕机, 多久恢复)
- [ ] **hermes service token 已 mint** (`catfish refresh-hermes` 验证 sub=client:hermes-cli)
- [ ] **30 天续期机制配好** (launchd / cron / 员工手动复诊)
- [ ] **生产 `CATFISH_HERMES_CLI_SECRET` env 已设**（非 demo secret）
- [ ] **§15: gateway `TAVILY_API_KEY` env 已配** (admin 改这个 50 人同步) — 跑 `curl -H "Authorization: Bearer <token>" http://gw:8999/v1/edge/tool-config/web_search` 返 200 + env_vars 含 key
- [ ] **§15: 008 migration 已跑** (`alembic upgrade head` → 部门 `allowed_tools` 包含 web_search/web_extract)
- [ ] **§15: 一个测试员工** `catfish refresh-hermes` 后 `cat ~/.hermes/.env` 真有 TAVILY_API_KEY + web_search demo 返真实结果

全过 → 上线给员工。
