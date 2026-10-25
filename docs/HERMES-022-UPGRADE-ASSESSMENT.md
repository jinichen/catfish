# hermes v0.21.0 → v0.22.0 升级评估 (2026-10-14)

> 当前: v0.21.0 · tag v2026.8.31 · commit 29112bef。目标: v0.22.0「The Threshold Release」· commit e06df9c2 (2026-10-14 发布)。
> 中间隔 0.21.1–0.21.7 七个小版本, **25,544 个提交**。上游已有 rc.1-v0.22.1, 0.22.1 很快会发。
> 方法: 两个版本源码逐个对照 catfish 依赖的每个挂载点; 能实测的都在副本上实测 (没碰 ~/.hermes)。
> 证据 (file:line) 见四份分报告 (审批 / 运行时与接口 / 定时任务·微信·记忆 / 安装与集成), 复现脚本在评估用的 scratchpad。

## 一句话结论

**不能直接升, 也不是"更新补丁锚点"就能升。** 这一版上游做了三件结构性的事:

1. **大拆分**: `gateway/run.py` 33,539 → 6,082 行, `run_agent.py` 9,413 → 1,555, `tools/approval.py` 5,971 → 1,251, `hermes_state.py` 拆成几十个模块。
2. **运行时换成 Python 3.14 + 自带包管理器 (`hermes pm`)**: 核心依赖全部只在 3.14 上装; install.sh / install.ps1 整个重写。
3. **提示语全部多语言化**: 3,433 个 key 都有官方中文。

catfish 受影响的三块: 鲶鱼插件 (36 个补丁)、离线安装链路、Companion 界面与 hermes 的接口。

## 实施进度 (10/14, 分支 hermes-v022, Companion 1.0.122 → 1.0.129)

A/B/C/D 四块都已改完并提交; 每块都在 v0.22 测试网关 (proto 安装 + 两个鲶鱼插件 + 本机鲶鱼网关) 上真跑过。

| 块 | 状态 | 验证 |
|---|---|---|
| A 安装链路 | Companion 自己装 (Python 3.14 + 离线 wheelhouse + 安装戳 external), mac / Windows 同一套 | mac 离线装机 e2e; Windows MSI CI (含打包后 Windows 运行时启动冒烟) 全绿 |
| B 规定配置 | `services/hermes_policy_config.rs`: 启动时 + 装机建好 config 当场写 | 单测 + 测试网关按该配置启动、对话 |
| C 插件 | 自检认 mixin; 补丁全部装上 (`_PATCH_FAILURES=[]`); 退役 P15 / P15.4 / P19 / P27; 新增 P58 语言包 | 插件测试 565 过; 测试网关真加载 |
| D 界面 | 原生 `approval.request` + `/v1/runs/{id}/approval`、`hermes.status`、思考内容不进正文 | 端到端: 删目录请求 → smart 转人工 → 新接口拒绝 → 未执行 |
| 专家 Bot | 白名单作废 → 用 profile 的 `gateway.parked` 停放标记 | 单测 |
| 会议转写包 | 包里自带 Python 3.11.15 (1.1.0) | mac 版打包自检 + Companion 安装代码 e2e 通过; Windows 版待 CI 打 |
| 审批留痕 | `~/.catfish/approval_audit.jsonl` (smart / 员工 / 超时 / P54 免批) | 测试网关实测写入 |

### 实施时新发现、评估里没有的 (都已处理)

