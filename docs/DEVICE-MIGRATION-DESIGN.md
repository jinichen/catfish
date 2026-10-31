# 换机迁移设计（BL-L9）

> 状态：**已实现**（Companion 1.2.18 / 中央端 alembic 20261031_016）· 设计 2026-10-31，同日按评审默认值落地。
> 实现与设计稿不同的地方见 §11。
> 范围：员工换电脑 / 重装系统时，把「鲶鱼对你的积累」完整搬到新机器。
> 不在范围：两台电脑同时用、内容实时合并（见 §9）。

## 1. 要解决的问题

- 鲶鱼的价值在于越用越懂你：工作日志、提炼出的记忆、画像、文书风格、个人知识库、技能、早安待办和对话历史。这些**只在员工本机**（数据边界，`CENTRAL-EDGE-DATA-BOUNDARY.md`），中央没有副本。换机 = 清零。
- 现在没有任何迁移手段。`ELEVATOR-PITCH.md` 写的「`~/.catfish/` cp 到新电脑」不成立：
  - 对话历史、记忆、人设在 Hermes 目录（`~/.hermes/`，Windows 是 `%LOCALAPPDATA%\hermes`），不在 `~/.catfish/`；
  - 运行中直接拷 SQLite 文件可能拷到写了一半的库；
  - 配置里有本机绝对路径、本机凭证引用；Mac ↔ Windows 路径格式不同；
  - 凭证在系统钥匙串里，拷目录拷不走（也不该拷走，见 §5）。

## 2. 方案选择

| 方案 | 做法 | 数据边界 | 结论 |
|---|---|---|---|
| **A. 迁移包** | 旧机导出一个加密文件（员工设口令），U 盘 / 隔空投送 / 网盘带到新机，新机导入 | 不经过中央，不碰 | **本期做** |
| B. 扫码直传 | 两台都在线，新机扫码；加密流经中央转一手、中央不留存 | 用已有的「只转交、不留存」例外（同同事邮筒） | 以后按需加，复用 A 的包格式 |
| C. 多机实时同步 | 两台同时用，自动合并 | 要么中央存密文（新例外），要么点对点 + 冲突合并，很重 | 不做 |

A 的包格式设计成可以直接被 B 复用：B 只是换了个运输方式。

## 3. 员工看到的流程

**旧电脑**：设置 → 隐私 / 本机数据 → 「换机迁移」→ 导出
1. 勾选要带的内容（必带项默认勾上不可取消，可选项显示大小）。
2. 设迁移口令（两次输入；提示「口令丢了包就打不开，我们也帮不了」）。
3. 导出期间暂停后台任务（早安刷新、邮件分诊、记忆蒸馏），保证快照一致；Hermes 不用停（见 §6.1）。
4. 得到 `鲶鱼迁移包-<姓名>-<日期>.catfishmove`，提示「这台电脑上的数据不会删除」。

**新电脑**：装好 Companion → 首次启动引导页多一个入口「从旧电脑迁移」（设置里也有）
1. 选包、输口令 → 校验（版本、完整性、是不是同一个人，见 §7）。
2. 显示包里有什么、各多少条，确认导入。
3. 导入完成 → 提示需要重新做的事：登录、邮箱授权、微信重新扫码、MCP 授权、下载本地模型（列清单，每项一个按钮直达）。
4. 后台重建索引（搜索、向量、知识图谱），进度可见，期间功能可用、只是搜索不全。

## 4. 带什么、不带什么

路径写成 Mac 的样子；Windows 下 `~/.catfish` = `%USERPROFILE%\.catfish`，Hermes 目录 = `%LOCALAPPDATA%\hermes`（`catfish_paths::hermes_home_for`）。

### 4.1 必带（员工的积累，丢了找不回）

