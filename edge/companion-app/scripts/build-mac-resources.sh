#!/bin/bash
# build-mac-resources.sh · BL-MAC-INSTALL-NODE+CHROMIUM-BUNDLE (7/17)
#
# 一键打全 mac dmg 用的 resources/mac/ 6 artifacts:
#   1. install.sh            (patch offline mode · 7 处 marker)
#   2. uv                    (下 astral.sh · macOS arm64 or x64)
#   3. cpython-3.11.15-embed.tar.gz  (下 python-build-standalone · macOS arm64 or x64)
#   4. hermes-agent-bundle.tar.gz    (clone + npm ci + tar 打包)
#   5. node-embed.tar.gz     (下 nodejs.org · v22 darwin arm64 or x64) · 新增
#   6. chromium-embed.tar.gz (npx playwright install chromium + tar 打包) · 新增
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
    NODE_ARCH="arm64"
    UV_ARCH="aarch64-apple-darwin"
    PY_ARCH="aarch64-apple-darwin"
else
    NODE_ARCH="x64"
    UV_ARCH="x86_64-apple-darwin"
    PY_ARCH="x86_64-apple-darwin"
fi

echo "==============================================="
echo "  build-mac-resources.sh · arch=$ARCH"
echo "  Node arch=$NODE_ARCH · uv=$UV_ARCH · py=$PY_ARCH"
echo "==============================================="

# ─── 下载 helper · retry + gzip verify + ghfast.top 代理 ─────
# 镜像 build-intel-dmg.sh 里的 download_with_retry (7/16), 修 uv 下载 truncated 挂.
# 国内下 GitHub Release 慢/易断 · ghfast.top 加速. 国外可 GH_PROXY="" 直连.
GH_PROXY="${GH_PROXY:-https://ghfast.top/}"

download_with_retry() {
    local url="$1"
    local output="$2"
    local url_proxied="${GH_PROXY}${url}"

    # 每次删旧 · 避免坏 cache 复用
    rm -f "$output"

    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        echo "  [attempt $attempt/10] $output"
        # -C - 断点续传, --retry 3 内部小 retry, --retry-max-time 15 min
        if curl -fL --retry 3 --retry-delay 5 --retry-max-time 900 --continue-at - \
                -o "$output" "$url_proxied"; then
            # verify gzip 完整性 (若 tar.gz)
            if [[ "$output" == *.gz ]] || [[ "$output" == *.tar.gz ]]; then
                if gzip -t "$output" 2>/dev/null; then
                    echo "  ✓ 下载完整 (gzip verify OK · $(ls -lh "$output" | awk '{print $5}'))"
                    return 0
                else
                    echo "  ✗ tar 坏 · 删了重下"
                    rm -f "$output"
                fi
            else
                echo "  ✓ 下载完整 ($(ls -lh "$output" | awk '{print $5}'))"
                return 0
            fi
        fi
        # 第 5 次后换直连试试
        if [ $attempt -eq 5 ] && [ -n "$GH_PROXY" ]; then
            echo "  switch to direct (no proxy)"
            url_proxied="$url"
        fi
        sleep 10
    done
    echo "!!! 下 10 次都挂: $url"
    return 1
}

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

# ─── 1. patch install.sh · offline mode ─────────────────

HERMES_SRC="/tmp/hermes-agent-src-$ARCH"

echo ""
echo "=== [1/6] Clone hermes-agent tag $HERMES_TAG · copy catfish plugins ==="
if [ -d "$HERMES_SRC" ]; then
    echo "  clean $HERMES_SRC"
    rm -rf "$HERMES_SRC"
