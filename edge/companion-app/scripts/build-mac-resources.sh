#!/bin/bash
# build-mac-resources.sh · BL-MAC-INSTALL-NODE+CHROMIUM-BUNDLE (7/17)
#
# 一键打全 mac dmg 用的 resources/mac-<arch>/ 运行时 (10/14 起, hermes v0.22.0):
#   1. uv                            (钉 .uv-version, 跟上游 pm/lock.json 一致)
#   2. cpython-3.14.7-embed.tar.gz   (python-build-standalone 20260901 · macOS arm64 or x64)
#   3. hermes-agent-bundle.tar.gz    (源码 + catfish 插件 + wheelhouse/ + catfish-requirements.txt
#                                     + install-stamp.json; 不含 node_modules)
#   4. chromium-embed.tar.gz         (wheelhouse 里那版 Python playwright 下的 Chromium)
# 不再有 install.sh (上游安装器整个换了, Companion 自己装) 和 hermes-deps-dist (额外包并进 wheelhouse)。
#
# 10/9 瘦身: 不再打 node-embed.tar.gz, hermes 归档里也不再有 node_modules。
# 员工机上 hermes 只跑 Python 网关 + 鲶鱼工具, 实测 (10/9, 去掉 node_modules 且 PATH
# 里没有 node/npx/npm) 聊天 / 浏览器 / 知识库 / 邮件 / 文件 40 次工具调用全过。
# 鲶鱼的浏览器是 catfish_browser_* (Python Playwright + 下面的 Chromium); hermes 自带
# 的 browser_* 靠 agent-browser, 而包里的 agent-browser 从来没有 .bin 链接、hermes
# 找不到它 (现网日志 check_browser_navigate_requirements 一直 False), 网关 BL-FIX4
# 也会在 catfish_browser_* 在场时把那一族丢掉 —— 那 376MB node_modules + 58MB Node
# 在员工机上是纯负担。10/14 起构建机上也不再需要 Node。
#
# 用法:
#   # 打 Apple Silicon (aarch64) 资源:
#   bash scripts/build-mac-resources.sh aarch64
#
#   # 打 Intel (x64) 资源 (在 Apple Silicon mac 上用 rosetta 2):
#   bash scripts/build-mac-resources.sh x64
#
# 完了后跑:
#   npm run tauri build              # aarch64 dmg
#   npm run tauri build -- --target x86_64-apple-darwin  # x64 dmg

set -euo pipefail

ARCH="${1:-aarch64}"
if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "x64" ]; then
    echo "❌ 用法: $0 aarch64|x64"
    exit 1
fi

# arch mapping
if [ "$ARCH" = "aarch64" ]; then
    UV_ARCH="aarch64-apple-darwin"
    PY_ARCH="aarch64-apple-darwin"
else
    UV_ARCH="x86_64-apple-darwin"
    PY_ARCH="x86_64-apple-darwin"
fi

echo "==============================================="
echo "  build-mac-resources.sh · arch=$ARCH"
echo "  uv=$UV_ARCH · py=$PY_ARCH"
echo "==============================================="

# ─── 下载 helper (download_with_retry) · 10/14 抽到 lib-download.sh, Windows 资源脚本共用 ─────
. "$(cd "$(dirname "$0")" && pwd)/lib-download.sh"

# 项目根
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPANION="$(cd "$SCRIPT_DIR/.." && pwd)"
# P3.5.83 (7/29): 输出目录**按架构分开**, 不再共用 resources/mac/。
#
# ── 为什么改 ────────────────────────────────────────────────────────
#
# 老做法是两个架构都往 resources/mac/ 写, 打 Intel 包时先把 arm64 那份改名
# 成 mac.arm64.bak 再重建。于是"当前 mac/ 里是哪个架构"完全靠人记, 脚本
# 中途挂掉或者忘了还原, 下一次打 arm64 就会把 x86 的 Python / node / hermes
# 装进 aarch64 的 dmg。
#
# 这不是假设: 7/16 那次 Intel build 之后没还原, 到 7/29 发现时
# resources/mac/uv 是 x86_64、node 是 darwin-x64 —— **中间十三天打出的每个
# aarch64 dmg 都是错的**。它没当场炸是因为 macOS 有 Rosetta 2 透明转译,
# 于是"能装能跑, 只是慢", 没有任何一处会报错。企业里若禁装 Rosetta,
# hermes 直接起不来。
#
# 现在两个架构各有各的目录, 同时存在、互不覆盖, 也不需要备份和还原。
# 打包时由 tauri 配置覆盖 (tauri.aarch64.conf.json / tauri.x64.conf.json)
# 指定用哪一份, 见 package.json 的 tauri:build:arm64 / tauri:build:x64。
RESOURCES="$COMPANION/src-tauri/resources/mac-$ARCH"
HERMES_TAG=$(cat "$COMPANION/.hermes-git-tag" | tr -d '[:space:]')
HERMES_COMMIT=$(cat "$COMPANION/.hermes-git-commit" | tr -d '[:space:]')

