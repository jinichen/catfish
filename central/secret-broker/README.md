# Secret Broker（秘密代理）

> **状态**：🟡 P1
>
> **定位**：员工**永不持有**长期 API key，所有凭据短期令牌化。

---

## 工作流

```
员工 SSO 身份
    │
    ▼
Secret Broker 换短期令牌（15min–1h）
    │
    ▼
Agent 用令牌访问内部系统
    │
    ▼
过期自动刷新
    │
    ▼
员工 offboarding → SSO 停用 → 所有令牌失效
```

## 作用域

每个令牌精确绑定：
- **员工 × 连接器 × 动作范围**

---

## 后端选型

- HashiCorp Vault / 云厂商 Secrets Manager
- 或自研（P1 MVP 可以先做简单的）
