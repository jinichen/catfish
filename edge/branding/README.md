# catfish / edge / branding

鲶鱼平台的**用户入口包装层**。员工敲 `catfish` 而不是 `hermes`。

## 解决什么问题

> "我们公司用的是鲶鱼" vs "我们公司用的是 Nous Research 的 Hermes"

入口命令的名字就是品牌的第一面。员工天天敲 `catfish`，心智里这就是鲶鱼平台，不是别人的工具。

跟 `edge/identity/` (SOUL.md) 是一对：
- `identity/SOUL.md` 接管 **agent 自我介绍**（"我是小鲶"）
- `branding/catfish` 接管 **入口命令名**（"敲 catfish"）

## 装

```bash
bash install.sh
```

软链 `catfish` 到 `~/.local/bin/catfish`。改脚本立即生效。

## 用

```bash
catfish                    # 进交互式 hermes（带鲶鱼 banner）
catfish --no-banner        # 跳过 banner
catfish --raw              # 透传给底层 hermes，不做任何包装

catfish status             # 看平台整体健康度
catfish doctor             # 深度自检
catfish version

catfish <hermes 子命令>    # 透传，比如 catfish chat / catfish model / catfish mcp
```

`catfish status` 会一眼看到这些状态：

```
=== 鲶鱼平台状态 ===

[Gateway]            OK / OFF
[Catfish Chrome]     OK / OFF (CDP 9222)
[Local Search]       索引文件数 / 大小 / 类型分布
[Identity (SOUL.md)] catfish 已接管 / 默认 / 缺失
[Hermes Runtime]     版本号
```

新员工 onboarding 第一句话就是"敲 `catfish doctor` 看自己装好了没"。

## 设计

- 纯 bash，不依赖 Python（启动够快）
- 不是 Hermes 包装代理，是**前缀脚本**：拦截"自有 meta 命令"，其他全部 `exec hermes`
- 不引入任何新依赖（curl / python3 这些系统都有）
- 改 `catfish` 脚本立即生效（软链）
- `--raw` 跳过任何包装，调试方便

## 为什么不直接 alias hermes catfish

会破坏环境隔离：
- alias 只在交互 shell 生效，子进程 / launchd / cronjob 拿不到
- 我们的 banner / `catfish status` 这种值需要 enclosing 命令来分发，alias 做不了
- alias 没法承载未来更多自有逻辑（status / doctor / 升级提醒等）

所以选**真实可执行命令**这条路。

## 后续

- [ ] 加 `catfish update` 更新 catfish + 所有 edge 模块
- [ ] 加 `catfish login` 走 SSO（替换 dev-token）
- [ ] 加 `catfish demo` 一键启动 demo 模式（自动补足缺失依赖）
- [ ] Windows `.ps1` 镜像版（有 Windows 员工时再做）
