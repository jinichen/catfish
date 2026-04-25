# 基础设施即代码

> **状态**：🟡 按需做

## 子目录

- `terraform/` — 云资源（如果上公有云）
- `k8s/` — Kubernetes manifest（生产部署时）
- `docker-compose.dev.yml` — 本地开发整栈（网关 + skills-hub + ...）
- `secrets-template/` — 秘密模板（不含真实值，真实值走 Vault）

## 当前状态

只有 `catfish/central/llm-gateway/docker-compose.yml` 可跑本地。

真正上生产 K8s / Terraform 在 P1 后期开始做。