mkdir -p "$RESOURCES"
cd "$COMPANION"

# ─── 1. 取 hermes 源码 + 拷 catfish 插件 ─────────────────

HERMES_SRC="/tmp/hermes-agent-src-$ARCH"

echo ""
echo "=== [1/6] Clone hermes-agent tag $HERMES_TAG · copy catfish plugins ==="
# 取源码这一整块 (缓存 / clone / 代理兜底 / 落点核对 / 存缓存) 抽到隔壁了。
# source 而不是子进程 —— 它要写 HERMES_SRC 这些变量。
. "$COMPANION/scripts/fetch-hermes-src.sh"

# copy catfish plugins
mkdir -p "$HERMES_SRC/plugins/memory"
for plugin in catfish-memory; do
    src="../hermes-plugins/$plugin"
    if [ -d "$src" ]; then
        # 10/1: 只拷 git 跟踪的文件。原来 cp -R 整个目录, 把开发机上 .gitignore 掉的
        # venv/ (21M · 1600+ 文件, 链到本机 python3.12, 客户机上用不了, 还带着开发机的
        # 绝对路径和用户名)、__pycache__、.pytest_cache 一起打进了安装包, 签名时也多签一堆。
        dest="$HERMES_SRC/plugins/memory/$plugin"
        mkdir -p "$dest"
        n="$(git -C "$src" ls-files | wc -l | tr -d ' ')"
        [ "$n" -gt 0 ] || { echo "❌ $src 里没有 git 跟踪的文件 (不是在仓库里跑?)" >&2; exit 1; }
        (cd "$src" && git ls-files -z | xargs -0 tar cf -) | (cd "$dest" && tar xf -)
    # 10/15: 插件读的两份受控词表在 edge/contracts/, 装好后插件旁边没有它 —— 随包放进插件目录
    mkdir -p "$dest/contracts"
    cp "../contracts"/wiki_relation_vocab.json "../contracts"/wiki_type_vocab.json "$dest/contracts/" || exit 1
        echo "  copied $plugin ($n 个 git 跟踪的文件, 不含 venv / 缓存)"
    fi
done

# ─── catfish-email 资源包 (wheel + skill + AppleScript 编译检查) ─────────────
# 9/28 拆到 build-email-resource.sh (本文件贴着 800 行红线), 来龙去脉的注释在那边。
bash "$COMPANION/scripts/build-email-resource.sh" "$ARCH" "$RESOURCES" || exit 1

# 安全导出文件读取器：独立零依赖 wheel，避免员工机依赖系统 Python。
bash "$COMPANION/scripts/build-wechat-reader-resource.sh" "$ARCH" "$RESOURCES"

# 10/14 (hermes v0.22.0): 不再打 install.sh, 也不再下 Node。
#   · 上游 install.sh 整个重写成"拿 uv + Python → 交给 hermes pm 联网装", 离线补丁的锚点全部对不上;
#     Companion 自己装 (src-tauri/src/commands/hermes_install_native.rs), 用下面 [5.5] 打进源码树的 wheelhouse。
#   · Node 原来只给 [6b] 的 npx playwright 下 Chromium 用; 现在用 wheelhouse 里那个 Python playwright 自己下,
#     浏览器版本跟员工机上装的 playwright 天然一致 (原来 npm 1.60.0 / Python 1.62.0 两个版本对不上)。
# 上一次构建留下的旧产物必须删掉 —— 否则 tauri 照 resources 映射打进去。
rm -f "$RESOURCES/install.sh" "$RESOURCES/node-embed.tar.gz" "$RESOURCES/hermes-deps-dist.tar.gz" \
      "$RESOURCES"/cpython-3.11.*-embed.tar.gz

# ─── 4. uv binary ───────────────────────────────────────

UV_VERSION="$(tr -d '[:space:]' < "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.uv-version")"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_ARCH}.tar.gz"

