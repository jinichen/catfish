# 跨服务测试

> **状态**：🟡 随功能推进

## 与模块内测试的区别

- **模块内单元测试**：放在各模块 `tests/` 下（如 `catfish/central/llm-gateway/tests/`）
- **本目录**：跨服务的端到端测试、冒烟测试、集成测试

## 子目录

- `e2e/` — 真实环境端到端（网关 + Hermes + Companion 联动）
- `smoke/` — 部署后快速验证（30 秒内跑完）

## 当前

只有 `catfish/central/llm-gateway/scripts/test_chat.sh` 是现成的冒烟脚本。
