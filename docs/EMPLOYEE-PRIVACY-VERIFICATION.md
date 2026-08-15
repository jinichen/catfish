# 员工隐私自查指南

> 给员工的"我不需要纯信任公司说辞, 我能自己验证"实操手册.
>
> 配套交付物 (5/25 一起 ship):
> - #76 `catfish privacy-audit` CLI — 终端一键扫
> - #77 Companion → 仪表盘 → 🔒 隐私状态 卡 — UI 实时看
> - #79 gateway `GET /api/audit/me` — 中央侧员工自查 API
> - #78 本文档 — 把上面 3 个串起来, 解释看什么 / 异常怎么报

---

## TL;DR (30 秒看完版)

| 数据 | 在哪 | 谁能看 | 上不上传 |
|---|---|---|---|
| 你跟鲶鱼的对话 | `~/.hermes/sessions/` (你的电脑) | 只有你 (跟登录账号的本地权限) | ❌ 不上传 |
| 鲶鱼对你的长期记忆 | `~/.hermes/memories/USER.md` etc. | 只有你 | ❌ 不上传 |
| 第三方 API key (Tavily 等) | `~/.hermes/.env` | 只有你 | ❌ 不上传 |
| 你的 OAuth token | `~/.catfish/auth/token.json` (chmod 600) | 只有你 (Unix 文件权限) | 仅 token 本身用于中央认证, 不含对话 |
| 中央调用流水 (metadata) | 中央 PG `gateway_audit` 表 (5/9 前镜像在 `~/.catfish/gateway_audit.jsonl`, 后切 PG-only) | 你 + 你部门 manager + admin | metadata 上传 (无 prompt/response 文本) |
| 中央存的你的 metadata | catfish 中央 PG/sqlite | 你 + 你部门 manager + admin | ✅ 上传 (按下面表) |

**中央存了你啥** (跑 `catfish privacy-audit` 或开 Companion → 仪表盘 → 隐私状态 看实时):

- 请求计数 (今日 / 历史)
- 总 token 数 (in + out)
- 模型分布 (你用了 deepseek-flash 多少次 / claude-sonnet 多少次)
- 时间戳 (每次请求的 ts)
- user_email + department (绑用户身份)

**中央不存你啥**:

- prompt 文本 (你输入给鲶鱼的内容)
- response 文本 (鲶鱼回你的内容)
- 你本机的对话历史 / 长期记忆 / API key
- 第三方 tool 调用的参数 / 返回 (e.g. 你搜了什么)

这条契约写在 gateway 代码里 (`catfish_gateway/quota.py::audit_summary_user_since` docstring +
`/api/audit/me` schema_note 字段). 跑 `catfish privacy-audit --json | jq .central.data.schema_note`
能直接读到中央自己声明的契约.

---

## 怎么自查 (3 种入口)

### 入口 1 · 命令行 (最完整)

```bash
catfish privacy-audit
```

输出 3 段报告:

- **段 1 · 本机数据**: 列你电脑上跟 catfish 相关的所有路径, 标 size / 权限位 / 最近改动
  时间, 标"敏感"(🔒)和"非敏感"(📄). token 文件权限不是 600 会标 ⚠ 警告
- **段 2 · 本机 audit log**: 你电脑上 `~/.catfish/gateway_audit.jsonl` 的统计
  (今日按模型 / 全量时间范围). 这份是边缘 gateway 写的, 跟中央存的内容**应该一致**
- **段 3 · 中央存了我啥**: 调 `/api/audit/me` 拉中央实时数字 (请求数 / token / 模型 /
  最早最新记录)

最后输出"隐私契约"4 条 — 这些是判定依据, 不是宣传话.

**离线模式**: 没登录 / 中央挂时, 段 3 会标"未连通", 段 1 和段 2 照常输出. 离线员工
也能审本机数据.

**JSON 模式** (CI / 给合规脚本看):

```bash
catfish privacy-audit --json > my-privacy.json
```

JSON schema 见命令源码 `edge/catfish-cli/catfish_privacy.py::cmd_privacy_audit`.

### 入口 2 · Companion UI

打开 Companion → 仪表盘 → 🔒 **隐私状态** 卡:

- 区 1: 本机数据清单 (路径 + 是什么 + 上不上传, 表格)
- 区 2: 中央 metadata 实时拉 (今日请求 / token / 最早最新记录 / schema 契约提示)
- 区 3: 进阶入口 (跑 CLI / 打开本 doc)

UI 上故意**不显示** prompt 内容 / token 字符串 / API key — UI 一旦显示, 截屏就漏.
要看具体内容自己进文件夹.

### 入口 3 · 直接调 API

```bash
# 拿你的 access_token
TOKEN=$(catfish token)

# 调 /api/audit/me (默认 gateway: localhost:8999, 改环境变量 CATFISH_GATEWAY_URL)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8999/api/audit/me | jq
```

