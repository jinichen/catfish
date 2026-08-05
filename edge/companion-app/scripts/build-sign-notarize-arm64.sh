#!/usr/bin/env bash
# Build, sign, notarize, and staple the complete offline ARM64 macOS package.
#
# Required environment (本机实际值, 8/5 填实 —— 占位符每次都要重查一遍):
#   SIGNING_IDENTITY="Developer ID Application: dan takaragi (LNCT7279Z6)"
#   NOTARY_PROFILE="catfish-notary"
#
# 这两个值不是机密: Developer ID 印在每个签过名的二进制里, 公开可读;
# NOTARY_PROFILE 只是钥匙串里一条记录的**名字**, 真正的 app-specific password
# 在钥匙串里, 不在这个文件里。所以可以入库。
#
# 忘了值怎么查:
#   security find-identity -v -p codesigning | grep "Developer ID Application"
#   xcrun notarytool history --keychain-profile catfish-notary   # 能跑通就是对的
#
# 首次在新机器上配 notary profile:
#   xcrun notarytool store-credentials catfish-notary \
#     --apple-id <Apple ID> --team-id LNCT7279Z6
#
# Usage:
#   SIGNING_IDENTITY="Developer ID Application: dan takaragi (LNCT7279Z6)" \
#   NOTARY_PROFILE="catfish-notary" \
#   bash scripts/build-sign-notarize-arm64.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="$APP_ROOT/src-tauri/target/release/bundle/macos"
APP_PATH="$TARGET_DIR/Catfish Companion.app"
DMG_PATH="$HOME/Downloads/Catfish-Companion-0.19.0-aarch64.dmg"
WORK_DIR="$(mktemp -d /tmp/catfish-sign.XXXXXX)"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

: "${SIGNING_IDENTITY:?请设置 SIGNING_IDENTITY}"
: "${NOTARY_PROFILE:?请设置 NOTARY_PROFILE}"

# ── 签名机制 ────────────────────────────────────────────────────────────────
#
# 8/5 公证日志 (submission c4fa54ba) 把要求说死了: Apple **会解开 tar.gz**,
# 里面每一个 Mach-O 都要满足三条 —— Developer ID 签名 + 安全时间戳 +
# hardened runtime。三个归档全中: cpython 的解释器和 .so、Chromium 整个
# Chrome for Testing.app、hermes-agent 的 node_modules 原生模块。
#
# 下面两件事是这一版的关键, 都不是"多加个 --deep"能解决的。

# ## 1. 运行时 entitlements —— 不给就等于把浏览器和解释器签死
#
# hardened runtime 默认禁掉 JIT、可写可执行内存、加载非同签名的库。而这三个
# 归档里装的恰好全是靠这些吃饭的东西:
#
#   · Chromium 的 V8    —— 没有 allow-jit 直接崩在渲染进程
#   · node / esbuild    —— 同上
#   · cpython           —— ctypes/cffi 要可写可执行内存; venv 里的 .so 由我们
#                          重签, 但 disable-library-validation 能免掉一整类
#                          "库签名不匹配"的加载失败
#
# 也就是说: 只签不给 entitlements, 公证会过, 但**达华点开浏览器就崩** ——
# 又是一个"我们这边全绿, 现场才炸"。宁可现在多想一步。
cat >"$WORK_DIR/runtime.entitlements" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-dyld-environment-variables</key><true/>
</dict>
</plist>
PLIST
ENTITLEMENTS="$WORK_DIR/runtime.entitlements"

sign_one() {
  codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" --sign "$SIGNING_IDENTITY" "$1"
}