echo ""
echo "=== [4/6] Download uv $UV_VERSION ($UV_ARCH) ==="
UV_TMP="/tmp/uv-$ARCH.tar.gz"
# 走 ghfast.top 代理 · 修 truncated 挂根因
download_with_retry "$UV_URL" "$UV_TMP" || exit 1
UV_EXTRACT="/tmp/uv-extract-$ARCH"
rm -rf "$UV_EXTRACT" && mkdir -p "$UV_EXTRACT"
tar -xzf "$UV_TMP" -C "$UV_EXTRACT"
cp "$UV_EXTRACT/uv-$UV_ARCH/uv" "$RESOURCES/uv"
chmod +x "$RESOURCES/uv"
echo "  OK $RESOURCES/uv ($(ls -lh "$RESOURCES/uv" | awk '{print $5}'))"

# ─── 5. cpython 3.14.7 ─────────────────────────────────

# 10/14: 3.11.15 → 3.14.7。hermes v0.22.0 的核心依赖全部带 `python_version >= '3.14'` 标记,
# 3.11 上 pip install 一个依赖都不装 (实测)。3.14.7+20260901 跟上游 pm/lock.json 钉的是同一份,
# 自带 SQLite 3.53.1 (不在 WAL-reset 漏洞范围内, 下面 [5.5] 的闸照样复核)。
PYTHON_VERSION="3.14.7"
# 8/8: 20260623 → 20260807, 为的是 **SQLite**, 不是 Python 本身。
# 同一个 CPython 3.11.15, 不同的 pbs 发布链的 SQLite 不一样: 前者 3.50.4
# (在 WAL-reset 损坏漏洞范围内), 后者 3.53.1。
#
# 先试过事后修 (scripts/repair-hermes-sqlite.sh), 能修好但**被 Companion
# bootstrap 冲掉了** —— 它会无条件重建 venv 并删 .hermes-runtime。"装完再修"
# 在这套架构里不成立, 只能从源头带好的。
#
# 20260807 是从实际修好的 runtime 的 BUILD 文件读出来的, 不是查文档猜的:
#   ~/.hermes/hermes-agent/.hermes-runtime/python/generation-*/cpython-3.11.15-*/BUILD
# 换 tag 换出有洞的版本, 下面 [5.5] 的闸会当场红。
PYTHON_BUILD_TAG="20260901"
PY_FNAME="cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-${PY_ARCH}-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/${PY_FNAME}"

echo ""
echo "=== [5/6] Download cpython $PYTHON_VERSION ($PY_ARCH) ==="
PY_TMP="/tmp/cpython-$ARCH.tar.gz"
# 走 ghfast.top 代理
download_with_retry "$PY_URL" "$PY_TMP" || exit 1
cp "$PY_TMP" "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz"
echo "  OK cpython-embed.tar.gz ($(ls -lh "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz" | awk '{print $5}'))"

# ─── 5.5 hermes venv 的额外 Python 依赖 (jieba / playwright / 9/28 起加文件解析) ──
#
# 8/5 鸿波「MACOS 怎么安装了新包, 为什么会缺」。查下来: 装机流程里
# jieba 和 playwright 的安装语句**是 0 处** —— 从来没打进过包。
#
# 后果 (autostart.rs 的两条自检早就写着, 只是没人去装):
#   · 缺 playwright → catfish_browser_* 全部不可用, 员工让鲶鱼开网页得到
#     「缺 playwright 包, 导航没走成」
#   · 缺 jieba      → 文书风格分词退化成字符二元组, top_words 变成
#     「覆盖 绩材 台账」这类碎片, 而它要注进 system prompt
#
# 达华现场无外网, 员工机不可能 pip install —— 必须随包带。
#
# ## 为什么放在第 5 段之后, 不跟 catfish-email 放一起
#
# catfish-email 那段在 218 行, 那时 cpython 还没下载。而这里**必须用包里
# 那个解释器**去取 wheel:
#
#   playwright 和它的依赖 greenlet 是**平台 + CPython 版本专属** wheel。
#   构建机的 python3 可能是 3.13, 而员工机上跑的是包里嵌的 3.11.15。
#   用构建机 python 取到的 wheel 装不进 3.11 的 venv —— 而且是在**员工
#   机器上**才失败, 正是这两天一直在修的那种"晚一步才炸"。
#
# 所以解压刚下好的 cpython, 用它自己的 pip download。Python 版本标签
# 自然对; 平台只再压一下最低系统版本 (见下面 9/28 那段)。
echo ""
echo "=== [5.5/6] hermes 离线 wheelhouse + 安装戳 (打进源码树, 跟 hermes-agent-bundle 一起走) ==="
PY_UNPACK="/tmp/catfish-py-unpack-$ARCH"
rm -rf "$PY_UNPACK" && mkdir -p "$PY_UNPACK"
tar xzf "$PY_TMP" -C "$PY_UNPACK" || { echo "❌ 解压 cpython 失败"; exit 1; }
EMBED_PY="$PY_UNPACK/python/bin/python3"
[ -x "$EMBED_PY" ] || { echo "❌ 找不到嵌入解释器: $EMBED_PY"; exit 1; }
echo "  嵌入解释器: $("$EMBED_PY" -V)"

