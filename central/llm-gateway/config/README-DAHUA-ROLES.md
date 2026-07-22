# Catfish · 达华 POC · roles.yaml 说明 · 7/19 修订

## 严守军规 · 用户 picker 选哪个 model 就用哪个 · 不越权

用户在 Companion 桌面 picker 选 · 是员工**明确意愿**. gateway 不能:
- 员工选公网 · 自动切内网 (员工可能就是选公网快)
- 员工选内网 · 自动切公网 (**数据外泄** · 合规违规)

**gateway 尊重 picker · 挂了返错给员工 · 让员工决定重试或切换**.

## 达华现场 · 用 · `roles.yaml.dahua`

**关键改动 vs 老版本**:
- **删了 `fallback_chain`** — 老逻辑残留 · 会诡异越权 · 不用
- **保 `chat_default: catfish-private-main`** — WeChat 场景 (无 picker) 走内网
- **RBAC 默认** · employee/manager 允许 2 个 model (员工 picker 可选)

## 部署

```bash
cp roles.yaml.dahua roles.yaml
docker restart catfish-gateway
```

## fallback 想启用? 走 models.yaml (每 model 独立)

若达华某个 model 想配 fallback (如 · 内网挂 · 自动走同类内网备份):

```yaml
# models.yaml
models:
  - name: catfish-private-main
    upstream: {...}
    fallback:
      on_errors: [502, 503, 504, "timeout"]
      chain: [catfish-private-backup]   # 只 fallback 到同类同网
      max_hops: 1
```

**建议**: 达华 POC 期 · 不配 fallback · 保持简单 · 挂了 IT 快修.

## WeChat 场景 · chat_default 决定路径

微信里 · 员工没 picker · 消息发到 hermes · hermes `config.yaml model.name = catfish-auto` · gateway 收 · 从 roles.yaml `chat_default` resolve.

**若 chat_default = catfish-private-main** · WeChat 走内网 · **数据不出内网** · 政企硬规.

## 一句话

**picker 场景 · 用户选谁用谁 · gateway 不越权. WeChat 场景 · 走 chat_default (内网优先)**.