| 问题 | 不处理会怎样 | 处理 |
|---|---|---|
| `provider: openai-api` 在 v0.22 变成"只连官方 OpenAI、只读环境变量" | **员工机一条都聊不了** (No usable credentials) | 改 `provider: custom` + `key_env: OPENAI_API_KEY`; 员工 1 小时令牌移出 `model.api_key` (custom 下它会压过 key_env, Companion 关一小时后全 401), 另存 `catfish-companion-token` 给 Windows 聊天用。只在有 v0.22 安装戳时切 (v0.21 不认 key_env) |
| 插件替换内置 `memory` 工具要授权 | 两个插件的记忆路由整个失效 | 规定配置写 `plugins.entries.<插件>.granted_capabilities: [tools.override]` (+ 旧键); 插件清单声明能力 |
| v0.22 不再装 PyYAML | 插件 / 记忆网关 / 工具桥 `import yaml` 直接报错 | 加进 `hermes-extra-packages.txt` |
| `agent.max_turns` 不设 = 不限轮数 | 卡住的任务无限跑 | 规定配置没配时补 90 |
| pre_tool_call 钩子 30 秒超时就拦, 之后 60 秒内该钩子一律跳过 = 拦 | 记忆检查里的模型调用一慢, **一分钟内所有工具调用都被拦** | 记忆检查模型调用按阶段限时, 最坏 20 秒 |
| 异步辅助调用不收 `extra_headers` | P3 照原写法会让异步辅助调用 TypeError | P3 改包 `_plan_aux_call` (同步异步都经过) |
| 审批"无人值守"判断有两份 (approval / approval_context) | Companion 的写文件保护、Codex 审批被当成"没人能回答"直接拒 | P15.3 两份都换, 且只认 api_server (webhook 每轮都注册回调但没界面) |
| 克隆 profile 时 `API_SERVER_*` 被当渠道配置去掉 | 专家 Bot 鉴权失败 | Companion 建 profile 后本来就从主 .env 补写, 已核; profile 同步时连 provider / key_env 一起抄 (Codex 不带) |
| 源码品牌补丁在 v0.22 几乎全对不上, 整函数替换欢迎横幅会把命令行弄崩 | 开发机 `catfish` 启动自愈把 CLI 弄坏 | v0.22+ 停用, 品牌由 P58 语言包负责 |
| `cryptography` 49 起不发 Intel Mac 轮子 (v0.22 锁 50.0.1, 安全修复), `greenlet` 没有 Intel Mac 的 3.14 轮子 | **Intel Mac 装不上** | 打包机现编: greenlet 用原生 clang 交叉编; cryptography 用 maturin 交叉编 + 静态 OpenSSL 3.5.9; Xcode 27 链接器把 x86_64 字符串表放在 4 字节对齐处 (dyld 拒载), 用 `scripts/macho_align_strtab.py` 补齐。Intel 资源离线装机 e2e + 真加载通过 (Rosetta)。打 Intel 包要 `rustup target add x86_64-apple-darwin` |
| `lib-download.sh` 的 `GH_PROXY=""` 从没生效 (`:-` 把空当没设) | CI 直连不了 | 改 `-` |

### 还剩的

- Windows 会议转写包 1.1.0: 推送后跑 `build-meeting-asr-pack.yml`; mac 1.1.0 已在 `~/catfish-components/`, 两个都要放进中央组件目录重出清单。
- 全新 Companion 包在真机上装一遍 (mac / Windows), 再从 0.21 原地升级一遍; 灰度顺序不变。
- **开发机注意**: `~/.hermes/plugins/` 下两个插件是软链到仓库工作区的。工作区在 `hermes-v022` 分支、本机 hermes 还是 0.21 时,
  0.21 网关一重启就会加载新插件 → 自检不过 → 插件不加载 (身份头 / 微信中文 / 审批界面都没了)。本机要么切回 main, 要么用这个分支的 Companion 把本机升到 0.22。

## 不改就升会发生什么 (按严重程度)