# 包里这个解释器的 SQLite 不许有 WAL-reset 漏洞 (8/8)。理由和判据见那个脚本。
echo "  → 复核内嵌 Python 的 SQLite..."
"$EMBED_PY" "$COMPANION/scripts/check-embedded-sqlite.py" || exit 1

# hermes 依赖 (上游 uv.lock, --extra all) + catfish 额外包 (src-tauri/hermes-extra-packages.txt)
# 按目标平台一起钉死版本 + sha256, 取 wheel; C 扩展没有 3.14 轮子的 (watchdog) 用嵌入解释器现编,
# 所以一定要用**目标架构**的 EMBED_PY 跑 (x64 在 Apple 芯片上走 Rosetta)。原理见脚本文件头。
HOST_ARGS=()
if [ "$ARCH" = "x64" ]; then
    # 10/14: Intel Mac 缺 cryptography (49 起不发 Intel 轮子) / greenlet 的 3.14 轮子, 要现编。
    # cryptography 是 Rust (maturin): 用构建机原生 Python 交叉编到 x86_64, 静态链 OpenSSL。
    # 详见 scripts/build_hermes_wheelhouse.py 的 SOURCE_BUILD / RUST_BUILD。
    command -v rustup >/dev/null && rustup target list --installed | grep -qx x86_64-apple-darwin || {
        echo "❌ 打 Intel 包要 Rust 的 x86_64-apple-darwin 目标: rustup target add x86_64-apple-darwin"; exit 1; }
    OSSL_VERSION="3.5.9"   # 3.5 LTS; 换版本就会重编
    OSSL_DIR="/tmp/catfish-openssl-x64-$OSSL_VERSION"
    # 10/19: 同上, 系统清 /tmp 可能只删掉一部分 (比如头文件) —— 库和关键头文件都在才算缓存可用, 不全就重编
    for f in lib/libcrypto.a lib/libssl.a include/openssl/ssl.h include/openssl/opensslv.h; do
        [ -f "$OSSL_DIR/$f" ] || { [ -d "$OSSL_DIR" ] && echo "  ⚠ OpenSSL 缓存缺 $f, 重编"; rm -rf "$OSSL_DIR"; break; }
    done
    if [ ! -f "$OSSL_DIR/lib/libcrypto.a" ]; then
        echo "  → 编 Intel 版静态 OpenSSL $OSSL_VERSION (一次, 之后用缓存 $OSSL_DIR)..."
        OSSL_SRC="/tmp/catfish-openssl-src-$OSSL_VERSION"
        rm -rf "$OSSL_SRC" && mkdir -p "$OSSL_SRC"
        download_with_retry "https://github.com/openssl/openssl/releases/download/openssl-$OSSL_VERSION/openssl-$OSSL_VERSION.tar.gz" \
            "$OSSL_SRC/openssl.tar.gz"
        tar xzf "$OSSL_SRC/openssl.tar.gz" -C "$OSSL_SRC"
        ( cd "$OSSL_SRC/openssl-$OSSL_VERSION" && MACOSX_DEPLOYMENT_TARGET=10.15 \
            ./Configure darwin64-x86_64-cc no-shared no-tests --prefix="$OSSL_DIR" --libdir=lib >/dev/null \
            && make -j8 >/dev/null 2>&1 && make install_sw >/dev/null ) || { echo "❌ 编 OpenSSL 失败"; exit 1; }
        rm -rf "$OSSL_SRC"
    fi
    export OPENSSL_DIR="$OSSL_DIR"
    # 构建机原生 (arm64) Python, 只给 maturin 交叉编用
    HOST_PY_DIR="/tmp/catfish-host-py-$PYTHON_VERSION"
    # 10/19: 只看 bin/python3 在不在不够 —— macOS 定期清 /tmp 里久没碰的文件, 会把标准库删掉一半、留下可执行文件,
    # 之后每次打包都拿这个坏解释器编 cryptography, 报 "No module named 'encodings'"。真跑一下, 坏了就整个重下。
    if [ -x "$HOST_PY_DIR/python/bin/python3" ] && ! "$HOST_PY_DIR/python/bin/python3" -c "import encodings, ssl, sqlite3" >/dev/null 2>&1; then
        echo "  ⚠ $HOST_PY_DIR 里的 Python 不完整 (多半被系统清 /tmp 删了文件), 重新下载"
        rm -rf "$HOST_PY_DIR"
    fi
    if [ ! -x "$HOST_PY_DIR/python/bin/python3" ]; then
        mkdir -p "$HOST_PY_DIR"
        download_with_retry "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-aarch64-apple-darwin-install_only.tar.gz" \
            "$HOST_PY_DIR/py.tar.gz"
        tar xzf "$HOST_PY_DIR/py.tar.gz" -C "$HOST_PY_DIR" && rm -f "$HOST_PY_DIR/py.tar.gz"
    fi
    HOST_ARGS=(--host-python "$HOST_PY_DIR/python/bin/python3")
