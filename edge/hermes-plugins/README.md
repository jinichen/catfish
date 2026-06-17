# catfish / edge / hermes-plugins

鲶鱼自己开发的 Hermes 插件目录.

## 现状 (P3.5.17, 6/17 鸿波)

**catfish-autocompress 已退役** — hermes 自带 ContextCompressor 已 cover preflight
压缩 (`~/.hermes/config.yaml` 里 `context.engine: compressor` + `compression.threshold: 0.5`).
catfish-autocompress 子目录源码早已删 (`./catfish-autocompress/` 目录在仓库里不存在).

### 鸿波 6/16 撞 304K 不压缩的真因

不是 hermes 自带 compressor 不行 — 是 **hermes auxiliary_client (压缩 summary 调用) 调
catfish-gateway 时缺 `X-Catfish-User` header**, 触发 gateway 400, 然后 hermes 压缩
"Further summary attempts paused for 60 seconds". 长任务永远没机会压缩.

P3.5.17.b 修在 `catfish-gateway/src/catfish_gateway/auth/__init__.py:resolve_effective_user_email`
— hermes-cli service token 缺 header 时 fallback 到 sub (= `client:hermes-cli`), 不报 400.
hermes 自带压缩链路打通.

### 现在的 catfish-memory 还在用这个目录

`catfish-memory/` 还是 active plugin (跨 session 记忆), 走 `install-catfish-memory.sh`.
不受 catfish-autocompress 退役影响.

### 老员工机器怎么清

跑 `bash edge/hermes-plugins/uninstall.sh` —
1. 删 dangling 软链 `~/.hermes/hermes-agent/plugins/context_engine/catfish-autocompress`
2. `~/.hermes/config.yaml` 里如果还写 `engine: catfish-autocompress` 改回默认 `compressor`

跑完重启 hermes, ContextCompressor 自动接管.

---

## 历史: catfish-autocompress (DEPRECATED, 保留作 git blame 用)

**自动上下文压缩**，在 Hermes 的 ctx 占用超过阈值时自动触发压缩（调用内置 `/compress` 的底层逻辑）。

### 为什么做

Hermes 内置 `ContextCompressor` 的默认触发阈值是 **75%**。对 128K 的 Qwen 3.5，意味着 **96K 才开始压**。员工在以下场景会被坑：

- **浏览器自动化业务流**：snapshot 一次好几 K tokens，10 个 snapshot 就 50K+，一会儿就 96K，压缩一次掉信息多
- **合规平台批量查询**：连续十几条 tool call + 用户列表 snapshot 累积很快
- **合约 / 长文档分析**：prompt 一开始就几 K

所以鲶鱼默认降到 **70%**（约 90K 触发），更早进入滚动压缩节奏，单次压缩量小，信息损失小。

### 实现思路

**超薄子类**：

```
catfish-autocompress （60 行）
    └── 继承 ContextCompressor （1000+ 行，Hermes 官方压缩逻辑）
            └── 继承 ContextEngine （150 行 ABC）
```

我们只改 `threshold_percent`（0.75 → 0.70），**不重造压缩算法**。
父类做什么我们做什么，只是"什么时候压"调早一点。

### 配置

员工可以用环境变量调阈值：

```bash
export CATFISH_COMPRESS_THRESHOLD=0.65    # 更激进，60% 就压
export CATFISH_COMPRESS_THRESHOLD=0.80    # 更懒，80% 才压
# 默认 0.70
# 合法范围 0.10 ~ 0.95
```

### 装卸

```bash
# 装（软链到 Hermes 目录 + 改 config.yaml + 自检）
bash edge/hermes-plugins/install.sh

# 卸（把 config.yaml 改回默认 + 删软链）
bash edge/hermes-plugins/uninstall.sh
```

幂等，`--yes` / `-y` 跳过所有确认。

### 验证

装完重启 hermes，启动日志里会看到：

```
[INFO] catfish.autocompress: catfish-autocompress 启用：threshold=70% 
    (prompt_tokens 达到 context_length × 0.70 时自动压缩)
```

跑一会儿 Hermes 后，gateway 日志里 `prompt_tokens` 应该**周期性降下来**而不是一路涨。Hermes 状态栏（`/sb` 显示）的 ctx 利用率也会在 70% 附近振荡，而不是一路冲到 95%+。

触发压缩时 catfish 层会打一条 info：

```
[INFO] catfish.autocompress: 触发自动压缩：prompt_tokens=89600 context_length=128000 
    (70% >= 70% 阈值) compression_count=3
```

`compression_count` 自增说明压缩真的发生了。

### 为什么不做 gateway 侧自动压缩

已经论证过（CHANGELOG 2026-04-24 那一段）：

- Gateway 是 stateless HTTP 代理，压缩需要**语义理解**（哪些 message 是工具结果可丢、哪些是当前任务要留）
- Gateway 层压缩会**破坏 Anthropic prompt caching**（Hermes 明确警告过）
- 正确分工：**Gateway 监测 + 告警 + 推路由，Agent 层做压缩**

这个插件就是 Agent 层实现。

## 目录结构

```
edge/hermes-plugins/
├── README.md
├── install.sh
├── uninstall.sh
└── catfish-autocompress/
    ├── __init__.py          # 主逻辑，约 80 行
    └── plugin.yaml          # metadata
```

## 未来

当有第二个 Hermes 插件时，考虑：

- 通用的 memory provider（catfish-knowledge-memory 之类，对接内部知识库）
- 特定场景的 tool plugin（比如自动错误分类 + 重试）

那时这个目录就会从"只有 autocompress"变成完整插件集合。
