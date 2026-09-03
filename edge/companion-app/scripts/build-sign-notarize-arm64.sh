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
# 8/8: 版本号从 package.json 读, 不再硬编码。
#
# 原来这里写死 "0.19.0"。而 Companion 版本号是跟 hermes 钉死的 (见
# check_version_sync.sh), 每次 hermes 升级都要 bump —— 硬编码就意味着每次都得
# 记得改这一行, 而它跟 3 处版本号不在一起, 必漏。漏了的表现是打出来的 dmg 文件名
# 还叫旧版本, 分发时没人分得清手里是哪一版。
APP_VERSION="$(grep -E '^[[:space:]]*"version"[[:space:]]*:' "$APP_ROOT/package.json" \
               | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
[ -n "$APP_VERSION" ] || { echo "✗ 从 package.json 读不出版本号" >&2; exit 1; }
DMG_PATH="$HOME/Downloads/Catfish-Companion-${APP_VERSION}-aarch64.dmg"
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

# 先把 Mach-O 清单**一次性**算出来存文件。
#
# 为什么不是对每个文件跑一次 `file`: hermes-agent-bundle 解开是 node_modules,
# 六万条; 每个起一个进程要十几分钟, 而签名和自检各遍历一次就是翻倍。
#
# 为什么也不是 `file -b -f <清单>` 然后按行对齐 —— 8/5 实测当场炸:
#
#     ❌ file 输出 17 行, 文件却有 15 个
#
# 不是文件名带换行, 是 **macOS 的 file 对 universal binary 会打多行**:
# 一行总述 + 每个架构一行。catfish-calendar 正好是 x86_64+arm64 的胖二进制
# (公证日志里它就被报了两个架构), 一个文件占三行, 15 个文件 17 行, 分毫不差。
# 那条行数核对不是白写的 —— 它拦下的正是一次会静默错位的对齐。
#
# 所以改成直接读魔数, 一个 perl 进程扫完整份清单。判定是确定的, 不用解析
# file 的自然语言输出, 也不受 file 版本影响。perl 是 macOS 自带的。
#
# 清单在调用者的 shell 里算 —— 不在 `< <(...)` 子 shell 里。子 shell 里 exit
# 只杀子 shell, 外层 while 读到空输入 = 一个都没签, 然后一路绿到 Apple 那边。
build_macho_list() {
  local root="$1" out="$2"
  find "$root" -type f >"$out.all"
  perl -ne '
    chomp;
    open(my $fh, "<", $_) or next;
    binmode $fh;
    read($fh, my $m, 8) or next;
    close $fh;
    my $h = lc unpack("H*", substr($m, 0, 4));
    # 瘦二进制: 32/64 位, 大小端各一
    if ($h =~ /^(cffaedfe|cefaedfe|feedface|feedfacf)$/) { print "$_\n"; next; }
    # 胖二进制 (universal): cafebabe 跟 Java .class 撞魔数, 用紧跟的
    # nfat_arch 区分 —— Mach-O 的架构数是个位数, Java 的 major_version 从 45 起
    if ($h eq "cafebabe") { my $n = unpack("N", substr($m,4,4)); print "$_\n" if $n >= 1 && $n <= 16; next; }
    if ($h eq "bebafeca") { my $n = unpack("V", substr($m,4,4)); print "$_\n" if $n >= 1 && $n <= 16; next; }
  ' "$out.all" >"$out"
  rm -f "$out.all"
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

  # bundle 的封装单独验 —— 上面那圈只看**文件**, 而 .app / .framework 是否
  # 被正确封口是另一回事: 只要签完 bundle 之后又动了它肚子里的东西, 封条就废了,
  # 而每个文件自己的签名依然完好。这正是"由内向外"顺序写错时的表现, 逐文件检查
  # 一个都抓不到。
  local bundle
  while IFS= read -r bundle; do
    if ! codesign --verify --strict "$bundle" >/dev/null 2>&1; then
      echo "   ✗ bundle 封装无效 (八成是签名顺序不对) · $bundle"
      bad=$((bad + 1))
    fi
  done < <(find "$root" \( -name '*.app' -o -name '*.framework' \) -type d)

  if [[ $bad -gt 0 ]]; then
    echo "❌ $bad/$checked 个二进制没签好 —— 提交上去必被驳回, 就地停" >&2
    exit 1
  fi
  echo "   自检通过 · $checked 个 Mach-O 全部满足 Developer ID + 时间戳 + hardened runtime"
}

