# 分享治理：技能 / 知识 / 专家 / MCP 一套范围、一套审批、审批人能先试

2026-11-08 鸿波：「SKILL、知识库、专家、MCP 分享应该有部门 / 公司分享，需要审批，应支持审批人测试。」本文是方案，还没做。

## 0. 一句话

四样东西现在四种做法，统一成：**三档范围（个人 / 部门 / 公司）+ 按风险分两级审（会执行的必审并先试，纯文字的发了再审）+ 审批人「装到我这里试用」+ 通过即签名、装机验签、全程可追溯。** 公司级不双签。

## 1. 现状（11/08 核过代码）

| | 范围 | 审批 | 审批人试用 | 签名 / 验签 | 撤回 |
|---|---|---|---|---|---|
| 专家包 (skills-hub `/experts`) | 部门 / 公司 | 有：提交 → 待审 → 部门主管 (公司由 admin) 通过 / 驳回；审核摘要由服务器从包里拆 | 无，只看摘要 | 有 (Ed25519，装机验签) | 有 |
| 技能 (skills-hub `/skills/{namespace}`) | 只有 namespace 桶，部门是所有部门共用一个 | 无，发了就上架；只有 PII / 内网地址自动脱敏 | 无 | 只有 sha256 | 有 (删版本) |
| 知识页 (wiki-hub `/wiki/documents`) | 部门 | 无，发了同部门立刻可见；只有作者 / admin 能改或撤 | 无 | 无 | 有，但装过的副本撤不回 |
| MCP (mcp-registry) | `allowed_dept` 按部门过滤 | 只有 admin 能登记，员工不能提交 | 无 | — | admin 下架 |

专家包那条线是 10/18 设计的（docs/EXPERT-PACK-PLAN.md §5 §6），是四条里最完整的；本方案就是把另外三条对齐到它，再给四条都补上「审批人试用」。

## 2. 目标模型

### 2.1 范围三档

- **个人**：只在自己机器上，不经中央（现状）。
- **部门**：本部门可见可装；由**本部门主管**审（identity 的 `managed_depts`，专家包已经用它）。
- **公司**：全公司可见；由 **admin** 审。不双签 —— 流程越长越没人用；靠「能撤回 + 能追溯」兜底。

### 2.2 按风险分两级

| 级别 | 东西 | 规则 |
|---|---|---|
| **会执行的** | 技能 (script.py 会操作浏览器 / 文件)、MCP (连外部系统)、专家包 (里面有技能和工作流) | **必审**：提交 → 待审 → 审批人试用 → 通过 (签名上架) / 驳回 (写原因)。没通过别人看不见。 |
| **纯文字的** | 知识页 | **发了即可见，标「待审」**：同部门马上能搜到、能装，但页面和搜索结果上带「待审」标；主管看过打钩变「已审」；主管也能一键撤下 (撤下时装过的人那边标「已撤回」, 搜索默认不列)。 |

知识页不强审的原因：分享知识本来就该顺手，强审会把它卡死；文字的风险是「错」不是「危险」，错了撤下就行。

### 2.3 审批人试用

审核页上加一个按钮 **「装到我这里试用」**：

- 以**试用身份**装到审批人自己的 Companion：跟正式安装同一条路，但打 `trial: true` 标记 —— 不进市场、不给别人、不进专家列表的默认选路（专家包装进去后只在「找谁」里带「(试用)」后缀）。
- 技能 / 专家包：试用时能在工作台直接跑；有工作流的，**试跑结果（哪步成了、产出了什么文件、哪步停下等确认）自动附进审核记录**。
- MCP：试用只允许连**测试账号**（manifest 里标 `trial_credentials`，没标的不能试用，只能看清单）。
- 点通过 / 驳回时，试用安装自动卸掉（驳回必卸；通过的问一句「要不要转成正式安装」）。
- 试用装在审批人机器上也走验签：hub 给试用包临时签名（有效期 7 天，签名里带 `trial`），防止「试用包」被拿去当正式包传。

### 2.4 可追溯