fi
# $HERMES_TAG 若是 annotated tag, git 会打一行:
#     warning: refs/tags/<tag> <sha> is not a commit!
# 那个 <sha> 是 **tag 对象**自己的 sha, 不是它指向的 commit —— 浅克隆时 git
# 就这么提示。checkout 落点是对的 (紧接着的核对会验), 这行可以忽略。
#
# 先说一句, 是因为它每次都出现: 一条长期存在、其实无害的 warning 会让人对
# 真正的 warning 脱敏 —— 底下那些"版本对不上"的检查才是要看的。
echo "  (annotated tag 会打一行 'is not a commit!' warning · 正常, 落点由下面的核对负责)"
# 代理挂了就直连重试一次 (8/8 加)。
#
# 8/8 实录: git 全局配了 http.proxy → 127.0.0.1:7890 (Clash 之类), 那天代理
# 没开, clone 一秒就死在
#     Failed to connect to 127.0.0.1 port 7890 after 0 ms
# 而整条打包链最贵的一步在这之后 —— 下 500 MB 的 chromium/python/node。
# 卡在第 1 步反而是运气好, 但报错只有 git 那一行, 看不出"是代理不是网"。
#
# 这个文件里下 GitHub Release 早就有同款兜底 (GH_PROXY 试几次转直连),
# clone 这一步一直没有。补齐, 顺便把"这是代理的问题"说清楚。
_clone() { git clone --depth 1 --branch "$HERMES_TAG" "$@" \
    https://github.com/NousResearch/hermes-agent.git "$HERMES_SRC"; }
if ! _clone; then
    GIT_PROXY="$(git config --get http.proxy || true)"
    if [ -n "$GIT_PROXY" ]; then
        echo ""
        echo "  ⚠ clone 失败, 而 git 配了代理: $GIT_PROXY"
        echo "    代理没开的话就是它。直连重试一次..."
        rm -rf "$HERMES_SRC"
        _clone -c http.proxy= -c https.proxy= || {
            echo ""
            echo "❌ 带代理和直连都 clone 不下来。"
            echo "   · 代理软件开着吗 (git 配的是 $GIT_PROXY)"
            echo "   · 或临时去掉: git config --global --unset http.proxy"
            exit 1
        }
        echo "  ✓ 直连成功 (这次绕过了 $GIT_PROXY)"
    else
        echo ""
        echo "❌ clone 失败, 且 git 没配代理 —— 是网络本身的问题。"
        exit 1
    fi
fi

# ── 校验真的落在了要的那个版本上 (P3.5.86 · 7/29) ────────────────────
#
# 7/29 实录: pin 写的是 v2026.7.1, git clone 打了一行
#     warning: refs/tags/v2026.7.1 462c8b02... is not a commit!
#     Note: switching to '7c1a0295...'
# 然后**自己切到了别的 commit**, 脚本毫不知情继续打包 —— 包里究竟是哪个
# 版本没有任何记录。
#
# 这件事的后果不在打包时, 在运行时: catfish 的 19 个 monkey-patch 是 patch
# hermes 内部函数的 (gateway.run._resolve_gateway_model 这类), 版本对不上
# 就加载失败, 而失败方式是 logger.warning + silent skip —— 多租户 header、
# picker 联动、RBAC、审批全部悄悄不工作, 界面上一切正常。
#
# 所以这里 fail-loud: 落点跟 pin 对不上就停, 不许打出一个"不知道装的是什么"
# 的包。同时把实际 commit 记进 bundle, 出问题时能回溯。
ACTUAL_DESC="$(cd "$HERMES_SRC" && git describe --tags --always 2>/dev/null || echo '<未知>')"
ACTUAL_SHA="$(cd "$HERMES_SRC" && git rev-parse HEAD 2>/dev/null || echo '<未知>')"
if [ "$ACTUAL_DESC" != "$HERMES_TAG" ]; then
    echo ""
    echo "❌ clone 落点跟 pin 对不上:"
    echo "     .hermes-git-tag 要的 : $HERMES_TAG"
    echo "     实际 checkout 的     : $ACTUAL_DESC  ($ACTUAL_SHA)"
    echo ""
    echo "   多半是这个 tag 指向的不是 commit (annotated tag 指到了 tree/blob),"
    echo "   或者 tag 名写错了。上游可用的 tag:"
    (cd "$HERMES_SRC" && git ls-remote --tags origin 2>/dev/null \
        | awk -F/ '{print "     " $NF}' | grep -v '\^{}' | tail -10) || true
    echo ""
    echo "   catfish 的 19 个 monkey-patch 是按特定 hermes 版本写的,"
    echo "   装错版本会静默失效 —— 所以这里不允许继续。"
    exit 1
fi
if [ "$ACTUAL_SHA" != "$HERMES_COMMIT" ]; then
    echo ""
    echo "❌ Hermes commit 跟 pin 不一致:"
    echo "     .hermes-git-commit: $HERMES_COMMIT"
    echo "     实际 checkout:      $ACTUAL_SHA"
    echo "   tag 名相同也不能继续；annotated tag 或远端移动都必须重新审计。"
    exit 1
fi
echo "  ✓ hermes 版本核对: $ACTUAL_DESC ($ACTUAL_SHA)"
# 把版本写进 bundle, 装机后可查 (~/.hermes/hermes-agent/.catfish-hermes-version)
printf '%s\n%s\n' "$ACTUAL_DESC" "$ACTUAL_SHA" > "$HERMES_SRC/.catfish-hermes-version"

# copy catfish plugins
mkdir -p "$HERMES_SRC/plugins/memory"
for plugin in catfish-memory catfish-todo-sync; do
    src="../hermes-plugins/$plugin"
    if [ -d "$src" ]; then
        cp -R "$src" "$HERMES_SRC/plugins/memory/$plugin"
        echo "  copied $plugin"
    fi
done

# ─── catfish-email 源码包 ──────────────────────────────────────────
#
# 7/30 达华现场: 员工装完 Companion, 邮件 tab 挂, 界面提示
#     "CLI 没装 (bash edge/email-agent/install.sh)"
# —— 而员工手里只有一个 dmg, **根本没有 edge/email-agent/ 这个目录**。
#
# 查下来这东西从来没进过交付链路:
#   - hermes-agent-bundle.tar.gz 里搜 catfish-email → 0 条
#   - 本脚本里搜 email / pip install → 0 处
#   - 而 Companion 的 link_catfish_email_bin() 只负责建软链, 前提是
#     venv/bin/catfish-email 已存在 —— 于是它永远走 warn 分支静默跳过
#
# 为什么一直没人发现: edge/email-agent/install.sh 用的是
# `pip install -e "$SCRIPT_DIR"` (editable), 要求源码目录长期在 ——
# 那是开发机的装法, 天生进不了一个要分发的包。它只在自己机器上跑过。
#
# 为什么打成 tar 而不是塞进 hermes bundle:
#   bundle 是 hermes 上游的源码快照 (装机时才现建 venv), 往里塞我们自己的
#   东西会污染"这份 bundle == 那个 pin 的 commit"这个契约, 也会让
#   .catfish-hermes-version 的核对失去意义。单独一个 tar, 来路清楚。
#
# ⚠ 为什么装的是 **wheel** 而不是源码目录:
#   hermes 的 venv 是 `uv venv` 建的, **不带 pip / setuptools**。装一个源码
#   目录要先跑构建后端, uv 会去联网拉 setuptools —— 恰好在内网机器上失败,
#   而内网正是这功能要服务的场景。构建期就做成 wheel, 装机时纯解包拷贝,
#   零构建零联网。
#
# 这个包零运行时依赖 (pyproject.toml `dependencies = []`, 只用标准库),
# 所以一个 py3-none-any 的 wheel 走遍两个架构。
echo ""
echo "→ 构建 catfish-email wheel"
EMAIL_SRC="$COMPANION/../email-agent"
EMAIL_TAR="$RESOURCES/catfish-email-dist.tar.gz"
if [ ! -f "$EMAIL_SRC/pyproject.toml" ]; then
    echo "❌ 找不到 email-agent 源码: $EMAIL_SRC"
    echo "   邮件功能会整个缺失 —— 不允许打出这样的包。"
    exit 1
fi
EMAIL_STAGE="/tmp/catfish-email-dist-$ARCH"
rm -rf "$EMAIL_STAGE" && mkdir -p "$EMAIL_STAGE"
# --no-deps: 它本来就零依赖, 显式写死免得哪天有人加了依赖却没人注意到
python3 -m pip wheel --no-deps --wheel-dir "$EMAIL_STAGE" "$EMAIL_SRC" >/dev/null || {
    echo "❌ 构建 catfish-email wheel 失败"
    echo "   本机需要 python3 + pip (只在构建期用, 员工机不需要)"
    exit 1
}
WHEEL_COUNT=$(find "$EMAIL_STAGE" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')
if [ "$WHEEL_COUNT" != "1" ]; then
    echo "❌ 期望正好 1 个 wheel, 实际 $WHEEL_COUNT 个 —— 装机时无法确定装哪个"
    exit 1
fi
# skill 一并带上: 没有它 CLI 装了但模型不知道有这个工具
cp -R "$EMAIL_SRC/hermes-skill" "$EMAIL_STAGE/hermes-skill"
tar czf "$EMAIL_TAR" -C "$EMAIL_STAGE" .
# 校验产出 —— 装机时才发现缺东西就晚了
if ! tar tzf "$EMAIL_TAR" | grep -q '\.whl$'; then
    echo "❌ $EMAIL_TAR 里没有 wheel"
    exit 1
fi
if ! tar tzf "$EMAIL_TAR" | grep -q 'hermes-skill/catfish-email/SKILL.md$'; then
    echo "❌ $EMAIL_TAR 里没有 hermes-skill/catfish-email/SKILL.md"
    exit 1
fi
rm -rf "$EMAIL_STAGE"
echo "  ✓ $(basename "$EMAIL_TAR") ($(du -h "$EMAIL_TAR" | cut -f1))"

echo ""
echo "=== [2/6] Patch install.sh offline mode ==="
python3 ../hermes-fork/patch_install_sh_offline.py \
    --input "$HERMES_SRC/scripts/install.sh" \
    --output "$RESOURCES/install.sh"
chmod +x "$RESOURCES/install.sh"

# ─── 3. Node.js darwin binary ────────────────────────────

# 8/8: 22.14.0 → 22.23.2, 因为 hermes v0.20 把底线抬了。
#
# v0.19 的 package.json engines 是 `node >=20.0.0`, 22.14.0 富余得很。
# v0.20 抬到 **`node >=22.22.0`**, 而上游 .npmrc 里写着 `engine-strict=true`
# —— 不满足是 **EBADENGINE 硬失败**, 不是 warn。22.14.0 会让 npm ci 当场挂,
# 而这一步在打包流程靠后, 要下完几百 MB 才炸。
#
# 选 22.23.2 (2026-07-29 发布) 而不是跳到 24/26: 留在 v22 LTS 线内是最小改动,
# 它 bundle 的 npm 是 10.x, 满足 engines 的 `<11.10.0` 那一支。
NODE_VERSION="22.23.2"
NODE_FNAME="node-v${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_FNAME}"

# 上面那个版本号是手写的, 而 hermes 每次升级都可能再抬 engines。与其指望下次
# 有人记得改, 不如**当场跟打包用的这份 hermes 源码对一遍** —— 源码就在
# $HERMES_SRC, 零成本, 一秒。
#
# 不实现完整 semver range 解析 (那要拉依赖), 只解 `>=X.Y.Z` 这一种最常见的形式;
# 解不出来就跳过并说一声, 不拦构建。
if [ -f "$HERMES_SRC/package.json" ]; then
    NODE_FLOOR="$(python3 -c '
import json, re, sys
try:
    eng = json.load(open(sys.argv[1])).get("engines", {}).get("node", "")
except Exception:
    sys.exit(0)
m = re.search(r">=\s*(\d+)\.(\d+)\.(\d+)", eng or "")
if m: print(".".join(m.groups()))
' "$HERMES_SRC/package.json")"
    if [ -n "$NODE_FLOOR" ]; then
        # sort -V: 版本号排序。最小的那个若不是 floor, 说明 NODE_VERSION < floor
        if [ "$(printf '%s\n%s\n' "$NODE_FLOOR" "$NODE_VERSION" | sort -V | head -1)" != "$NODE_FLOOR" ]; then
            echo "❌ 内嵌的 Node $NODE_VERSION 低于 hermes 要求的 >=$NODE_FLOOR"
            echo "   ($HERMES_SRC/package.json 的 engines.node)"
            echo "   上游 .npmrc 有 engine-strict=true —— 这是硬失败, npm ci 会报 EBADENGINE。"
            echo "   修: 把本脚本的 NODE_VERSION 抬到 >=$NODE_FLOOR 的一个真实发布版本。"
            exit 1
        fi
        echo "  ✓ Node $NODE_VERSION 满足 hermes 要求的 >=$NODE_FLOOR"
    else
        echo "  ⚠ 解不出 engines.node (非 '>=X.Y.Z' 形式?), 跳过 Node 版本下限校验"
    fi
fi

echo ""
echo "=== [3/6] Download Node.js $NODE_VERSION darwin-$NODE_ARCH ==="
# Node.js 官方源 nodejs.org 不走 GH proxy (nodejs.org 直接 CDN 快)
NODE_TMP="/tmp/$NODE_FNAME"
if [ ! -f "$NODE_TMP" ] || ! gzip -t "$NODE_TMP" 2>/dev/null; then
    rm -f "$NODE_TMP"
    curl -fL --retry 3 --retry-delay 5 --retry-max-time 900 -o "$NODE_TMP" "$NODE_URL"
    gzip -t "$NODE_TMP" || { echo "❌ Node tar corrupted, re-run"; exit 1; }
fi
cp "$NODE_TMP" "$RESOURCES/node-embed.tar.gz"
echo "  OK $RESOURCES/node-embed.tar.gz ($(ls -lh "$RESOURCES/node-embed.tar.gz" | awk '{print $5}'))"

# ─── 4. uv binary ───────────────────────────────────────

UV_VERSION="0.4.30"
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

# ─── 5. cpython 3.11.15 ─────────────────────────────────

PYTHON_VERSION="3.11.15"
PYTHON_BUILD_TAG="20260623"
PY_FNAME="cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-${PY_ARCH}-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/${PY_FNAME}"

echo ""
echo "=== [5/6] Download cpython $PYTHON_VERSION ($PY_ARCH) ==="
PY_TMP="/tmp/cpython-$ARCH.tar.gz"
# 走 ghfast.top 代理
download_with_retry "$PY_URL" "$PY_TMP" || exit 1
cp "$PY_TMP" "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz"
echo "  OK cpython-embed.tar.gz ($(ls -lh "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz" | awk '{print $5}'))"

# ─── 5.5 hermes venv 的额外 Python 依赖 (jieba / playwright) ──────────
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
# 所以解压刚下好的 cpython, 用它自己的 pip download。版本和平台标签
# 自然对, 不用猜 --platform 标签。
echo ""
echo "=== [5.5/6] 取 hermes venv 额外依赖 (jieba / playwright) ==="
DEPS_TAR="$RESOURCES/hermes-deps-dist.tar.gz"
DEPS_STAGE="/tmp/catfish-hermes-deps-$ARCH"
PY_UNPACK="/tmp/catfish-py-unpack-$ARCH"
rm -rf "$DEPS_STAGE" "$PY_UNPACK" && mkdir -p "$DEPS_STAGE" "$PY_UNPACK"
tar xzf "$PY_TMP" -C "$PY_UNPACK" || { echo "❌ 解压 cpython 失败"; exit 1; }
EMBED_PY="$PY_UNPACK/python/bin/python3"
[ -x "$EMBED_PY" ] || { echo "❌ 找不到嵌入解释器: $EMBED_PY"; exit 1; }
echo "  用嵌入解释器取 wheel: $("$EMBED_PY" -V)"

# jieba 和 playwright 必须分开取 —— 8/5 实测:
#
#     ERROR: Could not find a version that satisfies the requirement jieba
#            (from versions: none)
#
# 原因不是没网 (同一次运行里 node / uv / cpython 三个 curl 全部成功)。
# **jieba 在 PyPI 上只发 sdist, 一个 wheel 都没有** (0.42.1 只有
# jieba-0.42.1.tar.gz)。加了 --only-binary=:all: 就等于告诉 pip "只要 wheel",
# 候选自然是空集。原来那句 "构建机需要外网" 的报错把人往完全错的方向带。
#
#   · playwright + greenlet: 编译产物, 必须是**跟内嵌 3.11.15 对齐**的 wheel
#     → --only-binary=:all:, 用嵌入解释器取
#   · jieba: 纯 Python, 我们自己 pip wheel 现打一个 py3-none-any 的轮子。
#     不能把 sdist 丢给员工机 —— 装的时候 uv --no-index 要构建 sdist, 得有
#     setuptools 后端, 离线环境下拿不到, 又是一个"到现场才炸"。
"$EMBED_PY" -m pip download --only-binary=:all: -d "$DEPS_STAGE" playwright >/dev/null || {
    echo "❌ 取 playwright wheel 失败 (需要外网; 若已联网请看上面 pip 的原始报错)"
    exit 1
}
"$EMBED_PY" -m pip wheel --no-deps -w "$DEPS_STAGE" jieba >/dev/null || {
    echo "❌ 打 jieba wheel 失败 (jieba 只有 sdist, 这一步是现打轮子, 需要外网)"
    exit 1
}
# 正向断言: 这三个必须真的在, 而且必须是 .whl —— 光看 pip 退出码不够。
# 限定 *.whl (不是 *-*) 是因为员工机上 uv 带 --no-index 装, sdist 装不了;
# 混进一个 sdist 会一路绿到达华的机器上才炸。
for pkg in jieba playwright greenlet; do
    if ! find "$DEPS_STAGE" -maxdepth 1 -iname "${pkg}-*.whl" | grep -q .; then
        echo "❌ $DEPS_STAGE 里没有 $pkg 的 **wheel** —— 装机时会静默缺功能"
        find "$DEPS_STAGE" -maxdepth 1 -type f -exec basename {} \; | sed 's/^/     现有: /'
        exit 1
    fi
done
tar czf "$DEPS_TAR" -C "$DEPS_STAGE" .
echo "  OK hermes-deps-dist.tar.gz ($(ls -lh "$DEPS_TAR" | awk '{print $5}')) · $(find "$DEPS_STAGE" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ') 个 wheel"
rm -rf "$PY_UNPACK"

# ─── 6. hermes-agent bundle · npm ci · npx playwright install chromium · tar 打包 ─────

echo ""
echo "=== [6a/6] npm ci in hermes-agent (装 node_modules) ==="
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1  # 分开跑 chromium
# 顶级 npm ci
cd "$HERMES_SRC"
if [ -f "package-lock.json" ]; then
    npm ci --no-audit --no-fund --loglevel=error
else
    npm install --no-audit --no-fund --loglevel=error
fi
echo "  OK 顶级 node_modules 装完 ($(du -sh node_modules | cut -f1))"

# BL-HERMES-BUNDLE-SURGICAL (7/17): 手术级瘦身. hermes-agent 顶级 npm ci 装 1GB · 里面
# TOP: node_modules/hermes 811MB (React Native Hermes JS engine · Electron 用) + mermaid
# 80MB + @tabler 77MB + lucide-react 36MB + typescript 23MB + electron-winstaller 30MB.
# 员工机跑 hermes-agent (Python 主) 只需 agent-browser + node-pty + playwright. 删大而不用的.
echo ""
echo "=== [6a.5/6] 手术级瘦身 · 删 npm 大依赖 (Electron/前端/dev 用) ==="
cd "$HERMES_SRC/node_modules" 2>/dev/null || true
SURGICAL_TARGETS=(
    hermes                    # 811 MB · React Native Hermes JS engine
    mermaid @mermaid-js       # 80 MB · 画图库
    @tabler @icons-pack       # 77+27 MB · icon set
    lucide-react              # 36 MB · icon set
    three three-stdlib        # 30 MB · 3D
    electron-winstaller       # 30 MB · electron 装机器
    typescript                # 23 MB · dev tool
    @rolldown                 # 17 MB · bundler
    @tauri-apps               # 15 MB · Tauri (Companion 已含, 重复)
    react-native-*            # React Native (Electron/mobile 用)
    "@types"                  # dev TypeScript types
)
for t in "${SURGICAL_TARGETS[@]}"; do
    # 支持通配符 (react-native-* 之类)
    for match in $t; do
        if [ -e "$match" ]; then
            size=$(du -sh "$match" 2>/dev/null | cut -f1)
            rm -rf "$match"
            echo "  🔪 删 $match ($size)"
        fi
    done
done
cd "$HERMES_SRC"
echo "  OK 手术后 node_modules 大小: $(du -sh node_modules | cut -f1)"

echo ""
echo "=== [6b/6] npx playwright install chromium (arch=$NODE_ARCH) ==="
unset PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD  # 允许下载 chromium
#
# ⚠ 7/30: 必须用**每架构独立的下载目录**, 不能用默认的
#   ~/Library/Caches/ms-playwright/。
#
# 原因: Playwright 的目录名是 chromium-<revision>, **不带架构**。两个架构的
# 构建共用那一个缓存, 于是:
#     打 aarch64 → 下载 arm64 到 chromium-1234
#     打 x64     → playwright 看到 chromium-1234 已在 → 直接跳过下载
#                  → 6c 把 arm64 的 chromium 打进了 x64 的包
#
# 这个错法跟 7/16 那次 uv 装错架构是同一类, 但更隐蔽: 连"忘了还原"这种人为
# 动作都不需要, **只要两个架构先后各打一次就必然发生**。而且方向不固定 ——
# 谁后打谁中招, 先打的那个反而是对的。
#
# 7/30 打 x64 时被 verify-app-arch.sh 拦下才发现。它拦住了, 但那是在
# cargo build 跑完之后, 白等一分多钟; 而且**同一次污染 aarch64 包时它拦不住**
# (缓存恰好就是 arm64)。所以根因要在这里修, 不能只靠下游校验。
#
# 代价: 每个架构各下一次 chromium (~150MB), 不再复用缓存。换来的是
# "打哪个架构就下哪个架构", 与本机装没装过 playwright 无关。
PW_CACHE="/tmp/catfish-pw-$ARCH"
echo "  下载目录: $PW_CACHE (每架构独立, 不碰 ~/Library/Caches/ms-playwright)"
mkdir -p "$PW_CACHE"
#
# ⚠ 为什么不用 `arch -x86_64 npx` (7/30 查实, 别改回去)
#
# 在 Apple Silicon 上打 x64 包时, 老写法是 `arch -x86_64 npx playwright install`,
# 指望让 node 以 Intel 身份跑、于是 playwright 下 x64 的 chromium。**这条路
# 根本不通。** 看 playwright 判定平台的源码 (playwright-core, 1.49 到 1.60
# 逐字未变, packages/utils/hostPlatform.ts):
#
#     function calculatePlatform() {
#       if (process.env.PLAYWRIGHT_HOST_PLATFORM_OVERRIDE) { ...直接返回... }
#       ...
#       if (os.cpus().some((cpu) => cpu.model.includes("Apple")))
#         macVersion += "-arm64";
#
# 它在 macOS 上是靠 **CPU 型号字符串** 判架构的, 不是 process.arch。
# 即使 node 被 Rosetta 翻译成 x86_64 在跑, os.cpus() 依然报 "Apple M2" ——
# 因为机器本身就是 Apple Silicon。所以 `-arm64` 后缀**必然**被加上,
# 在 M 系列机器上永远下不到 x64 chromium。
#
# 这就是 7/30 那次的现象: 换了全新的空目录, 下下来的还是 arm64。
#
# 正解是 playwright 官方的覆盖开关, 也就是上面那个函数的第一条语句。
# 取值必须是它下载表里存在的键 —— 表里 mac 只有 mac10.13/10.14/10.15、
# mac11..mac15、mac26, **中间 mac16~mac25 不存在**, 写错会直接 404。
# mac15 / mac15-arm64 在新老版本里都在, 且 mac15 与 mac26 指向的是同一个
# chrome-mac-x64.zip, 所以选 mac15 最稳。
#
# 注意: calculatePlatform() 只在模块初始化时跑一次, 所以必须在进程启动前
# 就把环境变量给上 (下面这种前缀写法可以)。
#
# 两个架构都显式指定, 不留"靠自动探测"的那一半 —— 探测对不对不该是运气。
case "$ARCH" in
    aarch64) PW_PLATFORM="mac15-arm64" ;;
    x64)     PW_PLATFORM="mac15" ;;