| 内容 | 位置 |
|---|---|
| 工作日志 | `~/.catfish/employee_journal.md` |
| 提炼的事实 / 记忆 | `~/.catfish/distilled_facts.md`、`session_facts.json`、`memory_distill_state.json`；Hermes `memories/` |
| 画像、人设 | Hermes `USER.md`、`SOUL*.md` |
| 对话历史 | Hermes `state.db`（含早安事项对话的会话） |
| 早安 / 待办 / 决策 | `~/.catfish/task_chat/`、`tasks.jsonl`、`todo_store/`、`decisions.jsonl`、`advisor_closed.jsonl`、`situation/` |
| 个人知识库 | `~/.catfish/wiki/`、`wiki_relation_vocab.custom.json` |
| 技能 | `~/.catfish/skills/`、`skill_proposals.jsonl`、`skill_revisions.jsonl`；Hermes `skills/`（员工自建的部分） |
| 专家 / 销售 / 会议模板 | `~/.catfish/experts/`、`sales/`、`meeting-templates/`、Hermes `profiles/expert-*`（只带员工改过的设定，不带运行环境） |
| 定时任务 | Hermes `cron/` |
| 审计类日志 | `~/.catfish/outbound_log.db`（§6.2）、`approval_audit.jsonl`、`memory_audit.jsonl`、`skill_audit.jsonl`、Hermes `.catfish_audit.jsonl` |
| 偏好设置 | `companion.yaml`、`embedding.yaml`、`memory_plugin.yaml`、`picker_state.json`、通知 / 主动提醒开关 |

### 4.2 可选（员工勾选，显示大小）

`~/.catfish/uploads/`、`outputs/`、`output/`、`exports/`、`strategic_docs/`。

### 4.3 不带，新机自动重建 / 重新下载

| 内容 | 原因 |
|---|---|
| `search.db`、`wiki_embeddings.db`、`wiki_graph.db`、`attachments.db`、`advisor_embed_cache.db`、`advisor_cache.json` | 派生索引 / 缓存，可重建；带了反而可能和新机模型版本不一致 |
| 邮件分诊、钓鱼扫描、邮件列表缓存 | 派生数据，新机重新跑；且含邮件内容摘录，能不搬就不搬 |
| `models/`、`runtime/`、`edge-runtime/`、Hermes `hermes-agent/`、`plugins/` | 安装产物，装 Companion 时就有 |
| `wiki-shared/` | 部门共享知识，从中央重新拉 |
| `traces/`、各类 `.log`、`skill-trash/`、`skill-staging/` | 临时 / 诊断数据 |
| Hermes `config.yaml` | 由 Companion 按当前版本重新生成（`hermes_profile_sync`）；整份拷贝会带进旧路径 |

### 4.4 刻意不带（安全底线：包丢了也不泄露账号）

| 内容 | 位置 | 新机怎么办 |
|---|---|---|
| 登录令牌 | 钥匙串 + `~/.catfish/oauth/` | 重新登录 |
| 邮箱密码 / 授权 | 钥匙串（`imap_credentials`） | 重新授权 |
| 教学录制里存的网站密码 | 钥匙串（`catfish-teaching:*`）+ `teaching_credentials.json` | 重新录入 |
| MCP 授权 | `~/.catfish/mcp/` 里的 token | 重新授权（MCP 的非密钥配置可以带，导出时剥掉 token 字段） |
| API 密钥、`CATFISH_DEFAULT_USER` 等 | Hermes `.env`、`auth.json` | 登录后由 JWT 同步重新写 |
| 微信绑定 | Hermes `weixin/`、`platforms/`、`wechat_expert_routes.json` | 重新扫码；路由规则可以带，绑定不带 |
| 服务器证书 | `server-ca.pem` | 跟单位部署走，新机安装时就配好 |

清单是按 2026-10-31 代码 grep 出来的初稿，实施时第一步是写一份「目录 → 归类」的表驱动清单（§8），未归类的文件导出时报警，避免以后新增的数据悄悄漏掉。

## 5. 包格式

```
鲶鱼迁移包.catfishmove
└─ 加密层（age 格式，口令派生密钥 scrypt）
   └─ zip
      ├─ manifest.json        版本、来源、内容清单、每个文件的 sha256
      ├─ catfish/...          ~/.catfish 下的相对路径
      ├─ hermes/...           Hermes 目录下的相对路径
      └─ db/                  SQLite 一致性快照（state.db、outbound_log.db …）
```

- **加密**：用 `age`（Rust crate `age`）的口令模式。理由：成熟、流式（大包不占内存）、格式公开——万一 Companion 打不开，也能用官方 `age` 命令行解出来人工恢复。现有依赖里没有加密库，这是唯一新增的重依赖。
- **zip**：用现有 `zip` 依赖；文件名一律存相对路径 + `/` 分隔，导入时校验不许 `..` / 绝对路径（防目录穿越）。
- **manifest.json**：
  ```json
  {
    "format": 1,
    "created_at": "2026-10-31T14:20:00+08:00",
    "companion_version": "1.2.16",
    "source": { "os": "macos", "device_id": "…", "user_sub": "…", "user_email": "…" },
    "schema": { "state.db": 7, "outbound_log.db": 3, "task_chat": 2 },
    "items": [{ "path": "catfish/employee_journal.md", "sha256": "…", "bytes": 48213, "class": "required" }],
    "egress_chain": { "last_id": 4820, "last_hash": "…" }
  }
  ```

