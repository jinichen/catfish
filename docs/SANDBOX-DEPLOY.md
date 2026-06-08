# Catfish Sandbox 部署 SOP

> Catfish 通过 4 层沙盒保障 LLM 跑代码安全. 这份 doc 给 IT / DevOps 部署参考.
>
> 跟 `docs/CATFISH-CENTRAL-MANIFESTO.md` 公理 2 (数据零出端) 联动 —
> 沙盒是 manifesto 的 enforcement 层 (kernel 级, 不是承诺).

## 4 层架构 (跟主流对比)

| 层 | 技术 | 防什么 |
|---|---|---|
| 1. OS kernel sandbox | macOS `sandbox-exec` (SBPL 1) / Linux `nsjail` (7 个 namespace + rlimit + cgroups) / Docker (兜底) | 网络外传 / 偷读 `~/.ssh` / 偷开摄像头 / 改 `/etc` / fork bomb (Linux) |
| 2. 应用层字符串规则 | `adapter._check_execute_code_security` 25 类黑名单 | 快速拦明显恶意 (`rm -rf /`, `cat ~/.ssh/id_rsa` 等), 不入沙盒 |
| 3. 人审批 | P15/P15.2 catfish-tool-bridge → Companion banner (60s 倒计时) | 员工最终 veto, 跟 manifesto 公理 3 一致 |
| 4. Tauri WebView CSP | `default-src 'self'` + `connect-src 127.0.0.1:*` only | 防 LLM 在 WebView 内 XSS 偷传数据 |

## macOS 部署

### 1. sandbox-exec 自带

macOS 10.5+ 自带 `sandbox-exec`. 检查:

```bash
which sandbox-exec   # → /usr/sbin/sandbox-exec
```

### 2. 启用沙盒

```bash
# 默认开 (5/14 demo 起)
export CATFISH_SANDBOX_EXEC=1
```

或 `~/.hermes/.env` 加:
```
CATFISH_SANDBOX_EXEC=1
```

### 3. rlimit 限制 (6/7 BL-SANDBOX-MACOS-RLIMIT)

macOS `sandbox-exec` 不能拦 fork bomb / mem bomb / CPU bomb (SBPL 1 没这能力).
catfish 通过 `subprocess.Popen preexec_fn=setrlimit` 在子进程级补:

| 限制 | 值 | 效果 |
|---|---|---|
| `RLIMIT_CPU` | 30 秒 | 累计 CPU 30s 触发 SIGXCPU |
| `RLIMIT_FSIZE` | 50 MB | 单文件写 > 50MB 触发 SIGXFSZ |
| `RLIMIT_NOFILE` | 256 | 文件描述符上限 |
| `RLIMIT_STACK` | 8 MB | 栈上限 |

⚠️ **known limitation**: macOS `RLIMIT_NPROC` 是 **per-user** 不是 per-process, 设了
会影响员工**整个用户**的进程数, **直接让员工电脑卡死**. catfish 不设这个,
**fork bomb 在 macOS 拦不住**. Linux nsjail 通过 `pid namespace` 真隔离, 能拦.

→ 国企 / 金融客户安全等级高的, **推荐 Linux 部署** 配 nsjail, 不要 macOS.

### 4. 验证

```bash
cd ~/person_task/catfish/edge/tool-bridge
pytest tests/test_sandbox_macos_rlimit.py -v
```

期望: `test_rlimit_inherits_to_python_child` PASS — 沙盒子进程能看到 30s CPU / 50MB FSIZE.

## Linux 部署 (推荐生产)

### 1. 装 nsjail

```bash
# Ubuntu / Debian
apt install -y nsjail

# CentOS / RHEL
yum install -y nsjail

# 从源码 (任何发行版)
git clone https://github.com/google/nsjail
cd nsjail
make
sudo cp nsjail /usr/local/bin/
```

### 2. 装 Python 3.11 + 依赖

```bash
apt install -y python3.11 python3.11-venv
```

### 3. 启用沙盒

```bash
export CATFISH_SANDBOX_EXEC=1
```

### 4. 验证

```bash
nsjail --version
which nsjail   # → /usr/local/bin/nsjail
python -c "from catfish_tool_bridge.sandbox import detect_sandbox_kind; print(detect_sandbox_kind())"
# → nsjail
```

