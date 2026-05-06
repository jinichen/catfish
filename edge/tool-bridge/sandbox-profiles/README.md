# BL-S29 macOS sandbox-exec — 真技术沙箱 (5/7 起 12 天 sprint)

> 央企信安会问 "万一 25 字符串规则漏了怎么办" — 这文件夹是兜底答案.

## 这文件干啥的

LLM 通过 hermes/tool-bridge 调 `execute_code` / `python` / `bash` / `shell_exec` 时,
现在已有的 `_check_execute_code_security()` 拦 25 类危险字符串模式.
但字符串匹配能被 chr() / base64 / 环境变量绕过, 所以加 **OS kernel 级**沙箱兜底:

```
LLM 生成代码
  ↓
[第 1 道] 25 字符串规则 (5/6 已 ship)        — 拦 90% 明显恶意, audit 留痕
  ↓
[第 2 道] sandbox-exec OS 级隔离 (5/14 ship)  — 兜底剩下 10% 绕过的
  ↓
真跑
```

## 文件清单

- `catfish_execute.sb` — macOS sandbox-exec profile (SBPL TinyScheme, deny by default + 白名单)
- `../tests/sandbox/test_sandbox_exec.sh` — 10 恶意 case + 3 sanity check

## profile 拦的 6 大类

| 类 | 怎么拦 | 例 |
|---|---|---|
| 文件读 | `deny file-read* ~/.ssh ~/.aws Keychains Documents` | `open('~/.ssh/id_rsa')` |
| 文件写 | `deny file-write* (subpath HOME)` (除 TASK_DIR) | 改 `~/.zshrc` 植后门 |
| 网络 | `deny network*` 全断 | `urllib`, `socket`, `curl` 子进程 |
| IPC | `deny mach-lookup` 除 dyld 必需 | 跟 1Password / Slack 进程通信 |
| 进程 | `deny process-info*` 除自身 | `ps aux` 列其他进程 |
| 设备 | `deny iokit-open device-camera` | 偷开摄像头 / 麦克风 |

## 资源限制 (caller 控制, 不是 .sb 控制)

```bash
ulimit -t 30 -v 524288   # CPU 30s, RAM 512MB
sandbox-exec -f catfish_execute.sb -D TASK_DIR=... /usr/bin/python3 -c '...'
```

## 怎么跑测试 (鸿波在自己 mac 上跑)

```bash
cd ~/person_task/catfish/edge/tool-bridge/tests/sandbox
bash test_sandbox_exec.sh
```

期望: 10 case 全 DENY (rc != 0), 3 sanity check 全 OK (rc == 0).

如果 sanity check 失败 → 沙箱过紧, 调 `catfish_execute.sb` 加白名单.
如果某个恶意 case 跑通了 → 沙箱有洞, 加 deny 规则.

## 常见 sandbox-exec 报错排查

| 报错 | 原因 | 解 |
|---|---|---|
| `Operation not permitted` | profile 拦了 | 看 system.log: `log show --predicate 'process == "sandbox"' --last 1m` |
| `dyld: Library not loaded` | 系统 lib 路径漏白名单 | 加 `(allow file-read* (subpath "/usr/lib"))` 或对应 framework 路径 |
| `unable to spawn` | python 路径不在 process-exec 白名单 | 看 `which python3`, 加进 .sb 的 `process-exec` 白名单 |
| `parameter HOME not bound` | 调用没传 `-D HOME=...` | sandbox-exec 命令行加 `-D HOME=$HOME` |
| `TASK_DIR not bound` | 调用没传 `-D TASK_DIR=...` | sandbox-exec 命令行加 `-D TASK_DIR=/tmp/catfish-sandbox-xxx` |

## 已知限制

1. **sandbox-exec 是 Apple 私有 API** (deprecated 但 macOS 26 还能用).
   Apple 内部 SBPL 5+, 公开只到 SBPL 1. profile 只用 SBPL 1 标准动词
   (避开 `device-camera` / `mach-priv-host-port` 等 SBPL 5+ 私有动词,
   它们会让 sandbox-exec 直接 EX_OSERR rc=71).
