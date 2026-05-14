# catfish CLI — 鲶鱼员工自助登录

> **任务**: BL-CATFISH-LOGIN (5/14 凌晨 ship)
> **目的**: 解决"新装机器 API key 怎么来" — 员工浏览器登录公司 SSO, token 自动存 + 自动同步到 hermes config.

## 5 秒速试

```bash
# 1. 加 alias (建议加到 ~/.zshrc)
alias catfish='python3 ~/person_task/catfish/edge/catfish-cli/catfish.py'

# 2. 登录 (会开浏览器)
catfish login

# 3. 看状态
catfish status

# 4. 直接跑 hermes (api_key 已经被 catfish login 写进 hermes config 了)
hermes
```

## 命令一览

| 命令 | 用途 |
|---|---|
| `catfish login` | 浏览器 OAuth 登录公司 SSO, 拿 token 写 `~/.catfish/auth/token.json` + 同步到 `~/.hermes/config.yaml` |
| `catfish status` | 看登录状态 (是谁 / 还有多久过期) |
| `catfish logout` | 删 token (登出) |
| `catfish token` | 输出当前 access_token (供 shell 用, e.g. `curl -H "Authorization: Bearer $(catfish token)"`) |
| `catfish refresh` | 续 token (现在 = 重做 login. 5/16+ 加 refresh_token grant 后会真无感续) |

## 配置 (env 变量)

| 变量 | 默认值 | 用途 |
|---|---|---|
| `CATFISH_IDENTITY_URL` | `http://localhost:8998` | catfish-identity 地址 |
| `CATFISH_CLIENT_ID` | `hermes-cli` | OAuth client_id |
| `CATFISH_SCOPES` | `openid email profile chat.completions audit.write tools.invoke skills.run` | 请求的 scopes |
| `CATFISH_AUTH_DIR` | `~/.catfish/auth` | token 存哪 |
| `HERMES_CONFIG` | `~/.hermes/config.yaml` | hermes config 路径 |

也能通过 CLI 参数覆盖: `catfish login --identity-url https://identity.ffcs.cn`

## 工作流程图

```
首次 (1 次):
  catfish login
    │
    ├─→ 浏览器自动开 → catfish-identity /authorize
    │     │
    │     └─→ 员工填公司 SSO (chenhongbo@ffcs.cn / 公司密码)
    │
    ├─→ catfish-identity 签 access_token, redirect 回 localhost:RANDOM/callback
    │
    ├─→ 写 ~/.catfish/auth/token.json (chmod 600)
    └─→ 同步 ~/.hermes/config.yaml 的 custom_providers."Local (localhost:8999)".api_key

每次开 hermes:
  hermes  ←  从 config.yaml 读 api_key, 跟 gateway 8999 通

token 1h 后过期:
  catfish refresh   (现在: 重开浏览器登录. 5/16+ 加 refresh_token 后无感)
```

## 当前限制 (5/14 MVP)

| 问题 | 现状 | 何时解 |
|---|---|---|
| 没 PKCE | catfish-identity 没支持, 我们 MVP 跳过. 内网部署可接受. | 5/16 加 PKCE |
| token 1h 过期 | 没 refresh_token, 1h 后要重 login | #79 BL-IDENTITY-REFRESH-TOKEN |
| 不自动续 | 现在 `catfish refresh` 重开浏览器 | refresh_token + cron/launchd |
| 没装机脚本 | 员工要手 alias + cd 跑 | #80 BL-INSTALL-SCRIPT |
| 不是 pip 包 | 单文件 `python catfish.py` | RBAC sprint 后整 pyproject.toml |

## 测试

```bash
cd ~/person_task/catfish/edge/catfish-cli
python3 -m pytest test_catfish.py -q
# 26 测全过 (storage / 过期逻辑 / JWT 解 / hermes config patcher / 命令)
```

## 安全注记

- token.json **chmod 600** — 同机别的 user 看不到
- atomic write (`.tmp` 过渡再 rename) — 防写一半 crash 留半截
- state CSRF 防御 (32 字节随机)
- redirect_uri 强制 `http://localhost:RANDOM_PORT/callback` — loopback 不出本机 (RFC 8252 §7.3)
- **没存 client_secret** — public client (RFC 6749 §2.1), 装机包不应该含任何 secret

## 跟其他 task 的关系

- **#49 BL-HERMES013-INTEGRATE** ← 这个 CLI 是它的 "新机部署" 答案
- **#79 BL-IDENTITY-REFRESH-TOKEN** ← 加上后 `catfish refresh` 真无感
- **#80 BL-INSTALL-SCRIPT** ← 装机脚本调 `catfish login` 一次
- **#50 BL-RBAC P0+B sprint** ← 这个 CLI 是 sprint 的 client 端落地
