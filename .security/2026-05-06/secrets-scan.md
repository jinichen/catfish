# Secrets baseline scan (2026-05-06)

**结果**: 源码文件 0 hit. Chrome cache binary 命中是浏览器缓存的页面内容 (随机字串误判, 不是真 secret), 已通过 `.gitignore` 排除 (`.companion-state/`).

## 扫描清单

| 模式 | 正则 | 命中 |
|---|---|---|
| AWS access key | `AKIA[0-9A-Z]{16}` | 0 |
| GitHub PAT | `gh[pousr]_[A-Za-z0-9]{36,}` | 0 |
| OpenAI key | `\bsk-[A-Za-z0-9]{30,}` | 0 |
| Anthropic key | `\bsk-ant-[A-Za-z0-9_-]{20,}` | 0 |
| Google API key | `AIza[0-9A-Za-z_-]{35}` | 0 |
| JWT-like | `eyJ...\.eyJ...` | 0 |
| 私钥 PEM | `BEGIN .* PRIVATE KEY` | 0 |
| 字面量密码 | `password\s*[:=]\s*"[^"]{8,}"` | 0 |

排除目录 (`.gitignore` 已防止 commit):
- `venv/` `node_modules/` `target/` `.git/` `__pycache__/`
- `.companion-state/` (Chrome profile 缓存, 随机命中)
- `.catfish/` (员工本机运行时状态)

## CI 自动化

`.github/workflows/security.yml` 已加 `gitleaks` job, 每次 push / PR / 每天 09:00 UTC 跑一次。任何带 secret 的 commit 直接 fail。

## 给客户的话

- 鲶鱼仓库源码经过 gitleaks 模式自动扫描, 0 已知 secret 形式
- 客户内网部署时, 真实凭证 (LLM api_key / SSO secret) 通过 **环境变量注入** (`INTERNAL_LLM_BASE_*` / IdP credentials), **不入版本库**
- 员工密码走 macOS Keychain (`keychain://xxx` 引用), 不入 chat 历史 / audit log