# ## 4. wheel 里面还有一层 —— .whl 就是 zip, Apple 也会拆
#
# 8/5 第三次驳回。这一轮新加的 hermes-deps-dist.tar.gz 里躺着 4 个没签的
# Mach-O, 全在 wheel **内部** (实地拆开数的, 不是推测):
#
#   greenlet/_greenlet.cpython-311-darwin.so
#   greenlet/tests/_test_extension.cpython-311-darwin.so
#   greenlet/tests/_test_extension_cpp.cpython-311-darwin.so
#   playwright/driver/node          ← 一整个 115MB 的 Node 二进制
#
# 而脚本对这个归档打的是"共签 0 个" —— 因为它只拆 .tar.gz。Apple 连 zip 一起拆,
# 所以它看得见我看不见的东西。这就是本地全绿、提交必挂的原因。
#
# 重打包 wheel 要动两处, 少一处就是新的坑:
#   · RECORD —— wheel 规范里记着每个文件的 sha256 和字节数。签名会改文件,
#     不更新 RECORD 就是发一个自相矛盾的 wheel。装的时候校不校验取决于
#     pip/uv 的实现, 我在 Linux 沙箱里装不了 macOS wheel, **验不了**;
#     那就别赌 —— 直接把 RECORD 改对, 不管谁校验都成立。
#   · 可执行位 —— playwright/driver/node 是 -rwxr-xr-x, 掉了就起不来。
#     实测 unzip → zip 往返保留权限位 (所以不能用 zip -X)。
#
# 只碰真的含 Mach-O 的 wheel。jieba / pyee / typing_extensions 是纯 Python,
# 原样保留、连解压都不解 —— 不动的东西不会坏。
update_wheel_record() {
  local wdir="$1" rec
  rec="$(find "$wdir" -maxdepth 2 -path '*.dist-info/RECORD' | head -1)"
  if [[ -z "$rec" ]]; then
    echo "❌ wheel 里找不到 .dist-info/RECORD: $wdir" >&2
    exit 1
  fi
  perl -e '
    use strict; use warnings;
    use Digest::SHA;
    use MIME::Base64 qw(encode_base64);
    my ($root, $rec) = @ARGV;
    open(my $in, "<", $rec) or die "open $rec: $!";
    my @out;
    while (my $l = <$in>) {
      chomp $l;
      # RECORD 每行是  路径,sha256=<base64url无填充>,字节数
      # RECORD 自己那行是 "....dist-info/RECORD,," (哈希为空), 匹配不上, 原样留下
      if ($l =~ /^(.*),sha256=[A-Za-z0-9_\-]*,\d+$/) {
        my $p = $1;
        my $f = "$root/$p";
        if (-f $f) {
          my $sha = Digest::SHA->new(256);
          $sha->addfile($f);
          my $b = encode_base64($sha->digest, "");
          $b =~ tr{+/}{-_};        # base64 -> base64url
          $b =~ s/=+$//;           # wheel 规范: 去掉填充
          $l = "$p,sha256=$b," . (-s $f);
        }
      }
      push @out, $l;
    }
    close $in;
    open(my $o, ">", $rec) or die "write $rec: $!";
    print $o "$_\n" for @out;
    close $o;
  ' "$wdir" "$rec"
}

sign_wheels() {
  local root="$1" total=0 n whl wdir
  local list="$WORK_DIR/whl.macho"
  while IFS= read -r whl; do
    wdir="$WORK_DIR/whl-unpack"
    rm -rf "$wdir"; mkdir -p "$wdir"
    ( cd "$wdir" && unzip -qq -o "$whl" )
    build_macho_list "$wdir" "$list"
    n="$(wc -l <"$list" | tr -d ' ')"
    if [[ "$n" -eq 0 ]]; then
      echo "   · $(basename "$whl") 纯 Python, 原样保留 (不解不打, 不动就不会坏)"
      rm -rf "$wdir"
      continue
    fi
    echo "   · $(basename "$whl") 内含 $n 个 Mach-O, 逐个签名 + 重写 RECORD"
    while IFS= read -r f; do sign_one "$f"; done <"$list"
    verify_tree "$wdir"
    update_wheel_record "$wdir"
    rm -f "$whl"
    ( cd "$wdir" && zip -qq -r "$whl" . )   # 不加 -X, 要保住可执行位
    rm -rf "$wdir"
    total=$((total + n))
  done < <(find "$root" -type f -name '*.whl')
  [[ "$total" -gt 0 ]] && echo "   wheel 内共签 $total 个"
  return 0
}

