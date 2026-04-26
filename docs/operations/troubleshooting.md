# 鲶鱼运维故障排查手册

> 基于真实部署踩坑整理。遇到问题按顺序查，大部分能自己搞定。
>
> 覆盖范围：员工自装鲶鱼遇到的常见问题 + 平台侧运维问题。
>
> **变量约定**：本手册示例命令里 `$INTERNAL_LLM_HOST` 指代你公司内部 LLM
> 平台的 IP/hostname（来自 `INTERNAL_LLM_BASE_*` env vars 的 host 部分），
> 跑命令前请 `export INTERNAL_LLM_HOST=10.x.x.x` 或直接替换为实际值。

---

## 快速诊断三件套

遇到任何问题，先跑这三条：

```bash
# 1. 网关在跑吗，几个进程？
lsof -i:8999
ps aux | grep catfish_gateway | grep -v grep

# 2. 代理有没有劫持本机？
env | grep -i proxy

# 3. 到内网的路由是什么？
route get $INTERNAL_LLM_HOST 2>&1 | head -10
```

三条结果决定后续怎么查。

---

## 1. Hermes 启动后立刻 HTTP 502

### 症状
```
API call failed: HTTP 502
Endpoint: http://localhost:8999/v1
Error: HTTP 502
```

重试 3 次都 502，响应时间很短（几秒）。

### 排查顺序

#### 1.1 看网关日志有没有收到 POST

```bash
tail -f /tmp/gateway.log
```

在 Hermes 里发消息，**如果日志完全没新内容** -> Hermes 的请求没打到网关。
**99% 是代理劫持。** 查 1.2。

**如果日志有 POST 但带 traceback** -> 网关收到了但上游失败。查 2。

#### 1.2 代理劫持

```bash
env | grep -i proxy
```

看到 `HTTPS_PROXY=http://127.0.0.1:7890` 或类似？这是 Clash / V2ray / 公司代理。
Hermes 自身的 OpenAI client 用 httpx，但**忽略 `NO_PROXY` 环境变量**
（`trust_env=False` 类配置），所以哪怕目标是 `http://localhost`，
只要设了 HTTPS_PROXY，httpx 也会走代理。代理不知道怎么处理本地地址，
返 502 或 connection refused。

**先验证是不是这个问题（用 curl 绕开代理）：**
```bash
curl --noproxy '*' -sf http://localhost:8999/healthz
```
- 返回 `{"status":"ok",...}` → gateway 没问题，确认是 hermes 的代理问题
- 失败 → gateway 自己挂了，跟代理无关，去查 1.1

**正确修法：用 `catfish` 命令而不是裸 `hermes`**

`edge/branding/catfish` wrapper 已经内置代理净化，敲 `catfish` 自动 unset
`HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 再 exec hermes，员工 shell 的代理
设置不动（浏览器 / git 等照常）。

```bash
# ✅ 标准用法
catfish

# ❌ 别裸跑 hermes
hermes  # 撞代理

# ⚠ 极少数调试场景需要保留代理
CATFISH_KEEP_PROXY=1 catfish
```

**临时修（如果一定要用裸 hermes）：**
```bash
HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= hermes
```

**Clash Verge 用户也建议设直连规则（多一道保险，浏览器/curl 等也受益）：**
打开 Clash → 设置 → 规则 → 直连列表，加入：
- `localhost`
- `127.0.0.1/8`
- `10.0.0.0/8`
- `192.168.0.0/16`

> 注：Companion App **不受影响**，它内部 spawn 的 gateway 进程独立处理代理
> （`network.py` 启动时检测代理可达性后自动 unset）。

---

## 2. 网关日志里有 502 + traceback

### 症状
```
INFO: 127.0.0.1:XXXX - "POST /v1/chat/completions HTTP/1.1" 502
[ERROR] catfish.gateway: chat completion failed
Traceback ...
```

### 2.1 Network is unreachable

```
OSError: [Errno 51] Network is unreachable
Cannot connect to host 10.10.x.x
```

-> 没连公司 VPN 或 VPN 路由没刷新。

```bash
ifconfig | grep utun    # 有 utun0/utun1... 说明 VPN 接口在
route get $INTERNAL_LLM_HOST  # gateway 不能是 default 路由的家里网关
```

如果 `gateway: 172.20.10.1`（你手机热点）或 `gateway: 192.168.1.1`（家里路由）
-> 路由没走 VPN。重连 VPN 或检查 VPN 分流规则。

### 2.2 Connection refused / timeout

上游 LLM 平台没起或在维护。

```bash
INTERNAL_KEY=$(grep INTERNAL_LLM_KEY /Users/chenhongbo/person_task/catfish/central/llm-gateway/.env | cut -d= -f2)
curl -s -o /dev/null -w "%{http_code} · %{time_total}s\n" --max-time 5 \
  http://$INTERNAL_LLM_HOST:32730/openapi/YOUR-UUID/v1/models \
  -H "Authorization: Bearer $INTERNAL_KEY"