| # | 后果 | 原因 | 级别 |
|---|---|---|---|
| 1 | 装不上 / 起不来 | Companion 内嵌 Python 3.11, 在 v0.22 上依赖一个都装不进去; 离线安装补丁的锚点 sh 8 条里 7 条、ps1 7 条全部对不上, Companion 传的 `--offline-*` 参数直接报错 | P0 |
| 2 | 鲶鱼插件整个不加载 | 插件启动自检要找的 4 个挂点搬家或被删 (`_resolve_auto`、`_apply_client_headers_for_base_url`、`run_conversation`、P15 的 `_stream_q`) → 36 个补丁全部不生效, 包括"员工身份不串"那道防线 | P0 |
| 3 | Companion 里执行代码由 AI 先判断, 低风险自动放行 | 新版默认 `approvals.mode: smart`; 原来强制 Companion 人工审批的 P15.4 因 `_get_approval_mode` 搬家静默失效。**10/14 已定: 采用 smart**, 这一条从"漏洞"变成预期行为, P15.4 退役 (见下文"审批模式") | 已决策 |
| 4 | "执行代码"清不出永久免审名单 | `save_permanent_allowlist` 变成"磁盘 ∪ 内存", P20 日志写已移除实际没删 | P0 安全 |
| 5 | 有网时 hermes 自己换掉运行环境 | Companion 装机时建的空 `.git` 让 v0.22 把它当成"自更新安装", 联网下载 Python 3.14 另建环境, Companion 装进去的 jieba / playwright / 邮件 / 微信读取器全部失效; 离线时每次启动卡约 14 秒 | P0 |
| 6 | 员工 90 天前的老会话被删 | `sessions.auto_prune` 默认从 false 改成 true, 不认 Companion 的软删标记和任务关联表 (实测删了, 留下孤儿行) | P0 数据 |
| 7 | 回复气泡里出现模型的思考过程 | 8642 流新增 `delta.reasoning_content`, Companion `chat.ts:646` 当正文渲染 | P1 |
| 8 | 审批按钮不弹, 只能等超时 | 新版原生发 `event: approval.request` / 回执 `POST /v1/runs/{id}/approval`, Companion 只认旧的 `approval_pending` | P1 |
| 9 | 微信里冒出"代理/网关/Hermes", "小鲶"措辞消失 | 提示语多语言化后, P28 的英文原句替换大部分对不上; zh.yaml 有 126 处 Hermes/☤ (网关和平台 57 处会推到微信); 还有 8 处新增英文硬编码会发到微信 | P1 |
| 10 | 构建 / CI 变红 | 5 处用 `pyproject version` 校验, 新版 pyproject 是 `0.0.0`; Windows watchdog 补丁上游已原生修复, 补丁脚本报错 | P1 |
| 11 | 专家 Bot 关不掉 | `multiplex_profiles: false` 失效, allowlist key 被迁移删掉; clone profile 默认去掉渠道配置 | P1 |
| 12 | 会议转写包装不上 | 用 cp311 的包做的, base python 变 3.14 后装不进去 | P1 |

## 现在的 0.21.0 上就已经坏着的 (并入升级一起修, 不单独在 0.21 上发)

| 问题 | 现象 | 证据 |
|---|---|---|
| P16 会话搜索 | 包装函数不收 `detail` 参数, **每次调用都报错**; 生产日志 10/10、10/12 共 38 次 → 小鲶"搜以前的对话"一直失败 | 运行时报告 |
| P10 请求头 | 不收 `apply_user_headers`, 凭证池轮换到下一个 key 时 TypeError | `run_agent.py:6544` |
| P3a 辅助模型运行时 | 每轮 KeyError, 被上游吞掉, 从未生效 | `auxiliary_client` 3099/3118/2783 |
| P27 定时任务失败重试 | 调度模块启动时就绑定了本地名字, 包装从未被调用 | `scheduler.py:713-723` |
| P29 `/learn` 中文 | 读 `kwargs["message"]`, 实际传的是 `user_message=`, 死代码 | `api_server.py` |
| P39 Codex | `_codex_note_to_tool_progress` 新旧都不存在 | — |
| 品牌 banner 整函数替换 | 签名不收新参数 + 缺 import, TypeError / NameError | `brand_rules.py` |
| 升级脚本写的 `approvals.smart` / `session.auto_reset` | 没有代码读, 无效 key; 真正的开关是 `approvals.mode` | — |
| Windows 内嵌 Python 20260623 | 带 SQLite 3.50.4, 在 WAL-reset 漏洞范围内 (mac 已换) | `check-embedded-sqlite.py:14` |

## 不受影响 / 新版带来的好处

- **记忆插件 catfish-memory 不用改**; 新版同会话复用 MemoryManager (对节流缓冲是好事), 同步内容先脱敏。
- **state.db 26 → 31 不阻塞**: Companion 的所有 SQL 在新 schema 和"Companion 改过的老库迁移后"都实测跑通, `deleted_at`、自建索引、sidecar 表都保留。
- **8642 接口没删任何路由**; `X-Hermes-Session-Id`、Bearer 鉴权语义不变。
- **插件加载机制基本不变** (plugin.yaml / enabled / register_hook / register_tool override)。新增: 加载 10 秒超时; `plugins.isolation` 不能设 `host`。
- 仍然需要也照样生效的补丁 (只是搬家, 调用时查找): P14、P15.3、P51、P54、P55、P56、P57、P43、P45、P47、P1、P2、P4–P9、P12、P13、P17、P21、P23、P30、P40、P42、P44、P49 (只改自检)、P50。
- **新版原生已有, 我们的可以退役**: P15 (原生流式审批)、P19 (原生 `hermes.status` 进度)、P26 (原生 `/api/jobs/{id}/pause|resume`、DELETE)。
- **上游认中文审批词, 但不能替代我们的**: 它把「始终批准」「本会话」当永久/会话级批准, 也不认 1/2 → P52、P57 必须保留。
- **原生自动续跑只认英文和泰文** → P50 保留 (续跑那一轮设 `resume_unanswered_turn=False`, 防止和原生叠加续 2+2 次)。
- 微信适配器 51 个提交: iLink `ret=-2` 按会话错误处理不再算限流, 媒体发送遵守 ret, 无 token 重发不耗重试预算, long-poll 游标写盘移出事件循环。
- state.db "第二写者"一类问题上游专门修过, 对 Companion (本身就是第二写者) 有利。
- 新增按 key 覆盖语言包 (`ctx.register_locale` / `$HERMES_HOME/locales/zh.yaml`), 比 P28 逐句 str.replace 稳得多。

