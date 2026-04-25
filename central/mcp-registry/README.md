# MCP 连接器注册表

> **状态**：🟡 P1
>
> **定位**：企业内部系统通过 MCP 协议暴露给 agent。

---

## 首批连接器

- Jira / Linear
- Confluence / Notion / 语雀
- GitLab / GitHub Enterprise
- 公司 OA
- BI 系统
- CRM

每个连接器 = 一个独立的 MCP server 进程。

---

## 员工使用流程

1. 浏览注册表 → 看哪些连接器可用
2. 订阅想用的
3. OAuth 授权（走 Secret Broker）
4. Agent 自动加载工具集
5. 随时可取消

**不强制推**：员工按需订阅。
