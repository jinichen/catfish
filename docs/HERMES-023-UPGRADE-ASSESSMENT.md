# hermes v0.22.2 → v0.23.0 升级评估 (2026-11-06)

> 当前: v0.22.2 · commit d923a5eb。目标: v0.23.0「The Hearth Release」(2026-11-05 发布, 发布后第二天评估)。
> 上游自 v0.22.0 起约 10,080 个提交、1,950 个合并 PR、8,720 个文件改动。
> 方法: ① 四块并行读两版源码 (插件补丁 / 安装链路 / Companion 接口 / 工具·微信·定时任务), ② **在临时目录把两个版本都装起来,
> 配同样的规定配置和三个鲶鱼插件真起网关** (独立 HOME / HERMES_HOME, 不碰 ~/.hermes; 模型请求打到本机的假模型服务,
> 记录收到的请求头), 0.22.2 作对照。复现脚本在评估用的 scratchpad (`h023/`)。

## 一句话结论

**不能无缝升级, 现在也不建议升。** 不改就升, Companion 主聊天直接全部失败, 鲶鱼插件整个不加载 (身份、审批红线、记忆闸全没)。
要改的量跟上次 0.21 → 0.22 差不多 (约 2–3 天含实测), 但这次**没有安装链路的大改**, 主要是三处结构变化:

1. **「一个网关管所有会话」**: 所有 API 对话先登记、排队, 再由网关按顺序跑 (`gateway/session_authority.py`、`session_api_turn.py`, 0.22.2 里没有)。
   Companion 自己往 state.db 建的会话被拒; 对话改走新路径后, 我们给主对话注入员工身份的补丁不再经过。
2. **微信适配器从核心挪进内置平台插件** (`gateway/platforms/weixin.py` → `plugins/platforms/weixin/adapter.py`, 没有兼容壳)。
3. **依赖 / 构建**: Pillow 13 强制覆盖、构建后端 setuptools 84.1、uv 0.13.5。

0.22.3–0.22.7 不是只修 bug 的小版本 —— 每个都是上游主干几百个 PR 的合集 (0.22.3 一个就 620 个 PR、4,206 个文件), 升到任何比 0.22.2 新的版本工作量都一样。

## 实施进度 (11/06, 分支 `hermes-v023`, Companion 1.2.78)

鸿波 11/06:「直接考虑 0.23.0, 不要再浪费时间在小版本上」→ 当天开做。每一项都在临时目录的 0.22.2 / 0.23.0 两套真网关上对照验过
(三个鲶鱼插件 + 规定配置; 模型请求打到本机假模型服务, 记录收到的请求头和调用次数)。

| # | 项 | 状态 | 验证 |
|---|---|---|---|
| 1 | Companion 建的会话被拒 | ✅ 新建写 `source='api_server'`, 「是 Companion 的」记附表 `catfish_companion_sessions`; 老会话一次性迁; 列表 / session_check / 插件 P40 认附表 (`companion_sessions.rs`) | 单测; 0.23 上 api_server 来源正常回复 |
| 2 | 插件因微信挪位置不加载 | ✅ 自检两处都认 | 两版都「46 个补丁入口全部生效」 |
| 3 | 主对话丢员工身份头 | ✅ P65 `plugin_api_turn.py`: 包 `admit_api_steps`, 排队时登记 会话 → 员工 / 来源; P2 / P10 按会话 (含父会话) 查 | 两版发给网关的 X-Catfish-User / Source 一模一样 (带会话号 / 不带会话号两种) |
| 4 | 微信补丁打到另一份模块 | ✅ `weixin_compat.py` 从平台注册表取网关真正用的类 (`hermes_plugins.platforms__weixin.adapter`), 没注册就挂在 register 上补; P28 / P56 / P60 / P30 / wechat_push 都改 | 0.23 日志: 三个补丁都打在 `hermes_plugins.platforms__weixin.adapter` 上 |
| 5 | Pillow 13 冲突 | ✅ 打 wheelhouse 合并上游 override; 装依赖 `--no-deps` (清单本是完整闭包) —— Rust 装机、mac 资源、Windows 冒烟三处 | 三个目标的离线资源都打出来了, 装机见下 |
| 6 | setuptools 84.1.0 | ✅ | 同上 |
| 7 | uv 0.13.5 | ✅ `.uv-version` | 同上 |
| 8 | P48 / P53 参数 | ✅ 透传 | 插件测试 |
| 9 | P62 参数 | ✅ 透传 | 插件测试 |
| 10 | 网关有对话时拒绝重启 | ✅ 插件更新 / 专家设置: 被拒不回滚、不 kickstart 硬杀, 后台等对话收尾再试; 网关坏了的兜底重启、换目录前停网关带 `--yes` (只 0.23 带) | 单测 |
| 11 | browser_exec | ✅ 规定配置在 0.23 上关 `browser` 工具集; 中央过滤器无条件丢 `browser_exec` (第二道); SOUL / 截图技能不再提 browser_vision | 单测 / 中央测试 |
| 12 | P50 自动续跑 | ✅ 0.23 上续跑发新一轮 (历史从会话取, 自己的 request_id) | 两版都「打算一句 → 自动续 → 结果」 |