## 要改的清单

### A. 安装链路 (重新设计, 最大的一块)
1. Companion 不再调上游 install.sh / install.ps1, 自己完成: 解包源码 (必须带 `pm/`) → `uv venv` (内嵌 CPython **3.14.7**, pbs 20260901, 自带 SQLite 3.53.1) → `uv sync --locked --offline --no-dev --extra all` (不能 `--all-extras`, 会带上 torch) → 写 `install-stamp.json` (`updateMechanism: external`) → 自建 `hermes` 入口。uv 必须 0.12.3。
2. 删掉 `initialize_offline_git`; 已装机器启动时清掉空 `.git`, 写 stamp (实测: 之后命令 0.6 秒返回、不联网)。
3. Python 3.11.15 → 3.14.7 全仓库一起改 (约 20 处: tauri conf、wxs、artifacts.rs、构建脚本、CI); 额外 wheel 换 cp314; **会议转写包重做**。
4. 退役 `patch_install_sh_offline.py`、`patch_install_ps1_offline.py`、`patch_windows_watchdog.py`、`verify_windows_watchdog.py`、`hermes_windows_watchdog.rs`、CI 对应步骤。
5. 5 处 pyproject 版本校验改成 tag / sha / install-stamp。
6. 版本 pin: `.hermes-git-commit` = e06df9c2…, `.hermes-git-tag` = v0.22.0, `.hermes-target-version` = 0.22.0; fetch-hermes-src.sh 改成 `--exact-match` (同一 commit 上还有 rc / abandoned-rc 标签)。
7. 新写 `scripts/upgrade-hermes-v022.sh` (重建 3.14 venv、写 stamp、去 `.git`), v020 脚本的 `pip install -e .` 在新版上什么都不装。

### B. config 加固 (Companion 启动时 ensure + 升级脚本)
`sessions.auto_prune: false`、`nous.guest: false`、`tools.connectors.enabled: false` + `agent.disabled_toolsets` 加 `connections`、`auth.adopt_external_logins: false`、`security.allow_lazy_installs: false`、`agent.max_turns: 90` (不再放 .env)。`approvals.mode` 取值单独决策 (见文末)。

### C. 鲶鱼插件
1. `plugin_verify`: 认 mixin (`client_lifecycle` / `turn_facade`)、`_resolve_auto` → `_resolve_auto_route` (3 个返回值)、P15 检查改新文件或随 P15 退役、加 `_load_gateway_config`。
2. 安全: P15.4 退役 (采用 smart, 不再强制人工); P20 改成直接改 config.yaml 再重载 + 过滤 `_read_permanent_allowlist` (永久免审名单跟 smart 无关, 仍要清)。
3. 修参数透传: P16 (`detail`/`after`/`before`/`exclude_session_ids`)、P10 (`*args, **kwargs`)、P39 (换 `_load_gateway_config()`)。
4. P52: `_adapter_for_source` → `_delivery_adapter_for`; P53: 认新报错文案和 `{"calls":[…]}` 参数。
5. 删 P3a/P3b; 退役 P15、P19、P26; P27 退役或重写; P29 改读 `user_message`。
6. P28 改走中文语言包按 key 覆盖 (小鲶措辞 + 去掉 Hermes/☤ + 删 `text_choice_always`), str.replace 只留硬编码英文 (含新增 8 处); P57 的英文正则在中文下要复核。
7. 新版 pre_tool_call 钩子抛异常 = 拦截 (`plugins_dispatch.py:51,241-244`, 已核): 插件自检通过但后台安装失败时, 安全钩子会让**所有工具被拦**。要把这个行为当成设计前提重新看 `pre_tool_call_safety_check`。
8. 工具改名 `todo`→`todo_list`、`cronjob`→`cronjob_manage`、`process`→`process_manage`: `todo_collision_guard.py:77`、tool-bridge `adapter.py:659/699`、Companion `agentActivityI18n.ts:64` 都按旧名判断。
9. 兼容检查脚本 `audit_hermes_compat.sh` 改成同时搜拆出来的新文件 (现在 15 个 ✗ 里有一半是搬家误报)。