中央一张 `share_audit` 表（skills-hub 现有的 `/audit` 扩成四条线共用）：谁提交、什么范围、谁试过（试了几次、结果）、谁批的 / 驳的、驳回原因、谁撤的、每个版本的 sha256 和签名。门户「审计」页按四条线筛。

### 2.5 签名 / 验签

专家包已有：通过时中央用 Ed25519 签 `pack_id + version + sha256 + approved_by + approved_at`，Companion 装机验签。扩到技能和 MCP 清单；知识页不签（文字，且「待审」状态本身就是标记）。

## 3. 四条线各自要改什么

### 3.1 技能

- skills-hub：`POST /skills/{namespace}` 改成提交 → `status=pending`；新增 `/skills/review`（待我审的）、`/skills/{ns}/{name}/{version}/review`（通过 / 驳回）、`/trial-token`（给审批人的临时签名）。`namespace` **不改**（路径段只允许 ASCII，部门名是中文）——范围和部门记在审核记录里，老的 `department` 桶照旧，可见性由记录决定。
- 中央门户 `SkillsHubPage`：员工看到自己提交的状态；主管 / admin 看到「待我审」列表 + 试用按钮 + 服务器拆出来的摘要（SKILL.md 全文、script.py 会打开哪些网站、要哪些凭据）。
- Companion：`skill_publish` 多一个「发到哪个范围」；`skills_install` 认 `trial` 标记和临时签名；技能列表里试用的带标。
- 工作量：hub 2 天、门户 1.5 天、Companion 1 天。

### 3.2 知识页

- wiki-hub：文档多 `review_status: pending | approved | withdrawn`、`reviewed_by / reviewed_at`；新增 `/wiki/review`、`/wiki/documents/dept/{dept}/{id}/review`；撤下复用 `unpublish` 但多写 `withdrawn_by`。
- 中央门户 `WikiMarketPage`：「待审」标 + 主管的打钩 / 撤下；审计页接入。
- Companion：装机部门页的 frontmatter 带 `review_status`；搜索结果和页面上显示「待审 / 已撤回」；已撤回的搜索默认不列（跟过期一样进 `hidden`）。「拉更新」时同步状态。
- 工作量：hub 1 天、门户 0.5 天、Companion 1 天。

### 3.3 专家包

- 已有流程不动，只加试用：`/experts/{id}/{ver}/trial`（临时签名下载）；审核记录里加 `trials: [{by, at, runs: [...]}]`；Companion 专家包安装认 `trial`，试跑结果回传 hub（`POST /experts/{id}/{ver}/trial-result`）。
- 门户 `ExpertsMarketPage` 审核卡加「装到我这里试用」和试跑结果展示。
- 工作量：hub 1 天、门户 0.5 天、Companion 1 天。

### 3.4 MCP

- mcp-registry：加「员工提交」：`POST /v1/mcp/submit`（manifest + 范围 + 测试账号说明）→ `pending`；`/v1/mcp/review`；通过后进 registry，`allowed_dept` 按范围填。manifest 加 `trial_credentials`（测试账号怎么拿）。
- 门户 `McpMarketPage`：提交入口、待审列表、试用按钮（只在有 `trial_credentials` 时亮）。
- Companion：MCP 订阅认 `trial`；试用期间的调用在出站审计里标 `trial`。
- 工作量：registry 1.5 天、门户 1 天、Companion 0.5 天。

### 3.5 公共部分

- identity：`managed_depts` 已有；admin 判定已有。不改。
- gateway：四条反代都已注入身份头；新增的路由加进白名单。0.5 天。
- 审计表 + 门户审计页四线合一：1 天。
- 文档 + 迁移脚本（老 `department` 桶的技能归到各部门）：0.5 天。

**合计约 15 个工作日。** 建议分期：

1. **第一期（先把闸装上）**：技能必审 + 知识页「待审」标 + 审计合一。这两样是现在漏得最大的（技能发了就上架、知识页没人把关）。约 6 天。
2. **第二期（审批人试用）**：四条线的试用安装、试跑结果回传、临时签名。约 6 天。
3. **第三期**：MCP 员工提交。约 3 天。

## 4. 几个明确的取舍