sign_archive() {
  local archive="$1"
  local name="$(basename "$archive")"
  local unpack="$WORK_DIR/${name%.tar.gz}"
  local repacked="$WORK_DIR/$name"

  echo "→ 解压并签名 · $archive"
  mkdir -p "$unpack"
  tar xzf "$archive" -C "$unpack"
  sign_wheels "$unpack"          # 先处理嵌套的 zip, 再签外层散文件
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

# 8/5: 直接遍历 Contents/Resources 里的 Mach-O。
#
# Apple 公证驳回 (submission 8c3f698a) 的唯一硬错误就是这个:
#   Contents/Resources/catfish-calendar —— The binary is not signed.
#   (x86_64 和 arm64 两个架构各报 3 条: 没签名 / 没安全时间戳 / 没 hardened runtime)
#
# 原来只签 Contents/Resources/resources/mac/, 而 catfish-calendar 在**上一层**,
# 于是从来没被签过 —— 而且构建、签 App、做 dmg、spctl 全部一路绿, 直到提交给
# Apple 才炸。又是一个"本地全过、外部才发现"。
#
# 非 Mach-O (例如 mac 资源里的文本和归档) 自然跳过。
# Windows 资源由 tauri.windows.conf.json 只在 Windows 构建时加入，不能依赖
# 这里的 Mach-O 过滤来掩盖跨平台资源混包。
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
# 只扫描 resources/mac。Windows 归档属于另一条构建产线，不能在 macOS 签名时
# 解压，也不能把占位文件是否可识别交给签名脚本猜。
MAC_RUNTIME_RESOURCES="$MAC_RESOURCES/resources/mac"
[ -d "$MAC_RUNTIME_RESOURCES" ] || {
  echo "✗ 找不到 macOS runtime 资源目录: $MAC_RUNTIME_RESOURCES" >&2
  exit 1
}

echo "=== 签名归档内运行时 ==="
while IFS= read -r -d '' archive; do
  sign_archive "$archive"
done < <(find "$MAC_RUNTIME_RESOURCES" -type f -name '*.tar.gz' -print0)

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

# 8/5: 审核 Accepted 之后立刻 staple 会扑空。
#
#     CloudKit query ... failed due to "Record not found"
#     The staple and validate action failed! Error 65.
#
# notarytool --wait 返回的是"**审核**通过", 而 stapler 要去 CloudKit
# (api.apple-cloudkit.com/.../ticket-delivery) 取票据 —— 票据发布到 CDN 比
# 审核完成晚一步。实测签名时间戳 12:07:14, 12:12 查还是 NOT_FOUND。
#
# 这跟包本身无关, 重编译、重公证都没用, 只能等票据上架。所以改成重试。
#
# 达华现场无外网, 这一步**不能跳**: 没装订的话 Gatekeeper 要联网向 Apple
# 查公证记录才放行, 离线机器直接被拦。装订就是把票据钉进 dmg, 让它自己能验。
echo "=== 装订公证票据 ==="
STAPLE_TRIES=20        # 20 × 60s = 最多等 20 分钟
STAPLE_OK=0
for i in $(seq 1 "$STAPLE_TRIES"); do
  if xcrun stapler staple "$DMG_PATH"; then
    STAPLE_OK=1
    break
  fi
  if [[ "$i" -lt "$STAPLE_TRIES" ]]; then
    echo "   票据还没上架 (第 $i/$STAPLE_TRIES 次), 60 秒后重试 ——"
    echo "   审核已经 Accepted, 这只是 Apple 那边 CDN 的时间差, 包没问题"
    sleep 60
  fi
done
if [[ "$STAPLE_OK" -ne 1 ]]; then
  echo "❌ 等了 $STAPLE_TRIES 分钟票据仍未上架。dmg 本身是好的 (审核 Accepted),"
  echo "   过一会儿单独跑这条即可, **不用重新编译公证**:"
  echo "     xcrun stapler staple \"$DMG_PATH\" && xcrun stapler validate \"$DMG_PATH\"" >&2
  exit 1
fi
xcrun stapler validate "$DMG_PATH"

echo "✅ 完成：$DMG_PATH"