2. **macOS 26 (Tahoe) 起默认 deny 一切** — profile 必须 `(allow default)` 兜底,
   否则连 `process-exec /bin/echo` 都被拒. 早期 macOS 默认 allow.
3. **TCC 不在 sandbox-exec 控制范围** — Camera / Mic / Contacts 走 TCC 弹窗,
   sandbox-exec 拦不住已授权的 TCC. 但 catfish 跑的 python/bash 默认无 TCC 授权, 不是问题.
4. **fork bomb / 内存爆炸不是 sandbox-exec 责任** — SBPL 1 没有 process-fork
   / RLIMIT 控制, 这些是 cgroups / ulimit 的事. **5/19 BL-S29.6 上 nsjail (Linux)
   时由 cgroups pids.max + memory.limit 真拦**, macOS 侧需要 caller 设
   `ulimit -u 100 -t 30 -m 524288` 配合.
5. **Apple Silicon 跟 Intel 行为略不同** — M1/M2 上 brew 在 `/opt/homebrew`,
   Intel 在 `/usr/local`. profile 默认 allow + 关键 deny 模式不受影响.

## 跟 SECURITY-REVIEW.md 关系

`SECURITY-REVIEW-2026-05-06.md` § "execute_code 守卫" 当前只讲第 1 道 (25 字符串规则).
BL-S29 ship 后, 加章节"第 2 道: OS sandbox-exec/nsjail" — 见 `BL-S29.6` 文档收口.

## Linux 侧 (Hermes 服务器, BL-S29.4 ✅ 5/7 提前 ship)

`catfish_execute.cfg` — nsjail 配置 (Google 开源 Linux 沙箱).

### 比 macOS sandbox-exec 强一档的能力

| | macOS sandbox-exec | Linux nsjail |
|---|---|---|
| 文件隔离 | SBPL deny rules | mount/chroot, 沙箱看到的根本是空 tmpfs |
| 网络隔离 | `(deny network*)` | network namespace, 沙箱内连网卡都没有 |
| Fork bomb 防护 | ❌ SBPL 1 不带 process limit | ✅ `rlimit_nproc=10` + cgroups pids.max |
| 内存爆炸防护 | ❌ 靠 caller ulimit | ✅ `rlimit_as=512MB` + cgroups memory.limit |
| CPU 死循环 | ❌ 靠 caller ulimit | ✅ `rlimit_cpu=30` + cgroups cpu.max |
| Syscall 过滤 | iokit-open 等 | seccomp filter (BL-S29.6 收紧) |

### 怎么测 (鸿波 mac 用 docker)

```bash
cd ~/person_task/catfish/edge/tool-bridge
docker build -f tests/sandbox/Dockerfile.nsjail -t catfish-nsjail-test .
docker run --rm --privileged catfish-nsjail-test
```

`--privileged` 是 nsjail 在容器内创 namespace 需要 (测试环境). 客户内网真机部署 nsjail 不需要 docker, 不需要 privileged.

### 怎么测 (Linux 真机)

```bash
# 装 nsjail (Ubuntu 22+):
sudo apt install nsjail
# 或编 from source: git clone https://github.com/google/nsjail && cd nsjail && make

cd ~/person_task/catfish/edge/tool-bridge/tests/sandbox
bash test_nsjail.sh
```

### 期望: 13 case 全绿 (跟 macOS 一致 + 多 fork bomb / mem bomb 真拦)

### 部署到客户内网

Hermes 服务器 / 客户内网 GPU 都是 Linux. 部署清单:
1. `apt install nsjail` 或编 from source (二进制 ~ 2MB)
2. 拷 `catfish_execute.cfg` 到 `/opt/catfish/sandbox-profiles/`
3. tool-bridge 跑起来后, env `CATFISH_SANDBOX_EXEC=1` + `CATFISH_SANDBOX_PROFILE=/opt/catfish/sandbox-profiles/catfish_execute.cfg`
4. 跑 `test_nsjail.sh` 验证