```

200 = 平台活着；timeout/refused = 找平台运维。

### 2.3 RateLimitError / 429

免费额度用完（公共 LLM）或并发限额。等几分钟或切模型。

### 2.4 ServiceUnavailableError / 503

公共 LLM 平台临时过载（preview 模型常见）。切换到稳定版模型：
- `catfish-public-gemini-flash` -> `catfish-public-gemini-stable`
- 或切回内网 `catfish-private-main`

### 2.5 BadRequestError + tools 相关

某些 vLLM 部署对 tool_calls 支持不完整。检查网关 models.yaml 里 `supports_tool_use: false`
标注，或者升级 vLLM 版本。

---

## 3. 5 毫秒内返回 502

### 症状

```bash
curl ... http://$INTERNAL_LLM_HOST:...
HTTP 502 · 0.004s
```

5ms 不是网络延迟，是**本机某服务立刻拒绝**。

### 可能原因

**3.1 Clash TUN 模式劫持内网 IP**

Clash 某些版本有 TUN/虚拟网卡模式，劫持所有流量。关掉 TUN，只保留系统代理。

**3.2 Docker/容器内部网络冲突**

Docker 默认用 172.17.x.x，如果和公司内网段冲突，会优先走 Docker 网桥。

```bash
ifconfig | grep -B1 inet | grep -E "en|utun|bridge"
```

### 解决

- 退出 Clash 再试：`pkill -9 -f clash; pkill -9 -f verge`
- 或在 Clash 配置里加直连规则（见 1.2）

---

## 4. 端口被占用

### 症状
```
[Errno 48] error while attempting to bind on address ('0.0.0.0', 8999): 
address already in use
```

### 解决
```bash
lsof -i:8999                    # 找出占用进程 PID
pkill -9 -f catfish_gateway     # 或精确 kill <PID>
sleep 2
lsof -i:8999                    # 应该空了
python -m catfish_gateway.app   # 重启
```

---

## 5. Hermes 配置错乱

### 症状

- `hermes` 启动状态栏显示 `claude-opus-4.6` 或其他错误模型名
- Hermes 报 "model not found"
- 切换模型后不生效

### 解决

```bash
# 1. 看当前配置
cat ~/.hermes/config.yaml | head -30

# 2. 重新走 hermes model 配置向导（最稳）
hermes model
# 选：Custom endpoint (enter URL manually)
# URL: http://localhost:8999/v1
# Key: 你的 CATFISH_DEV_TOKEN
# Model: catfish-private-main

# 3. 确认
grep -A2 "^model:" ~/.hermes/config.yaml
```

**不要用 sed 批量改 Hermes 配置**，yaml schema 会因版本变化，容易改错。

---

## 6. 环境变量迷失

### 症状

- curl 通，Python/Hermes 不通
- 不同 terminal 行为不同
- 关终端重开就好了 / 或相反

### 排查

```bash
# 看你当前 shell 加载了什么
cat ~/.zshrc ~/.zprofile 2>/dev/null | grep -iE "proxy|path|export"

# 看运行中的 Python 进程继承的环境
ps eww | grep python | head -5
```

### 一条命令绕过当前 shell 环境

```bash
env -i HOME=$HOME PATH=/usr/bin:/bin hermes
```

完全干净环境启动 Hermes，排除环境干扰。

---

## 7. bash 脚本报 `declare: -A: invalid option`

macOS 自带 bash 3.2，不支持关联数组。我们项目的脚本已经兼容 3.2，
但你装的是别人写的脚本遇到这个错 -> 让作者改成 3.2 兼容，或者：

```bash
# 装新 bash
brew install bash
# 让脚本用 /opt/homebrew/bin/bash 而不是 /bin/bash
```

---

## 8. sed -i 报错（在 Mac 上）

```
sed: 1: "...": invalid command code
```

### 原因
BSD sed (Mac) 和 GNU sed (Linux) 语法不同。

### 解决
```bash
# Mac 专用
sed -i '' 's/old/new/g' file.py

# Linux 专用  
sed -i 's/old/new/g' file.py

# 跨平台脚本：先检测
if sed --version >/dev/null 2>&1; then
  SED_INPLACE=(-i)       # GNU
else
  SED_INPLACE=(-i '')    # BSD
fi
sed "${SED_INPLACE[@]}" 's/old/new/g' file.py
```

---

## 9. 命令行里 `#` 后面的内容被当成命令

### 症状
```bash
command --flag  # 这是注释
# 报错：command not found: #
```

### 原因
zsh 默认不启用 INTERACTIVE_COMMENTS。

### 解决
别在命令行写行内注释。或者：
```bash
setopt INTERACTIVE_COMMENTS   # 启用一次
# 或永久加进 ~/.zshrc
echo 'setopt INTERACTIVE_COMMENTS' >> ~/.zshrc
```

---

## 快速联系

- 代理 / 网络问题：找本地 IT 确认公司 VPN 配置
- LLM 平台问题：找平台维护团队
- 鲶鱼本身问题：查 catfish-design.md + 提 issue

---

## 本文档更新记录

每次遇到新的踩坑，补到这里。这是项目推广的**护身符**。