返:

```json
{
  "user_email": "you@company.com",
  "department": "eng",
  "since_ms": 1779660800000,
  "schema_note": "本端点只返 metadata: count / tokens / model / 时间戳. 中央不存 prompt / response 文本.",
  "request_count": 42,
  "total_tokens": 81234,
  "by_model": [
    {"model": "deepseek-v4-flash", "count": 38, "total_tokens": 75123},
    {"model": "anthropic/claude-sonnet-4-7", "count": 4, "total_tokens": 6111}
  ],
  "first_seen_ts": 1779001234000,
  "last_seen_ts": 1779660795000
}
```

字段就这些. 多一个少一个都是 bug / 违约 → 直接报上来.

---

## 每个数据项详解

### 你的对话 (`~/.hermes/sessions/`)

hermes 是底层 agent runtime, catfish 跑在它上面. 你每次跟"小鲶"说话, prompt +
response 全文写到 `~/.hermes/sessions/<session-id>.jsonl`. 这个文件**完全本地**,
hermes 不上传, catfish 不读 (catfish 只通过 gateway 调 LLM, 拿 metadata).

想确认? 跑 `lsof -i -p $(pgrep hermes)` 看 hermes 进程的 socket — 应该只有 LLM
gateway 连接, 没往别处写流量.

清空: `rm -rf ~/.hermes/sessions/*` (但你会丢历史对话, 想想清楚)

### 鲶鱼对你的长期记忆 (`~/.hermes/memories/`)

`USER.md` + `MEMORY.md` 是 hermes 0.12+ 的长期记忆机制, 由模型自己决定写什么.
内容是模型对你的认知 (你叫啥 / 偏好 / 上下文等). 跟对话一样**完全本地**.

UI 入口: 仪表盘 → 鲶鱼对你的认识 → HermesMemoryCard

清空: `rm ~/.hermes/memories/*.md` (鲶鱼会重新"认识"你, 不影响对话功能)

### 第三方 API key (`~/.hermes/.env`)

你自己配的 Tavily / 等 key, 用于鲶鱼调网络工具. **完全本地**, 不上传中央.

权限应该是 600. 检查: `stat -f '%Lp' ~/.hermes/.env` (macOS) — 输出应该是 `600`
而不是 `644`.

### 你的 OAuth token (`~/.catfish/auth/token.json`)

`catfish login` 拿到的中央认证 token. **不是对话**, 只是身份凭证.

权限**必须** 600 (CLI 写盘时自动 chmod). 检查: `stat -f '%Lp' ~/.catfish/auth/token.json`
应该是 `600`. 不是 600 → `catfish privacy-audit` 会标 ⚠.

清空 (= 登出): `catfish logout`

### 本机 audit log (`~/.catfish/gateway_audit.jsonl`) — 5/9 后历史归档

**5/9 之前**: 边缘 gateway 写一份镜像到本机 jsonl, 跟中央应该对得上.

**5/9 之后**: gateway 配了 `CATFISH_DB_URL` 切 **PG-only** backend (`metrics.py:79`),
metrics 直接写中央 PG, 不再镜像本机. 老 jsonl 文件保留为历史归档, 不再更新.

**当前唯一真相** = 中央 PG (`gateway_audit` 表), 通过 `/api/audit/me` 暴露员工自己的部分.

`catfish privacy-audit` 检测到本机 jsonl latest_ts < 中央 first_seen_ts 会自动
标 "PG-only 模式", 不再跑"差 N 条"对照 (那个对照在 PG-only 时代没意义).

清空: `rm ~/.catfish/gateway_audit.jsonl` (历史归档, 删了不影响任何功能 — 想留作
5/9 前的本地审计副本就别删)

---

## 中央存了你啥 (`/api/audit/me` 详细解读)

每个字段为什么存:

- **`request_count` / `total_tokens`**: 计费 + quota 控制. admin 要知道全员用量
  能不能撑住下个月 LLM 账单
- **`by_model`**: 容量规划. 哪些模型用得多就扩容哪些
- **`first_seen_ts` / `last_seen_ts`**: 用户生命周期. 30 天没活动 → admin 清离职员工
- **`user_email`**: 多租户隔离. 没这个 quota 算到部门级也不知道分摊给谁
- **`department`**: 部门审计 (manager 看本部门). 没这个 manager 没法做容量规划

**为什么不存 prompt / response**:

1. **隐私底线**: 员工跟鲶鱼说话有 sensitive 内容 (合同 / 简历 / 个人事), 中央存
   = 公司管理员能看 = 员工不敢用
2. **存储成本**: 50 人每天 1k 请求, 每 prompt 平均 500 字, 一年 ~36GB 纯文本.
   中央 PG/sqlite 撑不住, 也没价值