fi
"$EMBED_PY" "$COMPANION/scripts/build_hermes_wheelhouse.py" \
    --src "$HERMES_SRC" --uv "$RESOURCES/uv" --target "$UV_ARCH" --python "$EMBED_PY" ${HOST_ARGS[@]+"${HOST_ARGS[@]}"} || {
    echo "❌ 打 wheelhouse 失败 (需要外网; 原因见上面的报错)"
    exit 1
}
# 安装戳: updateMechanism=external —— 告诉 hermes"更新归 Companion 管"。没有它 (或者有个空 .git)
# hermes v0.22 会把 ~/.hermes/hermes-agent 认领成自更新安装, 每次启动去联网下 Python 3.14 另建环境。
"$EMBED_PY" "$HERMES_SRC/scripts/write_install_stamp.py" --output "$HERMES_SRC/install-stamp.json" \
    --commit "$HERMES_COMMIT" --branch main --base-version "${HERMES_TAG#v}" --distance 0 \
    --source catfish --update-mechanism external || { echo "❌ 写安装戳失败"; exit 1; }
python3 "$(dirname "$0")/build_edge_runtime.py" "$RESOURCES/catfish-edge-runtime.tar.gz" || { echo "❌ edge-runtime 打包失败 (tool-bridge/local-search/文件解析脚本, 见 edge_runtime.rs)"; exit 1; }

# ─── 6. Chromium (Python playwright 自己下) · tar 打包 ─────

echo ""
echo "=== [6b/6] playwright install chromium (arch=$ARCH) ==="
# 10/14: 用 wheelhouse 里那一版 Python playwright 下, 不再用 npx (见 [2] 那段)。
# 版本由 hermes-extra-packages.txt 里钉的 playwright 决定 —— 换浏览器版本 = 改那一行, 是有意识的动作。
PW_VENV="/tmp/catfish-pw-venv-$ARCH"
rm -rf "$PW_VENV"
"$RESOURCES/uv" venv -q "$PW_VENV" --python "$EMBED_PY"
VIRTUAL_ENV="$PW_VENV" "$RESOURCES/uv" pip install -q --offline --no-index \
    --find-links "$HERMES_SRC/wheelhouse" --require-hashes -r "$HERMES_SRC/catfish-requirements.txt"
PW_VERSION="$("$PW_VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("playwright"))')"
echo "  playwright $PW_VERSION (跟员工机上装的同一版)"
unset PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD  # 允许下载 chromium
PW_CACHE="/tmp/catfish-pw-$ARCH-$PW_VERSION"
echo "  下载目录: $PW_CACHE (每架构 / 每版本独立, 不碰 ~/Library/Caches/ms-playwright)"
mkdir -p "$PW_CACHE"
case "$ARCH" in
    aarch64) PW_PLATFORM="mac15-arm64" ;;
    x64)     PW_PLATFORM="mac15" ;;
esac
echo "  PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=$PW_PLATFORM"
PLAYWRIGHT_BROWSERS_PATH="$PW_CACHE" \
PLAYWRIGHT_HOST_PLATFORM_OVERRIDE="$PW_PLATFORM" \
    # 10/19: 网络抖一下 (TLS 握手被断) 整个打包就失败 —— 跟别的下载一样重试 3 次
    pw_ok=""
    for pw_try in 1 2 3; do
        "$PW_VENV/bin/python" -m playwright install chromium && { pw_ok=1; break; }
        echo "  ⚠ 下 Chromium 第 $pw_try 次失败, 30 秒后重试"
        sleep 30
    done
    [ -n "$pw_ok" ] || { echo "❌ 下 Chromium 失败 (重试 3 次)"; exit 1; }

