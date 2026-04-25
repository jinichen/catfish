# 内部系统 MCP 连接器

> **状态**：🟡 P1
>
> **定位**：公司内部系统（Jira / Confluence / GitLab 等）通过 MCP 协议给 agent 暴露能力。

---

## 为什么用 MCP

- 标准协议，Hermes 天然支持
- 每个连接器 = 独立进程，可以独立升级/重启
- 员工可以按需订阅（MCP Registry）

---

## 已规划的连接器

| 连接器 | 作用 |
|---|---|
| `jira/` | Jira ticket 查询/创建/更新 |
| `confluence/` | Confluence 文档搜索/创建 |
| `gitlab/` | GitLab 代码/PR/Issue 操作 |

（首批 3 个，其他按需增加）

---

## 如何新增连接器

复制 `_template/` 为新目录，填入：
- 目标系统 API 封装
- MCP server 样板代码
- 元数据（连接器名称、版本、所需权限）

发布到 MCP Registry 后员工可订阅。

---

## 贡献指南

- 权限最小化原则（只要 agent 实际需要的 scope）
- 失败要有可解释的错误消息
- 长操作要支持取消
- 单元测试覆盖核心路径