- **公司级不双签**：一个 admin 审就够。出了事靠撤回和追溯，不靠多一道签字。
- **知识页不强审**：发了即可见 + 「待审」标 + 可撤下。强审会让人不分享。
- **试用不是沙箱**：试用安装就是装在审批人自己的机器上跑，跟正式一样；区别只是「不给别人、到期自卸、签名带 trial」。真正的隔离沙箱（中央起容器跑技能）成本高一个量级，技能本来就是在员工机器上操作员工自己的浏览器，中央跑不出同样的环境，不做。
- **MCP 试用必须有测试账号**：没有测试账号的 MCP 审批人只能看清单，不能试。不允许审批人用自己的生产凭据试别人提交的连接器。
- **老数据**：现在 skills-hub 里已经上架的技能和 wiki-hub 里的页面全部视为「已审」（不回溯），从上线那天起新提交的才走流程。

## 5. 验收

- 员工发一个技能到部门：同部门别人**看不到**，主管「待我审」里出现；主管点「装到我这里试用」，自己机器上能跑，别人仍看不到；驳回后主管机器上自动卸掉，提交人看到原因；通过后同部门能装，装机验签通过，审计页一条完整记录。
- 员工发一页知识到部门：同部门立刻能搜到、页面带「待审」；主管打钩后标消失；主管撤下后，装过的人那边标「已撤回」、搜索默认不列。
- 审批人试用专家包里的工作流：试跑一次，审核记录里出现每一步的结果。
- 门户审计页能按四条线、按人、按时间查到以上每一步。

## 6. 接口 (11/08 后端已实现, 门户 / Companion 按这个接)

身份都由网关注入 (`X-Catfish-User-Sub / -Dept / -Role / -Managed-Depts`), 调用方只管带 OAuth token 走网关。
审批人 = 部门范围: `role=manager` 且目标部门在 `managed_depts`; 公司范围: admin / sysadmin。

### 6.1 技能 (网关 `/v1/hub/…` → skills-hub)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/skills/{namespace}?scope=department\|company&departments=a,b&note=…` | multipart 提交 (同原来), 返 `{…, review: {status: pending, …}, summary}` |
| GET | `/skills` | 只列你看得见的 (已通过且在范围内 / 自己提交的 / 待你审的); 每条多 `review: {scope, departments, status, submitted_by, submitted_at, reviewed_by, reviewed_at, review_note, legacy}`; 最新版待审时普通员工拿到上一个已通过的 |
| GET | `/skills/review` | 待我审的 `{skills: [record]}` |
| GET | `/skills/mine` | 我提交的 |
| GET | `/skills/{ns}/{name}` / `/{ver}` | 原字段 + `review` (审批人 / 提交人多 `trials`); 看不见的 403 / 404 |
| POST | `/skills/{ns}/{name}/{ver}/review` | `{decision: approve\|reject, note}`; approve 返 `signature` (Ed25519, 消息 `catfish-skill-v1\n ns\n name\n ver\n bundle_sha256\n reviewed_by\n reviewed_at`) |
| POST | `/skills/{ns}/{name}/{ver}/withdraw` | 提交人 / 审批人 / admin |
| GET | `/skills/{ns}/{name}/{ver}/trial-token` | 审批人: `{trial: true, expires, signature, public_key, bundle_sha256}`; 消息 `catfish-skill-trial-v1\n ns/name\n ver\n bundle_sha256\n by\n expires` |
| POST | `/skills/{ns}/{name}/{ver}/trial-result` | 审批人回传 `{ok, summary, files[], steps[]}` |
| GET | `/audit` | admin; 每条带 `line: skill\|expert` |

`bundle_sha256` = 把 `files_sha256` 按路径排序, 每行 `path\tsha256`, 用 `\n` 连起来再 sha256 —— Companion 装机时按下载到的文件重算, 再用 `/experts/signing-key` 的公钥验签名。

### 6.2 专家包 (同上 `/v1/hub/experts/…`)

新增 `GET /experts/{id}/{ver}/trial-token` (同技能, 消息 `catfish-expert-trial-v1\n id\n ver\n sha256\n by\n expires`)、`POST /experts/{id}/{ver}/trial-result` (`{ok, summary, workflow, files[], steps[]}`); detail 对审批人 / 提交人多 `trials`。

