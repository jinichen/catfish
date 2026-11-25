# Hermes v0.23.4 升级评估 (2026-11-25)

> 现钉: v0.23.3 (`caafe538`)。候选: v0.23.4 (`8fc03744`, 2026-11-24 发布)。
> 结论: **可以直接升, 插件不用改**。风险低, 主要工作是按老流程重打离线包。

## 这个版本是什么

名义补丁版, 实际是 v0.23.3 以来的合集: 229 个 PR、1,122 个提交、1,316 个文件 (+96,873 / −6,703)。上游细节等 v0.24.0 再写。
但这次比 0.23.3 温和得多: 新增代码大头是 `hermes gateway migrate --backends` (把旧 serve/dashboard 后端迁到网关)、
`hermes plugins config`、`hermes dashboard password`、桌面版流式性能、浏览器保险箱 (`browser_vault_tool`)、发布脚本。
我们用到的核心路径基本没动: `gateway/platforms/api_server.py`、`tools/approval.py`、`tools/delegate_tool.py`、
`gateway/session.py`、`hermes_state.py` **一字未改**; `gateway/run.py` 只改了 13 行 (启动时读 .env 的方式, 见下)。

## 查了什么、结果

方法沿用 0.23.2 / 0.23.3 那套 (scratch 两个 tag 各建 venv, `HERMES_ROOT` 指过去跑)。

| 项 | 结果 |
|---|---|
| 插件引用的 Hermes 内部符号 (AST 抽 186 处, 两版真 import + 逐级 hasattr) | 两版都是 172 处找得到, **没有一处只在新版缺**。其余 14 处两边都「缺」, 跟以前一样是插件运行时自己加的 |
| 真实网关测试 (委派 / 定时任务 / 微信补发 / P12·P40 真会话库 / P55 / P59) | 两版都 **137/137** |
| 插件全量单测 | xcatfish-user 两版都 792 过 (53 项跳过全是同一条 pyflakes 检查); memory 两版都 500 过 + 同样 1 项 (树外链接, 跟版本无关) |
| `audit_hermes_compat.sh` | 两版都 97 过 / 0 失败; 唯一差异: `plugins/memory/__init__.py` 里 loader 引用 10 处 → 11 处 (多了一处, 不影响) |
| Python 依赖 (`uv.lock`) | **逐字节相同** (355 个包)。wheelhouse 内容不变 |
| 构建后端 | 不变 |
| 会话库 (Companion / tool-bridge 直接读 `state.db`) | 建表 / 加列 / 索引语句 196 条, 两版完全一样 |
| 配置版本 | 54 → 54, 不变 |
| Hermes API 路由 | 一致 |
| 网关控制命令 (Companion 直接调的 `session.resume` / `prompt.cancel` / `prompt.resolve_unknown` / `approval.respond`) | 都在; 只新增 (`client.activity` 等), 没删没改名 |
| 网关启动参数 | `--external-supervisor` / `--force` / `--replace` 都在 |
| 危险命令清单 (`tools/approval.py`) | 文件没变, 没有新的要翻中文 |

## 新行为, 对我们的影响

- **启动读 .env 改成 `load_launch_home_dotenv`**: 只有被插件标成「在别处运行」的 profile (新功能 `profiles_run_elsewhere`) 才跳过
  `~/.hermes/.env` 和 config.yaml 导出。我们没用这个功能, 行为跟原来一样 (真实网关测试也证明 API_SERVER_* 照常读到)。
- **新模块 `gateway/status_report/`**: 网关定时向一个 URL 汇报状态。**默认关**, 只有启动环境里设了 `GATEWAY_STATUS_REPORT_URL`
  才开; 我们不设, 不会有新的外发。
- **重试去重忽略客户端标记** (`persistence/authority/vocabulary.retry_input`): 同一请求重发时, 不再因为发送端版本号不同被当成新请求。
  对我们是好事 (Companion 升级前后的重发不会重复排队), 老数据的指纹不变。
- **新插件钩子 `on_session_prompt` / `on_session_prompt_settled`**: 网关开 / 关一个需要人回答的提示 (审批、澄清、sudo…) 时通知插件。
  将来可以拿它替掉 P63「待审批」的轮询 (1.2.259 做的), 这次不必动。
- `hermes_state.SessionDB` 仍是「已废弃, v0.27.0 删除」; 我们的 P12 / P40 在 0.23.3 时已改走 `persistence`, 不受影响。

## 升级要做的

1. 钉 v0.23.4 (`8fc03744`): `.hermes-git-tag` / `.hermes-git-commit` / `.hermes-target-version`。
2. 按老流程重打 Hermes 离线包 (mac 两个架构 + Windows)、离线安装 / 就地升级 e2e、Windows MSI。
3. 装上后看本机 `agent.log` 的 `delayed install ✓`, 再看出卡、审批卡、群聊 @小鲶 各一次。

工作量: 代码零改动, 主要是重打包和 e2e, 约半天。

## 评估时踩到的

- 本机 uv 配了「只装 3 天前发布的包」, scratch venv 装 `a2ui-agent-sdk==0.12.1` (11/23 发布) 被挡; 加 `--exclude-newer <今天>` 才装上。
  正式打包走 wheelhouse 不受影响 (1.2.264 已打进去)。
- scratch venv 要额外装 `pytest-asyncio`, 否则 memory 插件 20 项 async 测试会「失败」(两版一样, 跟 Hermes 无关)。
- GitHub `git clone` 当时很慢 (13 分钟 10MB), 改用 codeload tarball (14MB/s) + `/tmp/catfish-hermes-src-<commit>.tar.gz` 缓存。
