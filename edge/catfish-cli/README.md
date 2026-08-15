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

## 文件布局 (8/15 拆分后)

原来是一个 1875 行的 `catfish.py`, 过了 CLAUDE.md §1 的 800 行红线。拆成:

| 文件 | 行 | 装什么 |
|---|---:|---|
| `catfish_config.py` | 105 | 常量 / 路径 / logger。**只依赖标准库** |
| `catfish_token.py` | 206 | TokenStore、存盘、JWT 解、refresh grant |
| `catfish_oauth.py` | 220 | 浏览器 OAuth flow + loopback callback |
| `catfish_hermes.py` | 494 | 往 hermes 同步 api_key / service token / 边缘 backend key |
| `catfish_proxy.py` | 183 | 死代理检测 + 清理 hermes 进程 env |
| `catfish_privacy.py` | 408 | 员工隐私自查 (`privacy-audit`) |
| `catfish.py` | 615 | 命令层 + argparse + re-export |

跑法没变, 还是 `python3 catfish.py <命令>` —— Python 会把脚本所在目录放进
`sys.path[0]`, 兄弟模块直接就能 import。launchd 里那条定时任务也不用改。

**依赖方向是单向的**: 子模块只往 `catfish_config` / `catfish_token` 依赖,
谁都不 `import catfish`。反过来 import 会有两个坑 —— 循环导入, 以及
catfish.py 当脚本跑时模块名是 `__main__`, 再 import 一次会得到第二个 module
对象 (两份常量两份 logger, 各自自洽, 测试看不出来)。这条由
`test_split_layering.py` 钉住。

**老的 `catfish.xxx` 写法全部还能用** —— catfish.py 顶部有 re-export。但
`monkeypatch.setattr(catfish, ...)` 有讲究, 见下面「测试」一节。

## 当前限制 (5/14 MVP)

| 问题 | 现状 | 何时解 |
|---|---|---|
| 没 PKCE | catfish-identity 没支持, 我们 MVP 跳过. 内网部署可接受. | 5/16 加 PKCE |
| token 1h 过期 | 没 refresh_token, 1h 后要重 login | #79 BL-IDENTITY-REFRESH-TOKEN |
| 不自动续 | 现在 `catfish refresh` 重开浏览器 | refresh_token + cron/launchd |
| 没装机脚本 | 员工要手 alias + cd 跑 | #80 BL-INSTALL-SCRIPT |
| 不是 pip 包 | 直接 `python catfish.py`, 没 pyproject | RBAC sprint 后整 pyproject.toml |

## 测试

```bash
cd ~/person_task/catfish/edge/catfish-cli
python3 -m pytest test_catfish.py test_split_layering.py -q
```

### ⚠ monkeypatch 要打对模块

`from X import name` 建的是**新绑定**不是别名, 函数体查自由变量查的是
**定义它的那个函数所在模块**的 globals。所以:

```python
monkeypatch.setattr(catfish, "_check_proxy_alive", fake)   # ✗ 打空了
monkeypatch.setattr(catfish_proxy, "_check_proxy_alive", fake)   # ✓
```

打空**不报错**, 只是被测代码跑去干真事 —— 连真代理端口、真跑
`hermes gateway restart`、真写你的 `~/.hermes/config.yaml`。
`test_split_layering.py::test_patch_的模块里必须真有人用这个名字` 会拦住这个。
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