### D. Companion 界面
1. `reasoning_content` 不当正文 (折叠区或丢弃)。
2. 审批改接原生 `event: approval.request` + `POST /v1/runs/{completion_id}/approval` (RoomLink 仍用 P15.2, 先保留)。
3. 压缩 / 等待进度接 `hermes.status`。
4. 专家 Bot: 关闭改成归档 profile; 确认 clone 去掉渠道配置后能否上线。
5. 读消息考虑按 `active=1` 过滤 (老问题, 低优先)。

## 建议的升级步骤

1. **建 v0.22 分支**: 先做 A (安装链路) + C1 (插件能加载) — 这两件不做什么都测不了。
2. 再做 C2–C9、B、D, 每项带测试。"现在就坏着的"那几项本来就在 C3/C5 里 (v0.22 上同样要改), 一起修, 不在 0.21 上单独发版 (10/14 鸿波: 都要升了, 单独修是浪费)。
3. 唯一例外: 如果升级排期拖长, 再考虑把 P16 会话搜索那一处单独补到 0.21 (改动几行, 新旧版同一个修法)。
4. **回归** (按 HERMES-UPGRADE-CHECKLIST 加本次新增项):
   - 全新装机 mac / Windows, 断网装机; 老机器从 0.21 原地升级 (state.db 迁移、`.git` 清理、config 加固)。
   - 审批: Companion 执行代码必须弹卡; 微信回 1/2、合并消息、重复「1」、超时提示只出现一次。
   - 微信全部提示语抽查无 Hermes/☤/英文; "小鲶"措辞。
   - 会话搜索、记忆写入、定时任务、协同 RoomLink、扫码登录、会议转写、邮件、专家 Bot。
   - 老会话 90 天以上不被删。
5. 灰度: 先自己这台, 再一台 Windows, 再推员工。
6. 回滚: 保留 0.21 安装包和 `restore-hermes-v020.sh` 同款的 v021 回滚脚本 (state.db 31 → 26 不能降级, 回滚前先备份 state.db)。

## 工作量粗估

| 块 | 估计 |
|---|---|
| A 安装链路 (mac + Windows + CI + 会议包) | 3–4 天, 最大不确定性: 完全离线 `uv sync`、Windows 上 cp314 依赖 (pywin32 / winrt / greenlet / lxml) |
| B config 加固 | 0.5 天 |
| C 插件 | 2–3 天 |
| D Companion 界面 | 1–1.5 天 |
| 回归 + 灰度 | 2 天 |
| **合计** | **约 9–10 个工作日** |

## 还没验证的 (要上真机或 CI)

- 完全离线的 `uv sync --locked --offline` (这次只验证了联网); Windows 3.14 上的 pywin32 / winrt。
- cp314 wheel 是否齐全: greenlet、lxml、watchdog、pypdfium2; tool-bridge / 邮件 / 微信读取器在 3.14 下能否运行。
- 1GB 级 state.db 从 26 迁到 31 要多久, 首次启动时 Companion 并发打开会不会 busy。
- 微信在中文界面下实际推出来的全部文案。

## 审批模式: 采用 v0.22 的 smart (10/14 鸿波定)

新版流程 (`tools/approval_smart.py`): 危险命令 / 执行代码先交给辅助模型 (task=`approval`) 判 APPROVE / DENY / ESCALATE;
ESCALATE 或模型调用失败、返回空 → 交给员工人工审批 (失败时偏安全)。配合我们的补丁后, 一次审批的顺序是:

1. **P54 只读代码免批** (静态分析, 不调模型, 零延迟) —— 保留, 能省掉大部分查资料脚本的模型调用;
2. **smart 判断** —— 批准就直接执行, 拒绝就告诉员工"AI 判断风险高, 没执行" (P51 已保留 smart_denied 这条路);
3. **拿不准才问员工** —— 微信回 1/2 (P51/P52/P56/P57)、Companion 弹卡 (改接原生 `approval.request`)。