3. **法规**: GDPR / 国内个保法都要求"非必要不收集". 计费 / quota 不需要 prompt 内容

---

## 常见问题

### Q: 我跑 privacy-audit 中央段挂了, 是不是有问题?

不一定. 看 `--reason` 字段:

- "未登录" → 跑 `catfish login`
- "token 过期" → CLI 会自动 refresh, 失败再重 login
- "连不上 gateway" → 中央或网络问题, 不是你的隐私问题
- HTTP 5xx → 中央 bug, 报给运维 (不会泄漏你的隐私 — 5xx = 中央根本没返数据)

### Q: 中央 `/api/audit/me` 返的数字跟我本机 audit jsonl 对不上, 怎么办?

`catfish privacy-audit` 自动对照, 差 ≤ 5 条算正常 (race condition). 差超过 5 条:

1. 先看本机 jsonl 是不是被 truncate 了 / 滚动备份了 (查文件大小 / 改动时间)
2. 再看 gateway 日志 (`~/.catfish/gateway.log`) 有没有写盘错误
3. 都正常的话报给运维 — 可能中央有 quota 重复计费 bug

### Q: 我能让中央删掉我的历史 metadata 吗?

走 catfish-web `/admin` 提工单. 员工自己改不了 (防恶意员工删自己用量逃 quota).
admin 收到工单后用 `central/llm-gateway` 的 admin SQL 工具操作.

如果你只是想暂停被记录, 不想用 catfish 了 — `catfish logout` 就行 (没 token 中央就
没有你的新记录, 历史保留按公司数据保留政策处理).

### Q: catfish-web 还有别的 admin / manager 端点能看到我的对话内容吗?

**没有**. 全套 admin 端点列表:

- `/api/audit/global` (admin only) — 全员 metadata 聚合, 不含对话
- `/api/audit/department/{dept}` (manager / admin) — 部门级 metadata 聚合
- `/api/audit/me` (任何登录员工) — 自己的 metadata

跑 `grep -rn "prompt\|response\|content" central/llm-gateway/src/catfish_gateway/quota.py`
应该返**零**结果 (`quota_events` 表里就没这俩列). 这是物理隔离, 不是策略隔离.

### Q: hermes 进程会偷偷把我的对话上传给中央吗?

不会. 验证:

1. 抓 hermes 出站流量: `sudo lsof -i -p $(pgrep hermes)` 看连了哪些 IP / 端口
2. 应该只有: LLM 推理端点 (e.g. 中央 gateway 的 `/v1/chat/completions`) +
   你配的第三方 tool (Tavily 等)
3. 中央 gateway 转发到 LLM 推理时, 走的是匿名化的请求 (没员工 email / 部门), audit
   只在 gateway 入口写, 不带到 LLM 端

### Q: 我担心我的对话被同事偷看 (同机器多用户登录)

`~/.hermes/sessions/` 默认 macOS 用户目录权限 (`750`, 同组可读). 想锁:

```bash
chmod -R 700 ~/.hermes/sessions/
chmod -R 700 ~/.hermes/memories/
chmod 600 ~/.hermes/.env
chmod 600 ~/.catfish/auth/token.json
```

`catfish privacy-audit` 会检查 token.json 的权限, 但不检查 sessions/ — 那个交给
你自己决定.

---

## 报隐私漏洞

发现:

- privacy-audit 输出跟中央实际不一致 (差 > 5 条且复现)
- `/api/audit/me` 返了 schema_note 里没声明的字段
- 中央对其他员工的 `/api/audit/me` 你能看到 (横向越权)
- hermes 出站流量到了 schema_note + LLM endpoint 之外的 IP

**怎么报**: 邮件给 security@company.com, 标题 `[PRIVACY] catfish ...`, 附:

- `catfish privacy-audit --json` 输出
- 网络抓包 / lsof 截图
- 怎么复现 (3-5 步)

不报到 GitHub issue / 群里 — 隐私漏洞走专项流程.

---

## 改动历史

- 2026-05-25 (#76/#77/#78/#79 同批 ship): 4 件一次性做完, 透明性卖点正式兑现.
  之前散在代码里 "中央只看 metadata" 这条契约第一次有了**员工能自验**的工具.

## 关联

- `edge/catfish-cli/catfish_privacy.py::cmd_privacy_audit` — CLI 实现
- `edge/companion-app/src/tabs/Dashboard/PrivacyCard.tsx` — UI 实现
- `central/llm-gateway/src/catfish_gateway/app.py::api_audit_me` — gateway 端点
- `central/llm-gateway/src/catfish_gateway/quota.py::audit_summary_user_since` — 数据层
- `docs/DEPLOYMENT-50-USERS.md` §8 安全与合规 — 部署级隐私章节
- `docs/SECURITY-REVIEW-2026-05-06.md` — 历史安全审计