# ## 2. 由内向外签, 而且**不用 --deep**
#
# 上一版对 .app 用的是 `codesign --deep`。日志里 Chromium 报的正是
# "The signature of the binary is invalid" —— Apple 自己的文档写明 --deep
# 只适合应急, 不适合分发: 它对嵌套 bundle 套用外层的参数, 顺序也不受控。
# Chromium 是 framework + 4 个 Helper.app 的嵌套结构, 必须先内后外:
#
#   loose 二进制/dylib → Helper.app → framework 的 Versions/<版本> → 外层 .app
#
# 实现上不写死 Chromium 的结构, 只按**路径深度倒序**签 —— 深的一定在浅的
# 里面, 天然就是由内向外。以后换 Chromium 版本、换目录名都不用回来改。
# 三类要签的东西 —— Mach-O 文件、.framework、.app —— 必须**混在一起**按深度
# 排, 不能分三轮各自排。
#
# 反例就在 Chromium 里 (我第一版真写错了):
#   .../CFT.framework/Versions/151/Helpers/Helper (GPU).app
# Helper.app 住在 framework **里面**。先签完所有 framework 再签所有 .app,
# 等于先给 framework 封了口, 再去改它肚子里的东西 —— 封条当场作废。
#
# 合成一条流按深度倒序就自动对了 (数字是 awk -F'/' 的 NF):
#   14  Helper (GPU).app/Contents/MacOS/Helper (GPU)   ← 最里面的可执行文件
#   11  Helper (GPU).app                                ← 再包起来
#    9  CFT.framework/Versions/151                      ← 再外面一层
#    7  Chrome.app/Contents/MacOS/Chrome
#    4  Chrome.app                                      ← 最后封外层

# 先把 Mach-O 清单**一次性**算出来存文件, 有两个原因:
#
#   · 快。hermes-agent-bundle 解开是 node_modules, 五万个文件; 对每个文件
#     起一次 `file` 进程要跑十几分钟, 而签名和自检各要遍历一次, 等于翻倍。
#     `file -b -f <清单>` 一个进程读完全部, 秒级。
#   · 能大声失败。清单是在调用者的 shell 里算的, 不在 `< <(...)` 的子 shell 里
#     —— 子 shell 里 `exit` 只杀子 shell, 外面的 while 读到空输入就"什么都没签"
#     然后一路绿。这条线上已经栽过太多次这种静默成功了。
#
# 按行对齐依赖"文件名里没有换行"。不敢默认成立, 所以核对行数, 对不上就停。
build_macho_list() {
  local root="$1" out="$2"
  find "$root" -type f >"$out.all"
  file -b -f "$out.all" >"$out.desc"
  local a b
  a="$(wc -l <"$out.all")"; b="$(wc -l <"$out.desc")"
  if [[ "$a" -ne "$b" ]]; then
    echo "❌ file 输出 $b 行, 文件却有 $a 个 —— 大概有文件名带换行, 不敢按行对齐" >&2
    exit 1
  fi
  # 没有任何 Mach-O 是合法的 (比如 catfish-email-dist 里全是 .whl), 所以 || true;
  # 数量会由调用方打印出来, 不会闷掉
  paste -d'\t' "$out.desc" "$out.all" | grep '^Mach-O' | cut -f2- >"$out" || true
  rm -f "$out.all" "$out.desc"
}

