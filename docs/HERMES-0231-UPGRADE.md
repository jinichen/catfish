# hermes v0.23.0 → v0.23.1 升级记录 (2026-11-10, Companion 1.2.139)

> v0.23.1 (commit `3b421578`) 是上游自 v0.23.0 以来约 2,604 个提交 / 618 个合并 PR 的合集,
> 不只是发布说明里那条「插件目录超过 2 MiB 读不了」(那条对鲶鱼无影响: 鲶鱼的三个插件不经插件市场安装)。

## 结论

鲶鱼这边**不用改代码**, 只换版本钉 (`.hermes-git-tag` / `.hermes-git-commit` / `.hermes-target-version`)。

## 怎么验的

两个版本都装进临时目录 (各自独立 venv / HOME / HERMES_HOME, 不碰 ~/.hermes, 没有微信凭据),
配同样的规定配置和三个鲶鱼插件真起网关, 模型请求打到本机假模型服务记录请求头, 0.23.0 作对照。

| 检查 | 结果 |
|---|---|
| `audit_hermes_compat.sh` | 两版输出完全一样, 无失败 |
| 三个插件的测试 (用各自版本的 venv) | 两版失败项逐条相同 (临时环境缺本机才有的东西), 无新增 |
| 真 hermes 行为测试: 定时任务 P21 / P26 / P59、微信补发 | v0.23.1 上 56 项全过 |
| 插件加载日志 | 两版 80 行完全一样, 无警告 |
| 主对话 / 流式 / 后台起标题的 X-Catfish-User / Source | 一样 |
| 记忆注入、工具数 (17) | 一样 |
| 删除类命令审批 (`rm -rf ~/catfish-del-test`) | 两版都拦下, 说明都是「⚠ 每次都要确认」, 事件内容除 run id 外一样, 目录没被删 |

## 上游改了、我们补丁碰得到的

补丁涉及的 23 个 hermes 模块里 13 个有改动。最大的是 `agent/auxiliary_client.py` (±590 行) 和
`hermes_cli/plugins.py` (插件加载器 −836 行); 上面的实测覆盖了这两块 (后台调用的身份头、三个插件照常加载)。
微信适配器只改了代理出错时的报错措辞, `session not ready` 原话不变 (微信补发靠它识别)。

依赖: 去掉 tenacity (鲶鱼不用); 语音转写的 av 钉 18.1.0。`override-dependencies` 没变,
`hermes-dep-overrides.txt` 仍为空。

## 临时环境的坑 (下次评估省时间)

- `API_SERVER_KEY` 少于 16 位, API 服务不起 (只有一句 "No messaging platforms enabled")。
- 临时 venv 要装 PyYAML 等 `hermes-extra-packages.txt` 里的包, 否则 catfish-policy 自己停掉。
- 插件日志在 `logs/agent.log`, 不在 gateway.log。