echo ""
echo "=== [6c/6] tar pack chromium-embed · from $PW_CACHE ==="
if [ ! -d "$PW_CACHE" ]; then
    echo "❌ Playwright 下载目录不存在: $PW_CACHE"
    exit 1
fi
CHROMIUM_TAR="$RESOURCES/chromium-embed.tar.gz"
cd "$PW_CACHE"
# BL-CHROMIUM-LATEST-ONLY (7/17 修 build-mac-resources dmg 挂根因):
# 老 script tar chromium* 会打**所有历史版本** (1200/1208/1217/1228 · 4 版 950MB),
# 应该只打**最新版本** (跟刚 npx playwright install 装的一致).
# sort -V 版本号排序 · tail -1 挑最大.
LATEST_CHROMIUM=$(ls -1d chromium-* 2>/dev/null | grep -v headless_shell | sort -V | tail -1)
LATEST_HEADLESS=$(ls -1d chromium_headless_shell-* 2>/dev/null | sort -V | tail -1)
if [ -z "$LATEST_CHROMIUM" ]; then
    echo "❌ 无 chromium-* 目录 in $PW_CACHE"
    exit 1
fi
echo "  只打最新版本 (省 700MB):"
echo "    chromium:        $LATEST_CHROMIUM"
echo "    headless_shell:  ${LATEST_HEADLESS:-无 · skip}"
# 老版本目录若在 · 提示 (但不主动删 · 别 npm cache 突然缺 chromium 挂)
OLD_COUNT=$(ls -1d chromium-* 2>/dev/null | grep -v headless_shell | wc -l | tr -d ' ')
if [ "$OLD_COUNT" -gt 1 ]; then
    echo "  ⚠ 检到 $OLD_COUNT 个 chromium-* 老版本 · 可手动清理:"
    echo "    ls -1d chromium-* | grep -v headless_shell | sort -V | head -n -1 | xargs rm -rf"
fi

# ── 打包前验架构 (7/30) ────────────────────────────────────────────
#
# verify-app-arch.sh 也验这个, 但那是在 cargo build 之后 —— 错了要白等一分多钟。
# 更要紧的是: 下载目录一旦复用, 它**可能验不出来**(缓存恰好是对的架构),
# 所以这里在源头再钉一次, 不合就当场停。
case "$ARCH" in
    aarch64) WANT_MACHO="arm64"  ; WANT_DIR="chrome-mac-arm64" ;;
    x64)     WANT_MACHO="x86_64" ; WANT_DIR="chrome-mac-x64"   ;;
esac
# 第一道: 解压出来的目录名自带架构 (playwright 的 executablePath 表:
#   'mac-x64'   → chrome-mac-x64/Google Chrome for Testing.app/...
#   'mac-arm64' → chrome-mac-arm64/Google Chrome for Testing.app/...)
# 这道能在 override 值写错时立刻暴露, 比只看 Mach-O 更早也更直白。
if [ ! -d "$LATEST_CHROMIUM/$WANT_DIR" ]; then
    echo "❌ 没有 $WANT_DIR/ —— 下的不是 $ARCH 的 chromium"
    echo "   实际有: $(ls -1 "$LATEST_CHROMIUM" 2>/dev/null | tr '\n' ' ')"
    echo "   删掉重跑: rm -rf $PW_CACHE && bash scripts/build-mac-resources.sh $ARCH"
    exit 1
fi
# 不写死 chrome-mac/ 这层 —— playwright 改过目录布局, 写死会在改版时静默失配。
# 跟 verify-app-arch.sh 一样按主程序名找。
CHROME_BIN="$(find "$LATEST_CHROMIUM" -type f \
    -path "*Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" \
    2>/dev/null | head -1)"
if [ -z "$CHROME_BIN" ] || [ ! -f "$CHROME_BIN" ]; then
    echo "❌ 找不到 chromium 主程序: $CHROME_BIN"
    echo "   playwright 的目录结构可能变了 —— 不确认架构就不能打包。"
    exit 1
fi
CHROME_ARCH="$(file -b "$CHROME_BIN" 2>/dev/null || true)"
if ! echo "$CHROME_ARCH" | grep -q "$WANT_MACHO"; then
    echo "❌ chromium 架构不符: 期望 $WANT_MACHO, 实际 → $CHROME_ARCH"
    echo "   下载目录: $PW_CACHE"
    echo "   多半是这个目录里残留了别的架构 —— 删掉重跑:"
    echo "     rm -rf $PW_CACHE && bash scripts/build-mac-resources.sh $ARCH"
    exit 1