实施时新发现的 (评估里没有):

| 问题 | 不处理会怎样 | 处理 |
|---|---|---|
| 0.23 把审批说明里看不见的字符显成转义, 我们的「⚠️ 每次都要确认」变成 `⚠\ufe0f 每次都要确认` | Companion 认不出 → **删除类审批又出现「始终允许」按钮** (P20 服务端仍拦着永久批准, 但界面跟规则不符) | 插件标记去掉 U+FE0F; Companion 新 / 旧 / 转义三种都认; 实测 0.23 说明以「⚠ 每次都要确认」开头 |
| 0.23 原生审批不带 `pattern_key` | 审批横幅里一个空的标签 | 空的不显 |
| 0.23 输入持久排队, Companion 断线 5 秒后自动重发 | 同一句话跑两遍 | 聊天带 `Idempotency-Key`: 断线重发 / 流卡住重连同 key, 上一轮已结束 (失败 / 截断) 的重试换新 key。实测同 key 发两次模型只调一次 |
| RoomLink 对方 run 新状态 `unknown` | 等满 10 分钟超时 | 按没跑成算, 提示对方看一眼 |
| Word / PPT / Excel 三个技能合成 microsoft-office, 老副本留在机器上 | 技能清单新旧两套重复 | 老的三个加进默认停用 |
| 插件测试: P36 墓碑 ($HOME 兜底) 挪到 `gateway/run_bootstrap.py`; 0.23 多两条 PowerShell 删除规则 | 测试红 | 测试认新位置 (兜底还在, 启动时调); 补中文 |
| `hermes-dep-overrides.txt` 全部被上游追上 | — | 清空 |

已确认不用改 (实测): 原生审批事件 / 回执、「小鲶正在做什么」(0.23 从网关会话登记取得到)、定时任务 P21、catfish-memory。

**离线资源 + 装机 (11/06)**:

| 目标 | 离线资源 | 全新装 | 0.22.2 → 0.23.0 原地升级 |
|---|---|---|---|
| mac arm64 | ✅ 502 MB (SQLCipher 自检过) | ✅ 14.5s, 健康, PIL / sqlcipher3 / 工具桥全部模块导得进, 第二遍走快路径 | ✅ 14.1s, 健康, 员工记忆保留 |
| mac Intel (Rosetta) | ✅ 513 MB (cryptography / greenlet 现编, SQLCipher 自检过) | ✅ 22.6s, 同上 (含 cryptography Rust 二进制真加载) | ✅ 19.1s, 同上 |
| Windows (CI run 42158072479) | ✅ v0.23.0 / Python 3.14.7 / uv 0.13.5 | ✅ 打包后运行时冒烟: 离线装依赖、SQLCipher 自检、工具桥起来健康 (165 个工具) | — (CI 只有冒烟) |

用的是 `hermes_install_native::e2e` 两条忽略测试 (装机 / 原地升级, 注释里有跑法)。升级测试第一遍假失败: 新 Companion 装老归档时把装好的记成新 pin 的 commit,
第二遍以为已是新版; 员工机上是老 Companion 记的老 commit —— 测试照员工机的样子改回老 commit 后真升级, 升级判据本身没问题。

