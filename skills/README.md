# Hermes Skill 包

> **状态**：🟡 Week 3 起陆续开发
>
> **定位**：按 [agentskills.io](https://agentskills.io) 标准写的技能，agent 可以加载和复用。

---

## 子目录职责

| 目录 | 用途 |
|---|---|
| `starter/` | 新员工默认订阅的通用 skill 包 |
| `email/` | Email Agent 用的 skill（每日摘要、智能回复、线程总结） |
| `browser/` | Browser Agent 用的 skill（Jira 探索、Confluence 搜索等） |
| `engineering/` | 研发部门专属 skill |

（后续按部门继续扩展）

---

## Skill 目录结构（单个 skill）

每个 skill 是一个子目录，含：

```
<skill-name>/
├── SKILL.md        # 说明书（agentskills.io 标准）
├── script.py       # 可选：辅助脚本
└── resources/      # 可选：模板、prompt 等
```

---

## Skill 从哪来

1. **人工写**：平台团队或业务专家手写
2. **Hermes 自创**：agent 完成复杂任务后自动生成
3. **员工贡献**：员工 publish 到 Skills Hub

三种来源的 skill 形态一致，可互操作。