fi
echo "  ✓ chromium 架构核对: $WANT_MACHO"
if [ -n "$LATEST_HEADLESS" ]; then
    tar czf "$CHROMIUM_TAR" "$LATEST_CHROMIUM" "$LATEST_HEADLESS"
else
    tar czf "$CHROMIUM_TAR" "$LATEST_CHROMIUM"
fi
echo "  OK $CHROMIUM_TAR ($(ls -lh "$CHROMIUM_TAR" | awk '{print $5}'))"

echo ""
echo "=== [6d/6] tar pack hermes-agent bundle (Companion runtime profile) ==="
# Companion 使用 Hermes 的 Python gateway/tool/plugin 运行链路, 不需要桌面
# Electron、前端构建、测试依赖。排除规则作用于 tar 输入, 不改动 HERMES_SRC。
#
# 10/9: node_modules 整个不进包 (见文件头), agent-browser 跟着一起不再打。
# --exclude patterns 支持 shell glob, 无需 leading 路径.
cd /tmp
HERMES_TAR="$RESOURCES/hermes-agent-bundle.tar.gz"
# 8/1: 补 node_modules/electron —— 排 apps/desktop 却留着它的运行时
#
# 上面那条 7/17 的注释说排 apps/desktop 是因为"我们用 Tauri Companion 替代它,
# 完全不用"。但 hermes 是 npm workspaces (apps/* / ui-tui / web / tests-js),
# apps/desktop 的依赖被**提升到根 node_modules**。所以 app 排掉了, 它那个
# 790MB 的 Electron 运行时原封不动留在包里 —— 占整个归档解压后的 56%,
# 而没有任何使用者。
#
# 能排掉的证据链 (8/1 在 0.19.0 打出来的实际归档上查的):
#   · 根 package.json 的四个依赖段都没有 electron
#   · 保留下来的 6 个 workspace package.json, 没有一个依赖 electron
#   · 归档里 hermes_cli/ plugins/ apps/ 下带 electron 字样的路径: 0 条
# 也就是说它只从 apps/desktop 来, 而 apps/desktop 已经排掉了。
#
# 8/27: 这些规则以前在 [6a.5] 通过 rm -rf 实施; 现在改为只作用于归档。
BUNDLE_EXCLUDES=(
    "--exclude=hermes-agent-src-$ARCH/.git"
    "--exclude=hermes-agent-src-$ARCH/venv"
    "--exclude=hermes-agent-src-$ARCH/venv.bak"
    "--exclude=hermes-agent-src-$ARCH/.venv"
    "--exclude=hermes-agent-src-$ARCH/target"
    "--exclude=hermes-agent-src-$ARCH/apps/desktop"
    # 10/9: 任何层级的 node_modules 都不进包 (见文件头)。[6a] 已经不装了, 这里再挡
    # 一道: 源码缓存 / 手工调试留下的 node_modules 也不能混进来。
    "--exclude=node_modules"
    "--exclude=hermes-agent-src-$ARCH/website"
    "--exclude=hermes-agent-src-$ARCH/tests"
    "--exclude=hermes-agent-src-$ARCH/tests-js"
    "--exclude=hermes-agent-src-$ARCH/.github"
)
tar czhf "$HERMES_TAR" \
    "${BUNDLE_EXCLUDES[@]}" \
    -s "|hermes-agent-src-$ARCH|hermes-agent-src|" \
    "hermes-agent-src-$ARCH"
# 9/18: 不再改归档, 只校验 (原委见 verify_hermes_bundle.py 的 docstring —— 老的
# patch 删掉 exclude-newer 反而让 uv.lock 失效, 断网装不上)。
python3 "$COMPANION/../hermes-fork/verify_hermes_bundle.py" --input "$HERMES_TAR"
echo "  OK $HERMES_TAR ($(ls -lh "$HERMES_TAR" | awk '{print $5}'))"