还剩: 真 Companion 上点一遍 (聊天、审批、微信、专家 Bot、换机迁移); Windows 从 0.22.2 原地升级 (要真 Windows 机)。

## 实测结果 (两个版本同样配置真起网关)

| 项 | v0.22.2 (现在) | v0.23.0 |
|---|---|---|
| 鲶鱼插件 catfish-xcatfish-user 加载 | ✓ 全部补丁生效 | **✗ 自检失败, 整个插件不加载** —— 缺的正好是 `gateway.platforms.weixin` 的 3 个目标 |
| 把自检里微信路径改到新位置后 (临时副本) | — | 插件加载; 45 个补丁入口里只有微信的 P28 / P56 / P60 失败 |
| Companion 方式建的会话 (`source='companion'`) 发消息 | ✓ 正常回复 | **✗ 流以 error 结束、正文为空: `permission_denied` (RuntimeStoreError)** |
| 会话来源写 `api_server` 再发 | ✓ | ✓ 正常回复 |
| 主对话发给模型的请求带不带员工身份 `X-Catfish-User` | ✓ 带 | **✗ 不带** (只剩 `X-Catfish-Source`); 起标题这类辅助调用仍带 |
| catfish-memory / catfish-policy 加载 | ✓ | ✓ |

## 必须改的 (按严重程度)

