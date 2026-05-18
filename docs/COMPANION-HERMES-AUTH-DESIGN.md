# Companion ↔ hermes API Server 认证设计

> **状态**: 设计稿. 5/19 凌晨写, 给 BL-COMPANION-SWITCH-TO-HERMES-SERVE 实施者用.
>
> **触发**: Phase 2-2 切换前置. Phase 2-2A 配置层已 ship (BL-COMPANION-HERMES-API-CONFIG).
> 这份 doc 解决 "API_SERVER_KEY 怎么管 / 装机时怎么自动同步" 的问题.

## 1. 问题

hermes API server 用 `API_SERVER_KEY` Bearer auth. Companion 调它必须知道这
key. 两份配置必须**同值**:

- `~/.hermes/.env`              ← hermes 启动时读, 验请求
- `~/.catfish/companion.yaml`   ← Companion 启动时读, 拼 Authorization header

**手动同步** (现在 Phase 2-2A): 装机时 IT 跑命令随机生成一个 token, 复制粘贴到两边.
易出错, 不可持续.

## 2. 方案 (短期 + 长期)

### 2.1 短期方案: 一键安装时自动同步 (推荐)

写 `scripts/setup-catfish-edge.sh`, 安装期 (catfish-edge 全套部署时) 一次性:

```bash
# 1. 生成强随机 token
KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 2. 写 ~/.hermes/.env (hermes 读, 给 API server 验请求)
if ! grep -q "^API_SERVER_KEY=" ~/.hermes/.env 2>/dev/null; then
    echo "API_SERVER_ENABLED=true" >> ~/.hermes/.env
    echo "API_SERVER_KEY=$KEY" >> ~/.hermes/.env
    chmod 600 ~/.hermes/.env
fi

# 3. 写 ~/.catfish/companion.yaml (Companion 读, 调请求)
python3 - <<PYEOF
from pathlib import Path
import yaml

cfg_path = Path.home() / ".catfish" / "companion.yaml"
cfg_path.parent.mkdir(exist_ok=True)
data = yaml.safe_load(cfg_path.read_text("utf-8")) if cfg_path.exists() else {}
if not isinstance(data, dict):
    data = {}
data.setdefault("hermes_api", {}).update({
    "enabled": True,
    "url": "http://localhost:8642",
    "key": "$KEY",
})
cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
PYEOF
chmod 600 ~/.catfish/companion.yaml

# 4. 重启 hermes
hermes gateway restart
```

**优点**: 简单, 一次性. **缺点**: token rotation 要手动 (重跑脚本就 rotate).

### 2.2 长期方案: catfish-identity 签发 (Phase 2 之后)

让 catfish-identity 给"Companion ↔ hermes 这一对" 签发短期 token, 跟 user
OAuth 共享身份:

```
Companion 登录员工 OAuth → 拿 id_token (员工身份)
  ↓
Companion 调 catfish-identity /api/companion/hermes-session-token
  ↓
catfish-identity 验员工 id_token + 签发 service_token-style 短期 token (1h)
  ↓
Companion 把这 token 当 Authorization 调 hermes 8642
  ↓
hermes 用同一 catfish-identity 公钥验签 (跟它对 gateway 验签同模式)
  ↓
hermes 知道是真员工 (sub=email, on-behalf-of), 走 agent loop + memory inject
```

**优点**:
- token rotation 自动 (跟员工登录态绑)
- 多员工场景天然支持 (每员工自己的 token, hermes 多用户)
- 跟 BL-CENTRAL-EDGE-BOUNDARY 一致 (中央签发, 边缘验签)

**缺点**:
- 需要改 hermes API server 接受 OIDC token (现在只接 API_SERVER_KEY 静态字串)
- hermes API server 不是 catfish 写的, 改它要 fork
- catfish-identity 加 `/api/companion/hermes-session-token` 端点

**这个方案现在做不了, 留 Phase 3+.**

### 2.3 折中方案: 装机随机 + 季度 rotate (实用)

不动 hermes / identity, 用脚本:

- 装机 setup-catfish-edge.sh 生成 token (跟 2.1 一样)
- 加 cron 季度自动 rotate:
  ```cron
  0 3 1 1,4,7,10 *  /opt/catfish/scripts/rotate-hermes-api-key.sh
  ```
  rotate-hermes-api-key.sh:
  1. 生成新 KEY
  2. 写 ~/.hermes/.env (新值)
  3. 写 ~/.catfish/companion.yaml (新值)
  4. `hermes gateway restart` (新 .env 生效)
  5. 通知 Companion 重读 yaml (Companion 自己也要重启, 或者支持 SIGHUP 重读)

**简单可行**, 跟 hermes 30 天 service token 续法类似.

## 3. 推荐: 2.1 装机一次 + 2.3 季度 rotate

短期足够, 长期等 catfish-identity / hermes 都齐了再做 2.2.

## 4. 实施 (Phase 2-2A 之后)

### 4.1 写 scripts/setup-catfish-edge.sh

一个安装脚本同时配置:
- hermes API server (env)
- Companion hermes_api 配置 (yaml)
- catfish-memory plugin 软链 (调 install-catfish-memory.sh)
- catfish-autocompress plugin 软链 (调 install-catfish-autocompress.sh)
- catfish-tool-bridge (pip install -e)

把 5/19 早上"客户怎么装" 这个问题彻底解决.

### 4.2 写 scripts/rotate-hermes-api-key.sh

季度 rotate, 自动 + 静默. 跑完 hermes 重启 + 日志 + Companion 提示用户也重启.

### 4.3 文档

- docs/CATFISH-EDGE-INSTALL.md — 给 IT / 员工的安装文档
- docs/CATFISH-EDGE-KEY-ROTATION.md — 解释 rotate 机制

## 5. 安全边界

| 风险 | 缓解 |
|---|---|
| API_SERVER_KEY 泄漏 → 任何进程能调 hermes 全部 agent 能力 | chmod 600 + 季度 rotate + .env / yaml 不进 git (gitignore 加 ~/.catfish 写法) |
| 别员工 Mac 偷看 ~/.hermes/.env | 用户 home 默认权限 700, 一般不会; 加 chmod 600 双保险 |
| Companion 进程被注入恶意代码 → 它能调 hermes 等于绕过员工人格 | 全 OS 级问题, 不在本设计范围 |
| Companion 把 key 误 log | hermes_api_config.rs `hermes_api_config_get` 命令不返 key (只返 has_key), Rust 端拼 header 不发 JS |

## 6. 实施记录

| Step | Status | 备注 |
|---|---|---|
| Phase 2-2A 配置层 (BL-COMPANION-HERMES-API-CONFIG) | ✓ ship | services/hermes_api_config.rs + tauri.ts wrapper |
| setup-catfish-edge.sh 一键安装 | ⬜ | Phase 2-2A 后立即写 |
| Phase 2-2B chat.ts 切到 hermes endpoint | ⬜ | 1 周大改, 用 BL-HERMES-OPENAI-SERVER-RESEARCH 当 spec |
| rotate-hermes-api-key.sh 季度脚本 | ⬜ | Phase 2-2B 后写 |
| Phase 3+ catfish-identity 签发模型 | ⬜ | 长期 |
