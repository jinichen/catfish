#!/usr/bin/env bash
#
# 含中文 (非 ASCII) 的 .ps1 必须是 UTF-8 **带 BOM**。
#
# ── 为什么需要这一条 ────────────────────────────────────────────────
#
# Windows PowerShell 5.1 (Win10/11、Server 2022 自带的那个 powershell.exe)
# 读没有 BOM 的脚本时, 按系统 ANSI 代码页解码 —— 中文 Windows 上就是 GBK。
# UTF-8 的中文被当成 GBK 读, 字符串乱码是轻的; 多字节序列吞掉引号 / 换行时,
# 整行跟着丢, 脚本照样往下跑, 报的错跟真因毫无关系。
#
# 10/20 真实发生过: 一个诊断脚本第一行被吞, $HERMES_HOME 是空的, 后面所有
# 路径全错。pwsh 7 默认按 UTF-8 读, 所以在开发机 / CI 上永远复现不出来。
#
# 只查含非 ASCII 的: 纯 ASCII 的文件在任何代码页下都一样, 不需要 BOM。
#
# 范围: git 跟踪的 + 未跟踪但没被 .gitignore 排掉的 (新写还没提交的也拦)。
# 被 ignore 的是构建产物, 不在这里管。
#
# 反过来: 被 Rust include_str! 嵌进去、拼成 `powershell -Command <字符串>` 跑的
# (wix/windows-maintenance.ps1), 内容走的是命令行参数 (UTF-16), 不经过按代码页
# 读文件这一步, 用不着 BOM; 而 BOM 会变成命令字符串里的一个 U+FEFF。这类文件
# 反过来要求**没有** BOM。
#
# 退出码: 0 = 都合规 / 1 = 有缺 BOM 的, 或被嵌入的带了 BOM
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

has_bom() { [ "$(head -c3 "$1" | od -An -tx1 | tr -d ' \n')" = "efbbbf" ]; }

# include_str!("相对路径.ps1") → 仓库内路径 (相对的是那个 .rs 所在目录)
embedded=$(git grep -o 'include_str!("[^"]*\.ps1")' -- '*.rs' | while IFS= read -r m; do
    rs="${m%%:*}"; rel="${m#*include_str!(\"}"; rel="${rel%\")}"
    d="$(cd "$(dirname "$rs")/$(dirname "$rel")" 2>/dev/null && pwd -P)" || continue
    echo "${d#"$(pwd -P)/"}/$(basename "$rel")"
done | sort -u)

bad=()
while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue                      # 已删除还没提交的
    if printf '%s\n' "$embedded" | grep -qxF "$f"; then
        has_bom "$f" && { echo "❌ $f 被 include_str! 拼进 -Command, 不能带 BOM —— 去掉开头 3 字节"; exit 1; }
        continue
    fi
    has_bom "$f" && continue
    LC_ALL=C grep -q $'[\x80-\xff]' "$f" || continue
    bad+=("$f")
done < <(git ls-files -z --cached --others --exclude-standard -- '*.ps1')

if [ "${#bad[@]}" -gt 0 ]; then
    echo "❌ 这些 .ps1 含中文 (非 ASCII) 但没有 UTF-8 BOM —— Windows PowerShell 5.1 会按 GBK 读乱:"
    echo ""
    for f in "${bad[@]}"; do echo "   $f"; done
    cat <<'MSG'

   加 BOM (只对上面列出的文件各做一次):
       f=<文件>; { printf '\xef\xbb\xbf'; cat "$f"; } > "$f.tmp" && mv "$f.tmp" "$f"

   ⚠ 如果这个文件不是用 `powershell -File` 跑的, 而是被别的工具读进去
     (Rust include_str! 拼进 -Command、Python 读出来打补丁……), 先确认那边
     能处理开头的 BOM (Python 用 encoding='utf-8-sig'), 再加。
MSG
    exit 1
fi

echo "✓ 含非 ASCII 的 .ps1 都带 UTF-8 BOM"
