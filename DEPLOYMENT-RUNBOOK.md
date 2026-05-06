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

## 12 · 部署后 checklist (上线前最后过)

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

全过 → 上线给员工。