## 6. 关键技术点

### 6.1 一致性快照

- SQLite 库用 `VACUUM INTO '<临时文件>'` 出快照（不需要 rusqlite 的 backup feature，Hermes 在跑也安全，读事务内完成）。
- JSONL / Markdown：导出前暂停 Companion 自己的后台写入（早安、分诊、蒸馏、记忆写回）；Hermes 侧的追加写（`.catfish_audit.jsonl`、`memories/`）按「先记长度、再拷前 N 字节」截在一行边界上。
- 导出结束恢复后台任务；中途失败也要恢复（RAII guard）。

### 6.2 数据外发记录链

现状：`outbound_log.db` 是一条哈希链，每行 `row_hash = sha256(上一行 + 字段)`；Companion 每天把链头 `(last_id, last_hash)` 报给中央 `egress_anchor`（只有指纹）。**中央锚点按 `sub`（人）记，不分设备。**

方案：
1. 迁移包原样带走 `outbound_log.db`，新机接着这条链往下写 —— 历史可查、中央锚点照样对得上。
2. 新机导入时在链上追加一条「迁移」记录（`purpose = 设备迁移`，内容是来源设备 ID 和导出时链头），本身也进链、也报锚点。审计时能看到「哪天从哪台设备搬来的」。
3. **防分叉**：旧机导出后如果继续用，它会接着写自己那份链并报锚点，中央会收到两条互相对不上的链头。解决：锚点请求加 `device_id` 字段（中央表加一列，仍然只是元数据，不碰边界），校验时按设备分组比对；导入时新机继承旧机 `device_id` 的历史段，之后用自己的 `device_id`。旧机导出后弹提示「建议在新电脑导入成功后，在这台电脑上退出登录」。
4. 中央改动：`egress_anchor` 加 `device_id TEXT NULL`（老数据为空，按旧逻辑比对），`POST /api/egress/anchor` 收可选字段。改中央要按规矩 `central_version.sh --write`。

### 6.3 路径改写

- 包内一律存相对路径，导入时拼到新机的 `catfish_home` / `hermes_home`。
- 文件内容里存了绝对路径的地方（已知：`email_config` 的 `mail_dir`、任务 / 输出记录里的文件路径、附件 `kept_path`），导入时把「旧 home 前缀」替换成「新 home 前缀」，Mac ↔ Windows 同时转分隔符。对不上前缀的路径原样保留、导入报告里列出来。
- 实施时要统一 home 目录的取法：现在 `catfish_home()` / `catfish_dir()` 有 8 个文件各写一份（`wiki_slug.rs`、`wiki_read.rs`、`server_config.rs`、`profile.rs` …），迁移代码只认 `catfish_paths` 里的一份，顺手把其余的收过去。

### 6.4 版本兼容

- 只允许「新机 Companion 版本 ≥ 包里的版本」导入；低了提示先升级。
- 各库带 schema 版本号；导入后走各自已有的迁移逻辑（如 `task_chat_migration`）升到当前版本。

## 7. 安全与校验

- 口令强度：至少 8 位，弱口令提示但不拦（员工自己的数据，自己担）。
- 导入前校验：
  1. 解密成功（口令对）；
  2. manifest 里每个文件 sha256 对得上（防损坏 / 篡改）；
  3. **同一个人**：包里的 `user_sub` 和新机登录的人一致才导入；新机还没登录时先导入、首次登录时比对，不一致则拒绝并回滚。防止把别人的积累导进自己电脑。
- 新机已有数据时：默认拒绝（「这台电脑已经有鲶鱼数据」），可选「先备份当前数据再覆盖」——不做两边合并。
- 导入是事务性的：先解到临时目录、全部校验通过后整体换入；失败不留半成品。
- 迁移包本身不上传中央；外发记录里会有一条「导出迁移包」的本地审计（不含内容）。

## 8. 实现拆分

