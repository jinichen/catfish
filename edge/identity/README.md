# catfish / edge / identity

鲶鱼平台的 **Agent 身份层** —— 让小鲶（Hermes 跑出来的对话）以"鲶鱼平台 AI 副手"的身份对话，不再以 Hermes / Nous Research 字样出现。

## 解决什么问题

Hermes 默认 banner / 自我介绍带 Nous Research 字样。对客户 demo / 企业部署 / 创业路演来说，这个第三方品牌出现是不专业的。

第一刀：**SOUL.md 接管 Agent 身份**。Hermes 0.10 自带这个机制：`~/.hermes/SOUL.md` 完全替换 agent 默认 personality。

## 装

```bash
bash install.sh
```

会做：
- 备份现有 `~/.hermes/SOUL.md`（如果有）到 `~/.hermes/SOUL.md.before-catfish`
- 软链 `catfish/edge/identity/SOUL.md` 到 `~/.hermes/SOUL.md`
- 软链方式让你改 SOUL.md 立即生效（不用每次重装）

幂等。重复跑安全。

## 卸

```bash
bash uninstall.sh
```

会做：
- 删软链
- 如果有备份就还原

## 验证

```bash
hermes
# 进去发：你是谁？
```

预期回复：以"我是小鲶"开头，不出现"Hermes"/"Nous Research"/"AI 助手"字样。语气直接、不堆套话。

## 这只是品牌化的第一刀

| 层级 | 状态 |
|------|------|
| **Tier 1: SOUL.md 重写身份** | ✅ 这里就是 |
| Tier 2: `catfish` 命令包装层 | TODO |
| Tier 3: Fork Hermes 改 ASCII art / 主题 | TODO（依赖 license：MIT 已确认放行） |
| Tier 4: 自己写 Web/TUI 双形态 | 中期目标 |

## SOUL.md 内容设计要点

- **身份**：小鲶 / Catfish，不是 Hermes
- **五条哲学**：边缘主权、中央最小、礼物经济、红线不审查、平权不反人
- **语气**：直接、技术导向、不堆套话、不卑不亢
- **工具偏好**：catfish-* 优先（local-search MCP / browser-task / autocompress）
- **创业语境**：知道这是创业产品，理解员工赶 demo / 跟客户聊定价 / 投资人提问的场景
- **隐私边界**：明确什么数据出员工本机什么不出

修改 `SOUL.md` 不影响 Hermes 别的功能，是最低风险的品牌化手段。

## 相关

- `LICENSE`（Hermes Apache）允许 fork+rebrand，所以未来 Tier 3 也能合法做
- `catfish-design.md` 是产品理念权威，SOUL.md 只是身份语言层的提取