| # | 级别 | 问题 | 证据 | 改法 |
|---|---|---|---|---|
| 1 | **P0** | Companion 建的会话被拒, 主聊天、早安任务对话、手机桥全部失败 | 实测见上; H23 `gateway/session_api_turn.py:197-199`、`session_api.py:176-178,230-231` 只认 `api_server` / `bot_room` 来源; 登记无条件开启 `run_bootstrap.py:794`、`run_runtime.py:177-198`; 我们 `session_write.rs:160` 写死 `'companion'` | 建会话改写 `source='api_server'`, Companion 自己的标记挪到 `catfish_session_metadata` (加 origin 列); `sessions.rs:254` 列表条件跟着改; 老的 `companion` / `companion-migrated` 行一次性迁移。**0.22.2 不看来源, 这一条可以先在 0.22.2 上单独发**, 升级时就少一个变量 |
| 2 | **P0** | 鲶鱼插件不加载 → P0–P64 全关 (员工身份、审批红线 P20/P59、RoomLink 红线 P49、记忆闸 P42、工具调用兜底拦截都没了) | 实测; 我们 `plugin_verify.py:129,140,141` → `__init__.py:116-123` raise | 自检的 3 个微信目标改到 `plugins.platforms.weixin.adapter` |
| 3 | **P0** | 主对话不带员工身份 `X-Catfish-User`: 中央对服务令牌要求带这个头 → 拒绝或记错人 | 实测; API 对话不再走 `APIServerAdapter._create_agent`, 改由 `run_api_turn` 写排队记录、后台 `_drain` 以内部消息经 `GatewayRunner._handle_message` 跑 (H23 `api_server.py:3686-3688`、`session_api_turn.py:315-384`、`session_ingress.py:318-348`) → P5 / P6 / P11 (身份属性、请求头、按界面选的模型) 不再触发; 员工身份放在上下文变量里, 只有建排队任务那一次请求的值会被继承 | 身份改挂在新路径上: 排队记录 / 内部消息带上员工身份, 在网关跑这一轮时设好再建 agent。**要重新设计并逐条验证** (P1/P2/P3 身份、P42/P44 来源闸、P11 选模型都在这条线上) |
| 4 | **P0** | 微信补丁打不到网关真正用的类: 内置平台插件按 `hermes_plugins.<名>` 延迟加载, 直接 `import plugins.platforms.weixin.adapter` 得到的是**另一份模块**, 打补丁静默无效 | H23 `plugins/platforms/weixin/__init__.py:1`、`hermes_cli/plugins_loader.py:789-845`; 上游给 signal / yuanbao 修过同样的问题 (`tests/gateway/test_platform_plugin_canonical_class.py`), 微信没修 | P28 中文化 / P56 连回「1」不被吞 / P60 找专家: 从 `gateway.platform_registry` 取真类 (延迟加载后) 再打补丁; P30 扫码绑定、tool-bridge `wechat_push.py` 只用模块函数, 改导入路径即可。实测要看到各自的 "patched" 日志 |
| 5 | **P0 (打包)** | Pillow 13 跟 browser-harness 0.1.13 钉死的 pillow 12.3.0 冲突; 上游靠 `[tool.uv] override-dependencies` 压过去, 我们打 wheelhouse 时只传自己的覆盖文件 → 解析失败, 包打不出来 / 员工机补装失败 | H23 `pyproject.toml:141,650-661`; 我们 `build_hermes_wheelhouse.py:251,263`、`hermes_install_native.rs:239` | 打 wheelhouse 时合并上游 override; 安装侧加 `--override` 或 `--no-deps` (清单本来就是带 sha256 的完整闭包) |
| 6 | P1 | 构建后端 `setuptools==84.1.0` (我们核对写死 83.0.0, 打包当场停) | H23 `pyproject.toml:583`; 我们 `build_hermes_wheelhouse.py:50,212` | 改两处 |
| 7 | P1 | uv 0.12.3 → 0.13.5, `uv.lock` 格式 3 → 5 | H23 `pm/lock.json`; 我们 `.uv-version` | 升 `.uv-version` (0.12.3 读新锁文件未验证, 升最稳) |
| 8 | P1 | P48 / P53: 上游改成 `resolve_underlying_call(args, session_tool_names)`, 我们的包装只收一个参数 → 经 `tool_call` 调的鲶鱼工具全部报 TypeError | H23 `model_tools.py:745`、`tools/tool_search.py:579`; 我们 `plugin_deferred_tool_guard.py:258-262`、`plugin_bridge_promoted.py:99-101` | 包装改 `*a, **kw` 透传 |
| 9 | P1 | P62: 上游改成 `exhausted_copy(..., error=...)`, 我们的中文化包装不收 → 「重试用完」那条路报错 | H23 `agent/turn_recovery.py:1266-1268`; 我们 `plugin_failure_copy_zh.py:60` | 加 `**kw` |
| 10 | P1 | 有对话在跑时 `hermes gateway stop/restart` 直接拒绝 (没终端时 exit 1, 要 `--yes`); Companion 6 处会撞: 切模型 / 技能变更报错, 专家 Bot 失败后**回滚配置**, 装机换目录时没停住网关就继续, mac 上失败后回退 `launchctl kickstart -k` 硬杀正在跑的对话 | H23 `hermes_cli/gateway_lifecycle_guards.py:97-129`; 我们 `codex_gateway.rs:140`、`expert_bots.rs:155-159`、`experts_local.rs:174`、`hermes_install_gateway.rs:91`、`hermes_plugin.rs:397` | 按版本区分: 0.23 先等空闲再 `restart --yes`, 被拒就标「待重启」稍后重试, 不回退硬杀、不回滚 (0.22 不认 `--yes`) |
| 11 | P1 安全 | `browser_exec` 成了默认浏览器工具 (主机上跑任意 Python); 我们靠中央过滤器丢 terminal 来禁 shell, 但 browser_exec 只在同一批工具里有 `catfish_browser_*` 时才被丢 → tool-bridge 没连上时会漏给模型 | H23 `toolsets.py:17`、`browser_use_cli.py:275-277`; 我们 `tools_sanitizer.py:410-418` | 规定配置给主 profile 也写 `disabled_toolsets: [browser]` (鲶鱼不用 hermes 自带浏览器); 过滤器无条件丢 `browser_exec` |
| 12 | P1 | P50 自动续写: 每次续写用同一个 request_id 再调, 排队登记按 request_id 去重 → 大概率被拒或拿回上一次结果 | H23 `session_api_turn.py:194,217-233` | 续写改发新一轮 (新 request_id); 实测 |

## 要实测确认的 (代码看不准)