## Docker 兜底 (不推荐生产, 仅紧急用)

如果 macOS / Linux 都没有原生 sandbox 工具 (e.g. 客户 Linux 机器临时没装 nsjail):

```bash
docker --version  # 装好 docker

export CATFISH_SANDBOX_EXEC=1
# catfish 自动检测: detect_sandbox_kind() → "docker"
```

**为什么不推荐生产**:
- docker 启动慢 (cold start 1-3s, sandbox-exec / nsjail < 50ms)
- 镜像层 / volume 管理麻烦
- 客户 IT 安全策略可能禁 docker

## 企业 Egress Proxy (合规审计)

catfish 沙盒内**网络全断**, 但 catfish-tool-bridge / hermes daemon 自己的
outbound HTTPS (调外网 LLM API e.g. Anthropic / OpenAI) 走 OS 网络层.

国企客户**要审计出口流量**时, 设 system HTTP proxy:

```bash
# 配 enterprise proxy (e.g. mitmproxy / Squid / Zscaler)
export HTTPS_PROXY=http://your-egress-proxy:3128
export HTTP_PROXY=http://your-egress-proxy:3128
export NO_PROXY=localhost,127.0.0.1,10.10.40.0/16
```

写到 `~/.hermes/.env` 或 catfish 启动脚本前 `source`.

**效果**:
- catfish-gateway → Anthropic / OpenAI 调用走 enterprise proxy, IT 能 audit (HTTP CONNECT log)
- 内网 LLM (e.g. `catfish-private-main` 在 `10.10.40.102:32730`) 走 NO_PROXY 直连不被 proxy 拦
- catfish 沙盒**内部 LLM 跑的代码**仍然 0 outbound (sandbox-exec network deny 优先级最高)

**catfish 自己不集成 mitmproxy** — 让 IT 用自家熟悉的 enterprise proxy 即可.

## env 配置总表

| env | 值 | 默认 | 作用 |
|---|---|---|---|
| `CATFISH_SANDBOX_EXEC` | `1` / `0` | `0` (但 5/14 demo 起代码层默认 `1`) | 启用沙盒 |
| `CATFISH_SANDBOX_PROFILE` | 路径 | 自动选 macOS .sb / Linux .cfg | 自定义沙盒 profile |
| `CATFISH_SANDBOX_PYTHON` | 路径 | `sys.executable` | 沙盒内 Python 解释器 (覆盖 tool-bridge venv) |
| `HTTPS_PROXY` / `HTTP_PROXY` | URL | (无) | enterprise egress audit |
| `NO_PROXY` | CIDR list | (无) | 内网 LLM 直连白名单 |

## 故障排查

| 症状 | 原因 | 解决 |
|---|---|---|
| `沙箱不可用: macOS 需要 sandbox-exec / Linux 需要 nsjail` | 没装 / PATH 找不到 | macOS `which sandbox-exec` 应该 `/usr/sbin/sandbox-exec`, Linux 装 nsjail |
| `Operation not permitted` on file write | sandbox 拦了 (员工 LLM 想写 ~/.zshrc 被拦) | 正常拦截, 让 LLM 改用 TASK_DIR |
| sandbox 子进程被 SIGXCPU kill | RLIMIT_CPU 30s 触发 | LLM 写了死循环, 让员工 abort + 改 prompt |
| sandbox 子进程被 SIGXFSZ kill | RLIMIT_FSIZE 50MB 触发 | LLM 写了 > 50MB 文件, 让 LLM 用 streaming write |
| Linux 上 fork bomb 拦不住 | nsjail rlimit_nproc=10 没生效 | nsjail 必须以 root 跑或 setuid root 才能用 pid namespace |

## BL (留待 ship)

| 项 | 风险 | 工程量 |
|---|---|---|
| Tauri capability strict (替 wildcard) | 低-中 (WebView CSP 已锁外发) | 1-2d |
| WebView IPC HMAC 验签 (Tauri 自带 capability check 之上加层) | 低 (overkill) | 0.5d |
| macOS fork bomb 真拦 (process group 监控 kill) | 中 (LLM 真写 fork bomb 罕见但 PoC 时撞) | 1d |

— SOP 完