要跟着做的:
- **P15.4 退役** (原本强制 Companion 人工审批)。P15.3 保留 (无人值守平台不能直接拒)。
- **写公司规则** `approvals.smart_policy` (系统提示里的可信规则, 中文即可), Companion 启动时 ensure。建议初稿:
  「读取本机文件、查询数据、生成报表的脚本可以批准; 删除或覆盖文件、向外部地址发送数据、安装软件、修改系统配置、
  操作邮件 / 微信发送的, 一律 ESCALATE」—— 10/14 鸿波确认可以。
- **判断用的模型 = 员工当前用的模型, 不另外限制** (10/14 鸿波定)。`auxiliary.approval` 默认 `provider: auto`,
  `_resolve_auto_route` 第一优先就是"本轮主模型" (`agent/auxiliary_client.py:4671-4686`), 在 Companion / 微信里就是员工选的那个。
  已知后果, 都偏安全, 不用处理: ① 上游限 `max_tokens=16`, 员工若选了先"思考"的模型, 可能答不出 → 转人工, 只是人工审批多一些;
  ② 员工的模型调不通时 auto 会走备用链, 私有模型由 P47 挡住不降级到公网。回归时用默认模型确认能正常回出 APPROVE / DENY 即可。
- **留痕**: 上游会触发 `post_approval_response` 钩子 (`decided_by=aux_llm`)。接到 catfish 审计, 管理员能查"AI 自动批了哪些"。
- 回归加三条: 只读脚本免批; 明显无害的命令被 AI 批准且员工无感; 删除 / 外发类被转人工或拒绝。

### 10/15 改: 「始终允许」真生效, 删除类每次问 (鸿波定)

起因: EIS 资质待办 skill 一轮弹好几次执行代码审批; 员工点了 9 次「始终允许」全白点 (P20 拦掉了执行代码的永久批准,
按钮还在); 「本会话允许」在 Companion 只管这一条消息 (api_server 拿 completion id 当审批会话); 定时任务里执行代码被
`cron_mode: deny` 一律拒, skill 进不了定时自动化。鸿波: 「只要点过始终允许, 下一次就不要再审批了, 除非那种删除等严重破坏内容的指令」。

现在的顺序: P54 只读免批 → **P59** (点过始终允许 → 直接放行, 聊天 / 微信 / 定时任务都一样; `destructive_code.py`
判出删除 / 结束进程 / 格式化 / 改安全设置 / 下载即运行 → 有人在就每次问人 (不走 smart、不记会话 / 永久批准),
定时任务里直接不执行) → smart → 问人。P20 改成: 执行代码可以永久批准, 删除类终端命令不许。
Companion 审批卡去掉「本会话允许」, 删除类不显示「始终允许」并写出原因。员工要重新点一次「始终允许」(之前点的被 P20 清掉了)。

## 需要你拍板的

1. **要不要跟 0.22.0, 还是等 0.22.1** (rc 已出, 大版本首个补丁通常修一批回归)。建议: 改造现在就开始 (基于 0.22.0 做), 正式切版本时换成 0.22.1 再回归一遍。
2. ~~`approvals.mode` 用什么~~ → 已定 smart, `smart_policy` 措辞已确认 (见上一节)。

### 10/17 切到 v0.22.2 (Companion 1.1.17)

pin: tag v0.22.2 · commit d923a5eb (`.hermes-git-commit` / `.hermes-git-tag` / `.hermes-target-version`)。上面第 1 条的回答:
**跳过 0.22.1, 直接 0.22.0 → 0.22.2** —— 0.22.1 建的会话在 0.22.2 里删不掉 (上游已知问题), 所以 0.22.1 一版都不发。
装了新包的机器启动时比对 commit 不一致 → 从包里带的归档重装 (和 10/14 一样的路径), 不联网。

核过的:
- `audit_hermes_compat.sh` 68 ✓ / 0 ✗; `plugin_verify._verify_patch_targets` 48 个补丁锚点在 0.22.2 全在 (反向对照照常报 ImportError)。
- 审批接口: 0.22.2 仍按名字读 `choice` / `request_id`, 多余字段忽略; 409 细分成 approval_not_active / approval_not_pending, 我们原来就按 409 处理。
- 依赖只多了 httpx2 2.12.0, 已进离线 wheelhouse; mac 两个架构的 hermes 归档跟 0.22.2 源码逐字节一致 (抽查 run_agent.py / uv.lock)。
- state.db 列 (`display_kind` / `_compressed_summary` / `active` / `compacted` / `message_uid`) 没变, 1.1.16 的历史显示规则照用。
- Windows: MSI 工作流按 pin 文件现打 hermes 归档, 不用另改。