| 块 | 内容 | 估时 |
|---|---|---|
| 清单 | `migration/catalog.rs`：目录 / 文件 → 必带、可选、重建、不带 的表；未归类文件告警；单测钉住 | 1 天 |
| 导出 | 快照、暂停 / 恢复后台任务、打包、age 加密、manifest | 2 天 |
| 导入 | 解密、校验、同人检查、临时目录换入、路径改写、schema 升级、触发重建 | 2.5 天 |
| 外发链 | 迁移记录行；中央 `device_id` 列与接口；校验按设备分组 | 1 天 |
| 界面 | 设置入口、首启引导入口、勾选 / 口令 / 进度 / 导入报告 / 「还需要重新做的事」清单 | 1.5 天 |
| 测试 | 单测 + 真机：Mac→Mac、Mac→Windows、Windows→Mac；大包（>2GB 附件）；中途断电；口令错；别人的包 | 1.5 天 |

合计约 9.5 个工作日（≈ 2 周，比上次口头估的 1～1.5 周多，主要多在外发链防分叉和同人校验）。

## 9. 以后（不在本期）

- **B 扫码直传**：中央加一个只转交不留存的中转通道，两端用一次性密钥对（扫码交换）端到端加密，复用本期包格式。
- **误删保护**：同一套导出逻辑，定时在本机生成加密快照（保留最近 N 份），BL-L9 的另一半。
- **多机同时用**：先观察真实需求；真要做，倾向点对点同步 + 按数据类型定合并规则，不在中央存密文。

## 10. 已拍板（2026-10-31，按默认）

1. 迁移包**不允许跨人**导入；交接走技能分享 / 专家包。
2. `uploads/`、`outputs/` 等可选项**默认不勾**。
3. 旧机导出后**只提示**退出登录，不强制。
4. `ELEVATOR-PITCH.md` 那句已随功能上线改掉。

## 11. 实现说明（跟上面的设计有出入的地方）

| 设计稿 | 实际 | 原因 |
|---|---|---|
| §3 导出时暂停后台任务 | 不暂停 | 一致性已由「SQLite `VACUUM INTO` 快照 + JSONL 只取到最后一个整行」保证；其余文件都是整文件原子改写，拷到的要么是旧版要么是新版 |
| §3 / §7 新机没登录也能先导入，首次登录再比对、不一致回滚 | **必须先登录再导入** | 首次引导里迁移步骤放在「登录」之后，导入前直接比对；省掉「导入后再回滚」这条复杂路径 |
| §4.4 MCP 非密钥配置剥掉 token 后可以带 | `~/.catfish/mcp/` 整个不带 | token 跟配置混在同一批文件里，剥不干净就宁可不带；新机用到时重新授权 |
| §4.1 Hermes `profiles/expert-*` 只带改过的设定 | `profiles/` 不带 | 专家运行环境由 Companion 按 `~/.catfish/experts/` 重新生成，`experts/` 是必带项 |
| §6 Hermes 在跑时怎么换 `state.db` | 用 SQLite backup 接口**在线还原**进正在用的库，不停 Hermes；导入完再重启 Hermes 让记忆 / 人设 / 技能重新加载 | 停 launchd 托管的进程不可靠（KeepAlive 会拉起） |
| — | 新增「未归类」：清单里没登记的第一层文件不带，导出页列出来 | 2026-10-31 用开发机真实目录名核对：`~/.catfish` 第一层约 90 项、Hermes 约 70 项原来没登记；大部分是旧备份 / 临时文件，按规则归到「重建」；仍不确定的（如 Hermes `hooks`、`shared-state.db`、`verification_evidence.db`、`~/.catfish` 下几个早已不被代码引用的老目录）保持「未归类」，导出时提醒 |
| — | `chrome-profile`（浏览器 cookie）、`feishu.yaml` 归「凭证」不带 | 核对真实目录时发现 |

代码位置：`edge/companion-app/src-tauri/src/commands/device_migration/`（`catalog.rs` 清单、`snapshot.rs` 快照与路径改写、`package.rs` 包格式、`apply.rs` 导入、`tests.rs` 端到端）、`commands/transparent_log_lineage.rs`（外发记录链）、`services/device_id.rs`；界面 `tabs/Dashboard/DeviceMigrationCard.tsx`、`components/DeviceMigrationImport.tsx`、`components/OnboardingStepMigrate.tsx`；中央 `central/llm-gateway/src/catfish_gateway/egress_anchor_*.py`。
