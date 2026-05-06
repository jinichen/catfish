#!/usr/bin/env bash
# 诊断 sandbox-exec 在你 mac 上为啥 execvp 失败
# 不引入新概念, 一次性跑完所有变量, 一目了然

echo "========== sandbox-exec 诊断 =========="
echo "macOS 版本: $(sw_vers -productVersion)"
echo "build:    $(sw_vers -buildVersion)"
echo "arch:     $(uname -m)"
echo
echo "sandbox-exec 路径: $(which sandbox-exec)"
echo "sandbox-exec ls -la:"
ls -la "$(which sandbox-exec)" 2>&1 | sed 's/^/  /'
echo
echo "/usr/bin/python3 路径:"
ls -la /usr/bin/python3 2>&1 | sed 's/^/  /'
file /usr/bin/python3 2>&1 | sed 's/^/  /'
echo
echo "/bin/echo 路径:"
ls -la /bin/echo 2>&1 | sed 's/^/  /'
echo

# ---------------------------------------------------------------------
echo "[A] sandbox-exec 起 echo (最简, 不要 profile)"
out=$(sandbox-exec -p '(version 1)' /bin/echo "hello-A" 2>&1)
echo "  rc=$? out=[$out]"

echo
echo "[B] sandbox-exec 起 python3 (最简, 不要 profile)"
out=$(sandbox-exec -p '(version 1)' /usr/bin/python3 -c 'print("hello-B")' 2>&1)
echo "  rc=$? out=[$out]"

echo
echo "[C] sandbox-exec 起 echo + (allow default) 全放"
out=$(sandbox-exec -p '(version 1)(allow default)' /bin/echo "hello-C" 2>&1)
echo "  rc=$? out=[$out]"

echo
echo "[D] sandbox-exec 起 python3 + (allow default) 全放"
out=$(sandbox-exec -p '(version 1)(allow default)' /usr/bin/python3 -c 'print("hello-D")' 2>&1)
echo "  rc=$? out=[$out]"

echo
echo "[E] sandbox-exec 起 sh (验 shell)"
out=$(sandbox-exec -p '(version 1)(allow default)' /bin/sh -c 'echo hello-E' 2>&1)
echo "  rc=$? out=[$out]"

echo
echo "[F] python 不带沙箱 (验 python 本身能跑)"
out=$(/usr/bin/python3 -c 'print("hello-F")' 2>&1)
echo "  rc=$? out=[$out]"

echo
echo "[G] echo 不带沙箱 (验 echo 本身能跑)"
out=$(/bin/echo "hello-G" 2>&1)
echo "  rc=$? out=[$out]"

echo
echo "========== 诊断完 =========="
echo "结果解读:"
echo "  F/G 失败    → 系统坏了, 跟沙箱无关"
echo "  F/G 通, A-E 全败 → sandbox-exec 在你 mac 上整体被禁 (Sequoia/SIP 收紧)"
echo "  A 通 B 不通 → sandbox-exec 不让 exec python3 (可能 Apple Silicon stub 路径问题)"
echo "  C/D/E 通    → 默认 deny 太狠, profile 加 (allow default) 兜底就行"