esac
echo "  PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=$PW_PLATFORM"
echo "  (playwright 会打一句 'your OS is not officially supported ... downloading"
echo "   fallback build for $PW_PLATFORM' · 用 override 时必然出现, 正常)"
# ⚠ playwright 版本必须钉死, 不能用 latest (7/30)
#
# 原本是 `npx --yes playwright install chromium` —— 不带版本号, npx 每次拉
# latest。而**装进包的 chromium 版本是由 playwright 版本决定的**:
#     playwright 1.60.0 → Chrome for Testing 151.0.7922.34 (chromium v1234)
# 换个 playwright 版本就换个 chromium, 而脚本不会有任何提示。
#
# 后果有三层:
#   1. 同一份代码不同时间打出的包内容不同 —— 出问题时没法复现"上个月那个包"
#   2. 员工拿到的浏览器版本静默变化, 没人测过也没人知道换了
#   3. 本脚本有两处依赖 playwright 的内部约定, 会跟着漂:
#        · 下面 [6c] 的目录名校验依赖 chrome-mac-x64 / chrome-mac-arm64 布局
#        · 上面的 mac15 依赖它下载表里有这个键
#      查过 1.49.1 与 1.60.0 这两处一致, 但 1.60 已经把 lib 打成 bundle 了
#      (老的 lib/utils/hostPlatform.js 路径没了), 说明它内部结构会动。
#
# 钉在这个版本 = 今天行为不变 (latest 现在就是 1.60.0), 变的是三个月后
# 再打还是同一个包。要升 chromium 就改这个数字 —— 让它成为一个**有意识的
# 动作**, 而不是每次打包随机发生。
PW_VERSION="1.60.0"
echo "  playwright@$PW_VERSION (钉死 · 决定 chromium 版本)"
PLAYWRIGHT_BROWSERS_PATH="$PW_CACHE" \
PLAYWRIGHT_HOST_PLATFORM_OVERRIDE="$PW_PLATFORM" \
    npx --yes "playwright@$PW_VERSION" install chromium

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
echo "=== [6d/6] tar pack hermes-agent bundle (含 node_modules · 排废件) ==="
# BL-HERMES-BUNDLE-SLIM (7/17): hermes-agent 里 apps/desktop 是 Electron 桌面 app
# (200MB Electron framework + electron/playwright 二次装 1.4GB, 总 1.6GB), 我们
# 用 Tauri Companion 替代它, 完全不用. exclude 后 tar 从 955MB → ~200MB.
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
# 这不是"排除失效"—— 那 5 条 exclude 全都生效, 是漏排了一项。7/17 时
# upstream 的 apps/desktop 还小, 注释里写的 "→ ~200MB" 就是那时候量的;
# 0.19 之后它长到 534MB 也没人回头看这条注释。
tar czhf "$HERMES_TAR" \
    --exclude="hermes-agent-src-$ARCH/.git" \
    --exclude="hermes-agent-src-$ARCH/venv" \
    --exclude="hermes-agent-src-$ARCH/venv.bak" \
    --exclude="hermes-agent-src-$ARCH/.venv" \
    --exclude="hermes-agent-src-$ARCH/target" \
    --exclude="hermes-agent-src-$ARCH/apps/desktop" \
    --exclude="hermes-agent-src-$ARCH/node_modules/electron" \
    -s "|hermes-agent-src-$ARCH|hermes-agent-src|" \
    "hermes-agent-src-$ARCH"
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
for _pat in "\.git/" "venv/" "target/" "apps/desktop/" "node_modules/electron/"; do
    if grep -qE "^hermes-agent-src/${_pat}" "$_HLIST"; then
        echo "  ❌ exclude 没生效: 归档里仍然有 hermes-agent-src/${_pat}" >&2
        _leak=1
    fi
done
if [ "$_leak" = "1" ]; then
    echo "     多半是 --exclude 的 pattern 跟 -s 改名的先后顺序变了。" >&2
    echo "     pattern 要匹配改名**前**的 hermes-agent-src-$ARCH/..." >&2
    exit 1
fi
echo "  ✓ exclude 全部生效 (含 node_modules/electron) · 归档 $(wc -l < "$_HLIST" | tr -d ' ') 条"

# ─── DONE ──────────────────────────────────────────────

echo ""
echo "==============================================="
echo "  DONE · $RESOURCES · 6 artifacts:"
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
for d in "$HERMES_SRC" "$DEPS_STAGE" "$PY_UNPACK" "$EMAIL_STAGE"; do
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