emit_signables() {
  local root="$1" macholist="$2"
  # (a) Mach-O 普通文件 (清单已备好)
  cat "$macholist"
  # (b) 版本化 framework: 签的是 Versions/<版本>, 不是 .framework 本身;
  #     有 Versions/Current 软链时 codesign 认 .framework, 直接给它
  while IFS= read -r fw; do
    if [[ -e "$fw/Versions/Current" ]]; then
      printf '%s\n' "$fw"
    else
      for v in "$fw"/Versions/*/; do
        [[ -d "$v" ]] && printf '%s\n' "${v%/}"
      done
    fi
  done < <(find "$root" -type d -name '*.framework')
  # (c) .app bundle
  find "$root" -type d -name '*.app'
}

sign_tree() {
  local root="$1" quiet="${2:-}" n=0
  local macholist="$WORK_DIR/macho.list"
  build_macho_list "$root" "$macholist"
  # awk -F'/' 的 NF 就是路径深度; $0 整行取回, 带空格的路径不会被切开
  # (Chromium 里满地都是 "Google Chrome for Testing Helper (GPU).app")
  while IFS= read -r target; do
    [[ -n "$quiet" ]] || echo "→ 签名 · $target"
    sign_one "$target"
    n=$((n + 1))
  done < <(emit_signables "$root" "$macholist" \
             | awk -F'/' '{print NF"\t"$0}' | sort -rn | cut -f2-)
  echo "   共签 $n 个 (其中 Mach-O 文件 $(wc -l <"$macholist" | tr -d ' ') 个)"
}

# ## 3. 本地自检 —— 用 Apple 的三条标准, 别再等 30 分钟才知道
#
# 这条线上最贵的不是签错, 是**签了以为签上了**。前两次都是本地
# `spctl -a` / `codesign --verify` 一路绿, 提交上去才被驳回 —— 因为那两个命令
# 验的是 App **自己**的签名, 而 Apple 验的是包里**每一个** Mach-O。
#
# 所以这里照着公证日志里那三条报错逐条自检, 一个文件一次 codesign -dvvv:
#   The binary is not signed with a valid Developer ID  → 查 Authority
#   The signature does not include a secure timestamp   → 查 Timestamp=
#   The executable does not have the hardened runtime   → 查 flags 里的 runtime
# 有一个不过就当场退出, 不做 dmg、不提交。
verify_tree() {
  local root="$1" bad=0 checked=0 out
  local macholist="$WORK_DIR/verify.list"
  build_macho_list "$root" "$macholist"
  while IFS= read -r f; do
    checked=$((checked + 1))
    out="$(codesign -dvvv "$f" 2>&1 || true)"
    if ! grep -q "Authority=Developer ID Application" <<<"$out"; then
      echo "   ✗ 没有 Developer ID 签名 · $f"; bad=$((bad + 1)); continue
    fi
    if ! grep -q "^Timestamp=" <<<"$out"; then
      echo "   ✗ 缺安全时间戳 · $f"; bad=$((bad + 1)); continue
    fi
    if ! grep -qi "flags=.*runtime" <<<"$out"; then
      echo "   ✗ 没开 hardened runtime · $f"; bad=$((bad + 1)); continue
    fi
  done <"$macholist"
  if [[ $bad -gt 0 ]]; then
    echo "❌ $bad/$checked 个二进制没签好 —— 提交上去必被驳回, 就地停" >&2
    exit 1
  fi
  echo "   自检通过 · $checked 个 Mach-O 全部满足 Developer ID + 时间戳 + hardened runtime"
}

sign_archive() {
  local archive="$1"
  local name="$(basename "$archive")"
  local unpack="$WORK_DIR/${name%.tar.gz}"
  local repacked="$WORK_DIR/$name"

  echo "→ 解压并签名 · $archive"
  mkdir -p "$unpack"
  tar xzf "$archive" -C "$unpack"
  sign_tree "$unpack" quiet
  verify_tree "$unpack"

  # 8/5: 原来先 find 出顶层条目再 `tar czf ... "${entries[@]}"`。两个毛病:
  #   1. macOS 自带 bash 3.2 下, 空数组展开 "${entries[@]}" 在 `set -u` 里直接
  #      "unbound variable" 退出 —— 归档一旦是空的就炸。
  #   2. 纯属多余 —— `-C "$unpack" .` 打出的结构完全一样, 而且这正是
  #      build-mac-resources.sh 自己用的写法 (第 246、364 行)。
  tar czf "$repacked" -C "$unpack" .
  cp "$repacked" "$archive"
  rm -rf "$unpack" "$repacked"   # chromium 解开近 1G, 三个归档同时留着会撑爆 /tmp
}

echo "=== 构建完整 ARM64 App ==="
cd "$APP_ROOT"
npm run tauri:build:clean
npx tauri build --bundles app --config src-tauri/tauri.aarch64.conf.json

if [[ ! -d "$APP_PATH" ]]; then
  echo "❌ 找不到构建产物：$APP_PATH" >&2
  exit 1
fi

# 8/5: 遍历**整个** Contents/Resources, 不是只 resources/mac。
#
# Apple 公证驳回 (submission 8c3f698a) 的唯一硬错误就是这个:
#   Contents/Resources/catfish-calendar —— The binary is not signed.
#   (x86_64 和 arm64 两个架构各报 3 条: 没签名 / 没安全时间戳 / 没 hardened runtime)
#
# 原来只签 Contents/Resources/resources/mac/, 而 catfish-calendar 在**上一层**,
# 于是从来没被签过 —— 而且构建、签 App、做 dmg、spctl 全部一路绿, 直到提交给
# Apple 才炸。又是一个"本地全过、外部才发现"。
#
# 改成遍历整个 Resources: sign_macho_files 里用 `file | grep Mach-O` 过滤,
# 非 Mach-O (Windows 的 exe / tar.gz / 文本) 自然跳过, 不会误签。
# 好处是以后再往 Resources 里加二进制不用记得改这里。
MAC_RESOURCES="$APP_PATH/Contents/Resources"

echo "=== 签名 App 内直接二进制 ==="
sign_tree "$MAC_RESOURCES"
verify_tree "$MAC_RESOURCES"

# 8/5: 这一段以前**一次都没执行过**。
#
# 原来写死三条路径 `$MAC_RESOURCES/cpython-3.11.15-embed.tar.gz` 等, 但归档的真实
# 位置是 `$MAC_RESOURCES/resources/mac/cpython-3.11.15-embed.tar.gz` —— tauri.
# aarch64.conf.json 里 "resources/mac-aarch64/X": "./resources/mac/X", 目标端多一层
# `resources/mac/`。于是 `if [[ -f ... ]]` 每次都为假, 三个归档全部跳过, 而 for 循环
# 悄无声息地走完 —— 日志里"=== 签名归档内运行时 ==="下面一行都没有, 谁也没看出来。
# 又是一次"检查写了, 但恒假", 跟 catfish-calendar 漏签是同一个病。
#
# 改成扫出 Resources 下**所有** .tar.gz。好处跟上面签二进制一样: 以后再加归档
# (node-embed、catfish-email-dist、hermes-deps-dist 本来就不在那三条里) 不用记得
# 回来改这里。非 Mach-O 内容由 sign_macho_files 的 `file | grep Mach-O` 自然过滤。
#
# resources/windows/ 下那几个是 0 字节占位文件 (真件由 CI 现下), 用 -s 跳过 ——
# 否则 `tar xzf` 对空文件报错, set -e 会把整个脚本带走。
echo "=== 签名归档内运行时 ==="
while IFS= read -r -d '' archive; do
  if [[ ! -s "$archive" ]]; then
    echo "→ 跳过空归档 (占位文件) · $archive"
    continue
  fi
  sign_archive "$archive"
done < <(find "$MAC_RESOURCES" -type f -name '*.tar.gz' -print0)

echo "=== 签名 App ==="
codesign --deep --force --options runtime --timestamp \
  --sign "$SIGNING_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
# --deep 会把 Resources 里的二进制重签一遍。它用的是同一组参数 (runtime +
# timestamp), 理论上不会退化 —— 但"理论上"正是前两次栽跟头的地方, 再验一次。
# 这次只剩 uv / catfish-calendar 两个, 几秒钟的事。
verify_tree "$MAC_RESOURCES"

echo "=== 制作并签名 DMG ==="
bash "$SCRIPT_DIR/make-dmg.sh"
codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG_PATH"
codesign --verify --verbose=2 "$DMG_PATH"

echo "=== 提交 Apple 公证 ==="
xcrun notarytool submit "$DMG_PATH" \
  --keychain-profile "$NOTARY_PROFILE" \
  --wait

echo "=== 装订公证票据 ==="
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"

echo "✅ 完成：$DMG_PATH"
