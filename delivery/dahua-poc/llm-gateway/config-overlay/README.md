# llm-gateway config-overlay · 达华 POC 专属

## 用途
覆盖 central 通用版 config · 只放**跟通用不同**的文件.

## rebuild 时怎么用
`central/rebuild-from-scratch-0720.sh` Phase 3.pre 两步 rsync:
1. `central/llm-gateway/config/` → `delivery/dahua-poc/llm-gateway/config/` (baseline)
2. `delivery/dahua-poc/llm-gateway/config-overlay/` → `delivery/dahua-poc/llm-gateway/config/` (覆盖 · 达华定制生效)

打 tar 时 · overlay 目录 **exclude** · 客户拿到的 tar 里只有 merge 后的 `config/`.

## 现有 overlay 文件
- `roles.yaml` · 达华单 model (阿里云百炼 qwen3.7-plus · 视觉多模态一站式) · 覆盖通用 7-role 版

## 加新 overlay 文件
```bash
# 拷 baseline 出来改
cp ../config/models.yaml ./models.yaml
vi ./models.yaml   # 改成达华专属 (删外网 model / 加内网 vLLM 地址 / 调 max_tokens)
```

下次 rebuild · 会自动 apply · 打进 delivery tar.

## ⚠ 不要放在 overlay 里的
- 敏感字段 (API key · secret · JWT signing key) → 走 `.env` · 客户自填
- `users.yaml` / `clients.yaml` / `dev_users.yaml` → identity 侧 · 有 users.yaml.example
- 通用版本身够用的文件 → 别复制过来 · 保持 overlay 最小