# ─── 打完就验 exclude 真的生效了 ────────────────────────────────
#
# 为什么要验而不是相信 --exclude: 这些 pattern 匹配的是 `-s` 改名**之前**的
# 路径 (hermes-agent-src-$ARCH/...), 而归档里存的是改名**之后**的
# (hermes-agent-src/...)。两者顺序反了的话 exclude 会全部静默失效 ——
# 表现是包大了几百 MB, 而脚本照常打印 OK。
#
# 静默多打几百 MB 的后果不是"文件大一点": 员工在 POC 现场的网络上多下一倍,
# 而且没人会想到去 tar tzf 一个装机包。
# 归档解出来 12 万条, 只列**一次**存起来再反复 grep ——
# 每个 pattern 单独 `tar tzf` 一遍就是把 500MB 的 gzip 解压 6 次。
_HLIST="$(mktemp -t hermes-bundle-list)"
trap 'rm -f "$_HLIST"' EXIT
tar tzf "$HERMES_TAR" > "$_HLIST"
_leak=0
# ⚠ 每条 pattern 必须以 / 收尾 —— 前缀匹配不是目录匹配。
# 8/3 Windows CI run #71 被这个坑拦下: "node_modules/electron" 匹配到了
# node_modules/electron-builder/, 而 electron/ 本身已被 --exclude 正确排掉。
# mac 这边一直没炸, 只是因为这棵树上恰好没有 electron-builder。
for _pat in \
    "\.git/" "venv/" "target/" "apps/desktop/" \
    "website/" "tests/" "tests-js/" ".github/"; do
    if grep -qE "^hermes-agent-src/${_pat}" "$_HLIST"; then
        echo "  ❌ exclude 没生效: 归档里仍然有 hermes-agent-src/${_pat}" >&2
        _leak=1
    fi
done
# 10/9: 任何层级的 node_modules 都不许出现 (见文件头) —— 混进来一份就是一百多 MB 白占
if grep -q '/node_modules/' "$_HLIST"; then
    echo "  ❌ 归档里出现了 node_modules (应为 0):" >&2
    grep '/node_modules/' "$_HLIST" | cut -d/ -f1-4 | sort -u | head -5 >&2
    _leak=1
fi
if [ "$_leak" = "1" ]; then
    echo "     多半是 --exclude 的 pattern 跟 -s 改名的先后顺序变了。" >&2
    echo "     pattern 要匹配改名**前**的 hermes-agent-src-$ARCH/..." >&2
    exit 1
fi
echo "  ✓ exclude 全部生效 (0 条 node_modules) · 归档 $(wc -l < "$_HLIST" | tr -d ' ') 条"

# ─── DONE ──────────────────────────────────────────────

echo ""
echo "==============================================="
echo "  DONE · $RESOURCES · runtime artifacts (10/14 起: uv / cpython-3.14.7 / hermes bundle 含 wheelhouse / chromium):"
echo "==============================================="
ls -lh "$RESOURCES/"

TOTAL_MB=$(du -sm "$RESOURCES" | cut -f1)
echo ""
echo "总大小: ${TOTAL_MB} MB"

# ─── 收尾: 清自己在 /tmp 留下的东西 (8/5 鸿波「空间又被吃完了」) ──────────
#
# 这个脚本一直只在**开头** clean 那几个目录, 跑完不管。于是每跑一次就在 /tmp
# 躺下一坨, 累积到 4.5 GB:
#
#     /tmp/hermes-agent-src-aarch64   1.1G  (整棵 hermes clone + node_modules)
#     /tmp/catfish-pw-aarch64         534M  (playwright 下的 chromium)
#     /tmp/catfish-hermes-deps-*       59M
#
# 开头 clean 保证了**正确性** (不会用上一次的残留), 但保证不了盘。
# 两件事, 之前只做了一件。
#
# PW_CACHE 特殊: 留着能省下次 ~170MB 的 chromium 下载, 是真缓存不是垃圾。
# 所以只报大小 + 给命令, 不替人删。其余是纯垃圾, 直接清。
echo ""
echo "── 收尾清理 /tmp ──"
# EMAIL_STAGE 不在这里: 9/28 邮件那段拆到 build-email-resource.sh (子进程),
# 变量跟着走了, 那边自己删自己的 stage。这里留着引用的话 set -u 直接退出 ——
# 产物其实都已经打好, 但 /tmp 那 1G+ 没清、退出码是 1、"下一步"也不打印。
for d in "$HERMES_SRC" "$PY_UNPACK" "$PW_VENV"; do
    [ -d "$d" ] || continue
    sz=$(du -sh "$d" 2>/dev/null | cut -f1)
    rm -rf "$d" && echo "  ✓ 删 $d ($sz)"
done
if [ -d "$PW_CACHE" ]; then
    echo "  · 留 $PW_CACHE ($(du -sh "$PW_CACHE" 2>/dev/null | cut -f1)) —— 这是缓存,"
    echo "    留着下次省 ~170MB chromium 下载。要腾盘就: rm -rf $PW_CACHE"
fi
echo ""
echo "下一步 (build dmg):"
if [ "$ARCH" = "aarch64" ]; then
    echo "  cd $COMPANION && npm run tauri build"
else
    echo "  cd $COMPANION && npm run tauri build -- --target x86_64-apple-darwin"
fi