### 6.3 知识页 (网关 `/v1/wiki/…` → wiki-hub)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/documents` | 同原来; 返回多 `review_status: pending` (重发也回到 pending) |
| GET | `/documents`, `/documents/dept/{dept}/{id}` | 每条多 `review_status: pending\|approved\|withdrawn` (+ `reviewed_by/at`, `review_note`, `withdrawn_by/at`, 老的 `legacy: true`) |
| GET | `/review` | 待我审的 (主管: 管的部门; admin 全部) |
| POST | `/documents/dept/{dept}/{id}/review` | `{decision: approve\|withdraw, note}`; withdraw 要写原因, 等于 unpublish + 标 withdrawn |

### 6.4 MCP (网关 `/v1/mcp/…` → mcp-registry)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/submit` | `{manifest_yaml, scope, departments[], note, trial_credentials}` → `{submission: {id, status: pending, …}}` |
| GET | `/review` / `/mine` | 待我审的 / 我提交的 `{submissions: […]}` |
| GET | `/submissions/{id}` | `{submission, manifest_yaml}` (审批人 / 提交人) |
| POST | `/submissions/{id}/review` | `{decision, note}`; approve → 写进 manifests, `allowed_dept` 按范围, 同事可订阅 |
| GET | `/manifest/{id}` | 待审的只有审批人能拿到, 且返回 `trial: true` |
| POST | `/subscribe` | 多 `trial: true`: 审批人订阅待审的连接器 (提交人要写了 `trial_credentials` 才行), 审计 `subscribe_trial` |

### 6.5 实现时补的几条 (11/08 晚)

- 三条线审计的字段名不一样 (技能 `event/by_user/namespace/name/version`, 专家 `event/by/id/version`, 知识 `event/by_user/namespace/file_id`, MCP `action/user_sub/connector_id/meta`), 门户「分享审计」里统一成 时间 / 哪条线 / 事件 / 谁 / 对象 / 备注; `line` 技能 + 专家由 hub 给, 知识页门户自己标 `wiki`, MCP 由 registry 给 (`GET /v1/mcp/audit`, admin)。
- `GET /v1/wiki/review` 返 `{documents}`; `GET /v1/hub/skills/review|mine` 返的是审核记录 (`{skills: [record]}`), 不是技能摘要。
- `GET /v1/mcp/subscribed` 每条多 `trial: true|false` (连接器还没上架的订阅 = 审批人试用), Companion 据此打标、审完自动退订。
- Companion 装 hub 技能走 tool-bridge `catfish_tools_install._install_from_hub` (Python), 下载带 OAuth token (以前不带, 新的可见性规则下会 403), 按 §6.1 重算 `bundle_sha256` 并验签; 试用安装在技能目录里放 `trial.json`。

## 7. 进度 (11/08)

三期一次做完, 已落代码:
- 后端: skills-hub / wiki-hub / mcp-registry / gateway 反代 (§6 全部接口), 测试 skills-hub 38 · wiki-hub 22 · mcp-registry 57 · 网关代理 25。
- 门户: 四个市场页 + 分享审计, 测试 30。
- Companion: 对话里发技能带范围 (`skill_publish` scope / departments / note); 装技能验签; 审批人「装到我这里试用」(技能 / 专家包 / MCP)、「回传试用结果」、「卸掉试用」, 审完自动卸 / 退订; 共享知识页 待审 / 已撤回 标 (搜索默认不列已撤回)。
- 验收 (§5) 对应的自动测试: 技能 `central/skills-hub/tests/test_skill_review.py`, 知识 `central/wiki-hub/tests/test_wiki_hub_app.py` 末尾两条, MCP `central/mcp-registry/tests/test_submissions_api.py`, Companion `edge/tool-bridge/tests/test_skill_trial.py` + `SkillTrialUi.test.tsx` / `McpTrialUi.test.tsx`。
- 没做成一键的: 试用通过后「转成正式安装」是自动卸掉试用 + 提示去市场点「安装」, 不是原地一键转。Companion「我的技能」卡上分享时直接选范围 (本部门 / 全公司) + 给审批人的一句话 (1.2.123)。
