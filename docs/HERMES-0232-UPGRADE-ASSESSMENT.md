# Hermes v0.23.2 升级评估 (2026-11-17)

> 现钉: v0.23.1 (`3b421578`)。候选: v0.23.2 (`ae2a81cc`, 2026-11-16 发布)。
> 结论: **兼容性风险低, 可以升**。要做的改动只有 17 条危险命令的中文说明; 其余工作是按老流程重打 Hermes 离线包、跑离线安装 / 升级 e2e 和 Windows MSI。

## 这个版本是什么

名义上是补丁版, 实际是 v0.23.1 以来的大合集: 1,989 个合并 PR、7,918 个提交、5,211 个文件 (+239,733 / −22,788)。
上游这次只写了 7 条修复 (都是 Desktop / TUI / Telegram 的), 完整说明要等 v0.24.0。说明里点名、没展开的: 网关重启或崩溃后
子任务能保留并接着对话 (durable subagents)、`hermes update --force`、新插件接口 (`ctx.fork_session` / `ctx.redact_text()` /
`ctx.register_idle_task`)、定时任务每条单独的选项、「模块搬家」的兼容层、macOS 包变小 (Chromium 首次使用才装)。

## 查了什么、结果

方法沿用 0.23.0 / 0.23.1 那套 (scratch 里两个 tag 各建 venv, `HERMES_ROOT` 指过去跑)。

| 项 | 怎么查 | 结果 |
|---|---|---|
| 插件对 Hermes 内部的引用 | 语法树抽出 3 个插件 (+ hermes-fork) 里所有 import、在导入的类 / 模块上取的属性、`setattr`/`getattr`, 共 162 处; 两个版本里真 import、逐个 `hasattr` | **0 处在 0.23.2 里消失**。两边都「缺」的 12 处是插件自己往 Hermes 类上加的方法 (本来就不在 Hermes 里) |
| 真实网关测试 | `test_delegation_on_real_hermes` / `test_cron_patches_on_real_hermes` / `test_weixin_resend_on_real_hermes` (起真网关 + 假模型) | 两个版本都 **26/26 通过** (委派身份 / 审批、定时任务补丁、微信补发) |
| 插件全量测试 (`HERMES_ROOT` 指向各版本) | catfish-xcatfish-user 801 项、catfish-memory 508 项 | xcatfish-user: 0.23.1 全过; **0.23.2 挂 1 项** —— 见下「要改的」。memory 两边同挂 1 项 (`test_树外那条链在且指得对`, 要求插件装在树外固定位置, 临时环境没装, 跟版本无关) |
| Python 依赖 (`uv.lock`) | 两个锁文件逐包比 | **完全相同** (354 个包, 版本一致) —— wheelhouse 不用动, 原生库不用重签 |
| 浏览器 / Chromium | playwright 版本、`tools/browser_launcher.py` / `browser_session.py` | playwright 两边都钉 1.64.0, 启动器一行没改 → 我们打进包的 chromium-embed 照用。「Chromium 首次使用才装」说的是上游 Electron 桌面版, 不是 Python 版 |
| 配置版本 | `_config_version` | 两边都是 53, 不用迁移 |
| 会话库 (Companion 直接读 `state.db`) | `hermes_state*.py` 里的建表 / 加列 / 索引 | 只**新增**一张 `resumable_subagents` (durable subagents 用); sessions / messages 等原有表不变 |
| Hermes API 路由 | gateway 下所有 `/v1/` `/api/` `/health` 路径 | 两边都是 41 个, 一个没少。Companion 用到的其余路径是我们自己的 (插件加的 `/api/catfish/*`、定时任务暂停 / 恢复; 中央网关的 `/v1/hub`、`/v1/metering`、`/api/quota`…) |
| 网关启动参数 | `hermes gateway run --help` | `--external-supervisor` / `--force` / `--replace` 都在 |

## 要改的

- **17 条新的危险命令要配中文说明** (`catfish-xcatfish-user` 的 `DANGER_ZH`; `test_p55_wechat_chinese_sweep` 就是查这个的,
  不配的话微信 / 卡片审批里员工看到英文)。0.23.2 新识别的: netcat / socat 反弹 shell; docker / podman 删容器、删卷、prune;
  Windows 注册表写入 (reg add / regedit /s / setx / Set-ItemProperty …); 删改 Hermes 的 home、会话库、凭据, sqlite3 改会话库,
  `hermes config set` 改审批设置; 往 SSH / 凭据 / shell 配置目录建文件、建链接。审批变严对我们是好事。

## 升级要做的 (按老流程)

1. 钉新提交: `edge/companion-app/.hermes-git-commit` → `ae2a81cc…`, `hermes_pinned_tag` → v0.23.2; 补 17 条中文说明。
2. 重打 Hermes 离线包 (mac 两个架构 + Windows), 跑离线安装 e2e 和就地升级 e2e
   (`CATFISH_TEST_RUNTIME` / `CATFISH_TEST_RUNTIME_OLD` + `CATFISH_TEST_OLD_COMMIT`)。
3. Windows: 分支上跑 `build-windows-msi.yml` (离线安装 + SQLCipher + tool-bridge 冒烟)。
4. 本机网关换成新版跑一天, 看 agent.log 里插件加载 (`delayed install ✓`)、出卡、会中小鲶、群聊 @小鲶。
5. 以后可以用上的: durable subagents (我们常重启网关, 委派出去的任务现在会断)、`ctx.register_idle_task` (后台活)。

工作量: 代码改动很小; 主要是重打包和 e2e, 大约半天到一天。