### 10/25 评估 0.22.3 / 0.22.4 / 0.22.5: 暂不升, 只把有漏洞的依赖单独换新 (Companion 1.1.77)

结论: 三个补丁版本都先不跟。拿各版源码跑了上面同一套检查 (源码静态检查, 不执行):

- `audit_hermes_compat.sh` 三版都是 67 ✓ / 0 ✗; 但 `plugin_verify._verify_patch_targets` **三版都失败** ——
  0.22.3 起微信 (连同 Signal / QQ Bot / 元宝等) 挪成内置插件 (上游 #139843), `gateway/platforms/weixin.py`
  变成 `plugins/platforms/weixin/adapter.py`。我们打补丁的 `WeixinAdapter` / `_api_get` / `save_weixin_account`
  都还在, 只是换了位置; 鲶鱼插件里引用旧路径的有 13 处, 升级时要一起改, 再真机跑通微信。
  不改就升, 插件按设计拒绝加载 (防串员工数据), 员工隔离 / 审批 / 微信全部失效。
- 上游还开着两个回归, 正好是我们老会话的形状 (`source` 是自定义标签、`user_id/session_key/chat_id/origin_json`
  全空; 本机 1149 个 `companion` + 7 个 `companion-migrated`): #143831 (0.22.4 起恢复报 not_found)、
  #143839 (0.22.5 删除 / 归档报 permission_denied)。等这两个修了再升。
- 不受影响的: Companion 调的 hermes 接口 (含 `/v1/runs/{run_id}/approval`, 仍按 `request_id` 读) 全在;
  state.db SCHEMA_VERSION 31 → 32 但上游注明不做数据迁移, Companion 读的列没变; 压缩标记文字没变 (1.1.73 的过滤照用)。

依赖换新 (不动 hermes 代码): 上游 #139963 清了一批依赖告警, 跟着员工机装的有 pyjwt 2.13.0 (严重) / urllib3 2.7.0 /
multidict 6.7.1 / mcp + mcp-types 2.0.0。版本取 0.22.5 锁定的那个, 清单在 `src-tauri/hermes-dep-overrides.txt`:

- 打包: `build_hermes_wheelhouse.py` 用 `uv pip compile --override` 盖过 0.22.2 的 uv.lock, 解析完核对覆盖确实生效。
  mcp 必须和 mcp-types 同版本一起换。只变了这 6 个包 (另外 xlsxwriter / tzdata 的变化不带覆盖也一样, 跟这次无关)。
- 老机器: hermes 提交号没变不会整个重装, 已装目录里还是旧清单和旧 wheel。Companion 启动时
  (`hermes_dep_overrides.rs`) 发现已装清单低于覆盖版本, 就从安装包的 hermes 归档里只解出新清单 + wheel 换进去;
  运行环境里装的版本低于覆盖版本, 按缺包离线补装 (`hermes_runtime_deps.rs`), 然后重启网关。
- 验证 (隔离环境, hermes 0.22.2 源码 + 新依赖, 对照组是旧依赖):
  · hermes 里 mcp / jwt / 控制台登录 / OAuth 相关测试 184 个文件 ~1900 条: 只多出 2 条失败, 都在
    `test_mcp_oauth_manager.py`, 是测试假对象跟不上新版 mcp (上游 0.22.5 只给测试补了 `resp.next_request = None`,
    `tools/mcp_oauth_manager.py` 一字没改); 换上上游那份测试, 新旧依赖都 27/27 过。
  · 工具桥 1468 条测试新旧结果一致; 起真的 MCP 服务用新版 mcp 客户端握手, 83 个工具都列得出来。
  · `uv pip check` 会报 hermes 要求 `pyjwt==2.13.0` —— hermes 只在自己新建环境的那一刻跑这个检查
    (`pm/environment.py` 的 `check()`), 不回头查在用的环境, 不会因此拒绝或回滚。
- 以后升 hermes 本体: 新版锁定的版本 >= 覆盖清单里的, 就把那一行删掉。
