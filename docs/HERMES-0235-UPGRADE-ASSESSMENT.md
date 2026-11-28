# Hermes v0.23.5 升级评估 (2026-11-28)

> 现钉: v0.23.4 (`8fc03744`)。候选: v0.23.5 (`a428f534`, 2026-11-27 发布)。
> 结论: **风险低, 插件不用改, 可以直接升**。要做的是按老流程重打离线包, 装上后看一眼运行日志。

## 这个版本是什么

名义补丁版, 实际是 v0.23.4 以来的合集: 338 个 PR、1,262 个提交、1,146 个文件 (+57,989 / −5,346), 细节等 v0.24.0。
新增大头是桌面版 / 仪表盘 / 插件目录 / profile 改名搬家 / 网关迁移命令, 跟我们关系不大。
我们直接用到的核心文件 **一字未改**: `gateway/platforms/api_server.py`、`api_server_store.py`、`tools/approval*.py`、
`gateway/session_api_turn.py`、`session_display.py`、`agent/conversation_compression.py`、微信适配器、`cron/jobs.py`。

## 查了什么、结果

| 项 | 结果 |
|---|---|
| Python 依赖 (`uv.lock`) | **逐字节相同**, wheelhouse 不用变 |
| 插件 import 的 Hermes 符号 (133 处, 两版真 import) | 两版都 129 找得到; 缺的 4 处两边一样 (微信旧路径, 插件有兼容层) |
| 插件对 Hermes 对象的属性访问 / 打补丁目标 (106 处) | 两版都 94; 缺的 12 处两边一样 (插件自己加的方法 / 老版本兜底) |
| 插件包着的 21 个 Hermes 函数, 逐个比函数体 (AST) | 20 个**完全没变**; 只有 `cron.scheduler_delivery._resolve_delivery_targets` 多了一个可选参数 `skipped`, 微信补发照旧调用, 不受影响 |
| xcatfish-user 全量测试 (含真网关: 委派 / 定时 / 微信补发 / P12·P40 真库 / P55 / P59 / 技能门禁) | 两版都 **898/898** |
| catfish-memory 测试 | 两版都 500 过 + 同一条失败 (树外链接, 跟版本无关, 以前就有) |
| 真网关运行日志对照 (三个鲶鱼插件 + 工作台两轮对话) | 插件装载日志去掉时间 / 会话号后**两版一样** (119 行); 所有补丁都 patched ✓; 启动到 `delayed install ✓` 都是 6.1 秒; 没有新的 失败 / Traceback |
| 员工消息 `display_kind` (P66 那次的坑) | 两版一样, 没有被标成 internal_notification |
| 会话库建表 / 加列 / 索引 | 两版 119 条完全一样 |
| 网关控制命令 (tui_gateway 148 个) / API 路由 | 一样 |
| 启动参数 `--external-supervisor` / `--force` / `--replace` | 都在 |
| 危险命令清单 | `tools/approval*.py` 没变, 没有新的要翻中文 |
| 测试过程中本机真网关 | 前后 PID 一样, 没碰 |

## 新行为, 对我们的影响

- **网关自带「空闲时后台压缩」, 现在所有入口都会跑** (`gateway/run_watchers_idle.py`): 只在 `compression.idle_compact_after_seconds > 0`
  时开。默认 0, 我们的规定配置也没设 → 不开, 不会跟 P61 重复压。**以后不要把它打开**, 否则一个会话会被两边各压一次。
  (将来可以评估用它替掉 P61, 这次不动。)
- **默认插件预检** (`default_plugins_ensure`): 只在桌面版 / 新建 profile 时跑; 我们的规定配置 `security.allow_lazy_installs: false`,
  不会联网装东西。
- **定时任务投递**: `deliver: all / origin` 碰到平台插件没加载时, 现在会把「哪个家频道没送到」记成错误 (以前是悄悄跳过)。是好事。
- **给记忆插件的对话记录去掉了 `display_metadata` 里的 display_text / plugin_card**: catfish-memory 不读这两个字段, 不受影响。
- **终端工具说明改短**, 「读写文件请用 read_file 等」那句只在会话有文件工具时才加。原来就有这句, 行为基本不变。
- **`get_hermes_home()` 不再对 override 展开 `~`** (改在设置时展开): 插件没调 `set_hermes_home_override`, 不受影响。
- 新的插件 API (这一轮从哪来、会话怎么建的、网关静默助手): 以后可以用来简化 P66 / P63, 这次不必动。
- profile 改名 / 搬家时插件跟着走: 我们只用默认 profile, 不涉及。

## 没覆盖到的 (装上后看)

- **P61 空闲压缩在临时网关里没触发** (假模型的 token 数没进到 `last_prompt_tokens`, 是试验台的问题): 它依赖的代码
  (API server、压缩锁、读会话、会话卫生 agent) 这版一字未改, 本机 0.23.4 今天 17:34 也真压成功过 (88,537 tokens, 38 → 10 条)。
  升级后看 `agent.log` 里有 `P61 空闲压缩 … 压好了`。
- Windows 只能靠 MSI 工作流 (离线安装 + SQLCipher + tool-bridge 冒烟) 验证。

## 升级要做的

1. 钉 v0.23.5 (`a428f5344f777ccafe389c79d5b09384abef1437`): `.hermes-git-tag` / `.hermes-git-commit` / `.hermes-target-version`。
2. 按老流程重打 Hermes 离线包 (mac 两个架构 + Windows), 离线安装 / 就地升级 e2e (记下真网关 PID 前后), Windows MSI。
3. 装上后: `agent.log` 看 `delayed install ✓`、没有插件 `失败` / `Traceback`; 网关日志 `hermes:aux:*` 没有 `content=0`;
   `select display_kind,count(*) from messages where role='user' group by 1`; 出卡、审批卡、群聊 @小鲶 各一次; 等一次 P61。

工作量: 代码零改动, 主要是重打包和 e2e, 约半天。
