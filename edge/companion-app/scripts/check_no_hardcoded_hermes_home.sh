#!/usr/bin/env bash
# 禁止 Rust 代码写死 `~/.hermes` —— Windows 上 hermes 不在那儿。
#
# # 为什么有这个文件
#
# 9/22 鸿波 Windows 截图: 仪表盘上四个本地服务 (Hermes / Tool Bridge / Chrome /
# Local Search) 全红「未启动」, 只有远端 Gateway 绿。
#
# 不是四个 bug, 是一个路径假设: catfish_paths::tool_bridge_python() 找的是
#     ~/.hermes/hermes-agent/venv/Scripts/python.exe
# 而 Windows 安装器装在
#     %LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe
# 找不到 → 静默退回裸 python3 → Windows 上不存在 → tool-bridge 起不来。
#
# 更要紧的是这个缺口**代码里早就写着**: catfish_paths.rs 的 hermes_home()
# 注释 —— "本文件其它地方仍写死 ~/.hermes, 全 Rust 代码里这样的还有 40 多处,
# 没在这次一起动"。写下来了, 没有任何东西执行它, 于是一直留到 Windows 上线。
# 跟本仓库 gitleaks 白名单 / bench 基线 / 版本号那三桩是同一个形状。
#
# 9/22 把 37 处全部换成 hermes_home() / hermes_home_for(), 这个脚本保证不再长回来。
#
# # 允许的例外
#
#   services/catfish_paths.rs 里 hermes_home_for 自己的回落分支 —— 它就是
#     "Unix 上默认 ~/.hermes" 这条规则的**唯一**出处
#   *_tests.rs / tests/ —— 测试造假 home 目录
#   注释里提到 .hermes —— 说明性文字, 不是路径拼接
#
# 用法:  bash edge/companion-app/scripts/check_no_hardcoded_hermes_home.sh
# 退出码: 0 = 干净; 1 = 有新的写死 (打印位置)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/../src-tauri/src"

# 只看代码行: 剔掉纯注释行 (// 开头) 和行内 // 之后的部分, 再找 ".hermes" 字面量
HITS="$(grep -rn --include='*.rs' '"\.hermes"' "$SRC" \
    | grep -v '_tests\.rs:\|/tests/' \
    | sed -E 's#(:[0-9]+:)(.*)//.*$#\1\2#' \
    | grep '"\.hermes"' \
    | grep -v 'services/catfish_paths.rs:[0-9]*:.*home\.join("\.hermes")$' \
    || true)"

if [ -z "$HITS" ]; then
    echo "✓ Rust 代码里没有写死的 ~/.hermes (全部走 hermes_home / hermes_home_for)"
    exit 0
fi

echo "❌ 发现写死的 ~/.hermes —— Windows 上 hermes 不在那儿 (%LOCALAPPDATA%\\hermes):" >&2
echo "$HITS" | sed 's#^#   #' >&2
echo "" >&2
echo "   改用:" >&2
echo "     crate::services::catfish_paths::hermes_home()          // 返 Option<PathBuf>" >&2
echo "     crate::services::catfish_paths::hermes_home_for(&home)  // 已有 home 路径时" >&2
echo "   两个都处理 HERMES_HOME / LOCALAPPDATA / 回落 ~/.hermes 三种情况。" >&2
echo "" >&2
echo "   9/22 之前有 37 处这样的写法, 后果是 Windows 上四个本地服务全部起不来。" >&2
exit 1