- P15.2 Companion 审批按钮 (`/v1/sessions/{sid}/approval`) 在新的「排队 + 代次」审批下还能不能对上 —— 原生 `approval.request` / `POST /v1/runs/{id}/approval` 字段没变。
- P44 进度登记: 新路径不传 `agent_ref`, 「小鲶正在做什么」可能一直空。
- P21 定时任务跟随选择器的模型: `run_job` 改成转交网关再调一次, 包装应仍生效, 看日志。
- Companion 直接往会话里补写的消息 (主动搭话开场白、早安任务对话补写) 进不进下一轮上下文 (`store/ui.ts:159`、`useTaskChat.ts`)。
- 同一会话连发两条: 现在排队顺序执行, 第二条要等, 可能撞 Companion 的空闲超时; 停止按钮还能不能停住。
- 聊天请求没带 `Idempotency-Key`: 0.23 会把输入持久排队, 网关重启后再跑一次; Companion 出错 5 秒自动重发一次 → 可能执行两遍。建议每条消息生成固定的 key。
- RoomLink 远端 run 新增 `unknown` 状态, 我们不认, 会等到 10 分钟超时。

## 不受影响 / 顺带变好的

- **安装戳 / 不自更新**: 没有 `.git` 或 `updateMechanism != self` 时照旧不自更新; 写安装戳的脚本逐字节没变。「功能挪出核心自动装插件」受 `allow_lazy_installs: false` 管, 不会联网。
- **规定配置的每个键**都还有效, 默认值没变; 新加的 `webapp.enabled`、`plugins.auto_apply` 默认关 (建议也写进规定配置, 防以后默认改)。`sessions.auto_prune` 0.23 默认改回 false。
- **审批**: `approval.request` 事件字段、回执格式、`hermes.status`、`reasoning_content` 都没变。P20 反而更稳 (每次检查都重读永久名单, 走我们的包装)。
- **state.db**: 结构版本 31 → 32 **只新增**列和表, 我们自加的 `deleted_at` 和索引不受影响。
- **定时任务**: 任务文件、`cron create` 参数兼容; 补跑策略默认跟 0.22 一样。
- **Intel Mac**: 新增依赖都有三个平台的 cp314 轮子; cryptography / greenlet 照旧现编。Python 侧没有新的系统版本要求 (macOS 13 只是上游 Electron 桌面端的要求, 我们不带)。
- **浏览器**: 教学填密码、鲶鱼自己的 `catfish_browser_*` (Playwright + 自带 Chromium) 不依赖 hermes 自带浏览器工具。只有 SOUL.md 和截图技能里还提到 `browser_vision`, agent-browser 自检和 CI 打包可以删。
- **端口**: API 仍 8642, Webapp 关着不占固定端口。
- 内置技能 58 → 44, Office 三个合成 `microsoft-office`; 员工机上的旧副本不会自动删, 建议加进默认停用名单。

## 能退役 / 可以换成上游新接口的

- 没有明确能直接删的补丁。候选: P61 (上游有可选的提前压缩 `compression.prepare_ahead`, 要对比)、P40 (「一个会话一个写入者」后可能不再需要)。
- 新插件接口值得用: `pre_llm_call` 能拦整轮 (现在「插件没装好就拒绝服务」只拦得住工具调用, 纯文字回合拦不住); `on_session_delete` 可清理会话登记。

## 建议

1. **先在 0.22.2 上发第 1 条** (会话来源改 `api_server` + 老会话迁移): 0.22.2 不看来源, 对现网无害, 升级时少一个变量。
2. **等 0.23.x 第一个修正版**再动手 (0.23.0 昨天才发; 上次 0.22.0 发布后几天内就出了 0.22.1 / 0.22.2)。
3. 动手时按块做, 每块在 0.23 测试网关上真跑 (这次的临时目录测法可以直接复用): 插件 (2、3、4、8、9、12) → 打包 (5、6、7) → Companion (1、10、11) → 全量回归 (微信中文、审批按钮、员工身份头、定时任务、专家 Bot、RoomLink、换机迁移)。
4. 预计 2–3 天开发 + 实测, 再加三个安装包的离线装机验证和从 0.22.2 原地升级一遍; 动插件 → Companion 版本号、打包都要走一遍, 中央不用动。
