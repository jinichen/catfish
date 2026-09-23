#!/usr/bin/env bash
# 在 Linux / mac 上按 Windows 目标类型检查 Companion (9/23).
#
# # 为什么
#
# mac 上 `cargo check` 看不到 `#[cfg(windows)]` 的代码, 而 lib.rs 顶上是
#     #![cfg_attr(all(target_os = "windows", not(debug_assertions)), deny(warnings))]
# 所以 "只在 Windows 上 unused 的 import" 在 mac 上是零信号, 在 Windows release
# 上是编译错误 —— 以前要等打 MSI 才炸。9/23 修 Windows 那一晚, 这个脚本抓到
# 两处我自己刚写出来的 (calendar.rs 的 JXA 常量 / Command import)。
#
# # 怎么做到不装 MSVC 也能 check
#
# `cargo check` 不链接, 但依赖的 build script (libsqlite3-sys 编 C、tauri-winres
# 编 .rc) 要跑完。这里给它们假的 cl / lib / llvm-rc: 只把该产出的文件 touch
# 出来。生成的 .rlib 不能拿去链接 —— 只用来做类型检查, 别的用途都不对。
#
# tauri 的 build script 还会检查 bundle.resources 里列的文件在不在 (那些是打包
# 时才生成的大归档), 缺的临时补空文件, 跑完删掉 —— 不留在工作区。
#
# 用法:  bash edge/companion-app/scripts/cargo_check_windows.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAURI="$HERE/../src-tauri"
FAKE="$(mktemp -d)"
PLACEHOLDERS=()
cleanup() {
    for f in "${PLACEHOLDERS[@]:-}"; do [ -n "$f" ] && [ -f "$f" ] && [ ! -s "$f" ] && rm -f "$f"; done
    rm -rf "$FAKE"
}
trap cleanup EXIT

cat > "$FAKE/cl" <<'EOF'
#!/bin/bash
for a in "$@"; do case "$a" in -Fo*|/Fo*) out="${a:3}";; -E|/E) exit 0;; esac; done
[ -n "${out:-}" ] && : > "$out"; exit 0
EOF
cat > "$FAKE/lib" <<'EOF'
#!/bin/bash
for a in "$@"; do case "$a" in -out:*|/OUT:*|-OUT:*|/out:*) out="${a#*:}";; esac; done
[ -n "${out:-}" ] && printf '!<arch>\n' > "$out"; exit 0
EOF
cat > "$FAKE/llvm-rc" <<'EOF'
#!/bin/bash
prev=""; for a in "$@"; do
  case "$prev" in /fo|-fo|/FO|-FO) out="$a";; esac
  case "$a" in /fo?*|-fo?*) out="${a:3}";; esac; prev="$a"; done
[ -n "${out:-}" ] && : > "$out"; exit 0
EOF
chmod +x "$FAKE"/*

cd "$TAURI"
while IFS= read -r f; do
    [ -e "$f" ] && continue
    mkdir -p "$(dirname "$f")"; : > "$f"; PLACEHOLDERS+=("$PWD/$f")
done < <(python3 -c 'import json; [print(k) for k in json.load(open("tauri.windows.conf.json"))["bundle"]["resources"]]')

export PATH="$FAKE:$PATH"
export CC_x86_64_pc_windows_msvc="$FAKE/cl" CXX_x86_64_pc_windows_msvc="$FAKE/cl" AR_x86_64_pc_windows_msvc="$FAKE/lib"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$TAURI/target/windows-check}"

echo "── cargo check --release --target x86_64-pc-windows-msvc (deny(warnings)) ──"
cargo check --release --target x86_64-pc-windows-msvc
echo "── cargo check --tests --target x86_64-pc-windows-msvc (本 crate 零警告) ──"
# 测试构建没有 deny(warnings), 警告会一直攒着 —— 9/23 一次清掉 20 条, 这里防它长回来。
# 用输出判断而不是 RUSTFLAGS=-D warnings: 后者会让所有依赖重编一遍。
OUT="$(cargo check --tests --target x86_64-pc-windows-msvc --message-format short 2>&1)" || { echo "$OUT"; exit 1; }
if echo "$OUT" | grep -E '^src/.*: warning:' ; then
    echo "❌ Windows 测试构建有警告 (上面) —— 多半是只在 Unix 用的东西没加 cfg(not(windows))"
    exit 1
fi
echo "✓ Windows 目标类型检查通过"
