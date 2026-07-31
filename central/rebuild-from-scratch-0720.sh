#!/bin/bash
# BL-P29-REBUILD-CLEAN (7/20 Task #29): 中央 image tar 完全从头打 · 无老 tar 依赖.
#
# 与 rebuild-full-image-tars-0720.sh 差异 (军规迭代 · 鸿波 catch "不要用基础包"):
#   - 不 load 任何老 tar · 6 个自造 image 全部从 Dockerfile rebuild
#   - upstream postgres + nginx · docker pull 官方最新 tag
#   - image tag 从 docker compose config --images 动态拿 · 免硬编码错
#     (上版硬编码 gateway 0.1.0 · 实际 0.1.1 · 会漏)
#
# 8 image 全 rebuild:
#   catfish-identity     · Task #29 改密 + Task #17 CORS
#   catfish-gateway      · Task #16/#14/#7 catfish-auto + errors + orjson
#   catfish-skills-hub   · 全新 rebuild (Dockerfile 7/14)
#   catfish-web          · 全新 rebuild
#   catfish-mcp-registry · 全新 rebuild
#   catfish-wiki-hub     · 全新 rebuild
#   postgres:16-alpine   · docker pull 官方
#   nginx:1.27-alpine    · docker pull 官方
#
# 时长: amd64 走 QEMU 慢 · 单架构 30-60 min · 双架构 ~90-120 min
#
# 用法 (8/1 起 · 一次一个平台, 见下面 ARCH 那段为什么):
#   ARCH=arm64 nohup bash rebuild-from-scratch-0720.sh > /tmp/build-arm64.log 2>&1 &
#   disown
#   # 上一个跑完之后, DATE 照抄上一次的:
#   DATE=20260731 ARCH=amd64 nohup bash rebuild-from-scratch-0720.sh > /tmp/build-amd64.log 2>&1 &
#
# 睡醒验:
#   grep -E "===|✅|❌|⚠" /tmp/build-arm64.log
#   ls -lh ~/Downloads/catfish-达华POC-0715/dahua-poc-*.tar.gz

set -euo pipefail

# ── P3.5.79+ (7/23 参数化 · date + skip arch + full delivery tar) ──
# DATE          默认今天 YYYYMMDD · 可 override (DATE=20260723 bash ...)
# SKIP_ARM64    默认 0 · 达华 Linux x86_64 服务器 SKIP_ARM64=1 省 40 min
# SKIP_AMD64    默认 0 · 只需 mac 本地 arm64 测试 SKIP_AMD64=1
# BUILD_FULL_DELIVERY  默认 1 · Phase 3 打完整交付 tar (image+setup+docker-compose+companion+docs)
#                 客户 IT 拿到一个 tar 解压 · `bash setup.sh` 秒装
#
# ONLY_SERVICES (7/23 P2 abort 传导做完后加): 空 = 打全 6 image (默认).
#                指定则**只 build 这几个**服务 · save 时 image list 也只含这几个.
#                场景 · gateway 单改一处热修 · 别陪打 identity/web/skills-hub 等 5 个.
#                例: ONLY_SERVICES="gateway" bash rebuild-from-scratch-0720.sh
#                    ONLY_SERVICES="gateway identity" ...
#                空格分隔 · 名字对应 docker-compose.yml 里 service key (不带 catfish- 前缀).
#                验证 · 名字必须在 BUILT_SERVICES 列表里 · 否则 fail-loud.
#                注 · postgres / nginx 是 upstream image · 只在全打时 pull · ONLY_SERVICES
#                模式不 pull (假设已在本地 · 增量更新场景默认满足).
#
# REPACK_ONLY (7/23 · 补 Phase 3 语义分裂): 默认 0.
#                = 0 (默认): SKIP_ARM64=1 严格跳该 arch (Phase 1/2 build + Phase 3 FULL 全跳).
#                = 1: 只重打 Phase 3 FULL · 用**已有** image tar (config-only 更新场景).
#                        忽略 SKIP · 只要 $ARM_OUT / $AMD_OUT 存在就打 FULL.
#                动机 · 之前 Phase 3 逻辑 "tar 存在就打" 跟 SKIP 撞 · SKIP_AMD64=1
#                但打出 FULL-amd64 (用老 image tar · 没含新 gateway) · 给客户是"假新版".
#                fix · 严格默认 · 显式 REPACK_ONLY=1 才复用老 image tar.
#                8/1 · =1 现在**自动置 SKIP_ARM64/SKIP_AMD64=1** (不再 build) ·
#                      名字终于名副其实 · 详见下面赋值处注释.
DATE="${DATE:-$(date +%Y%m%d)}"
SKIP_ARM64="${SKIP_ARM64:-0}"
SKIP_AMD64="${SKIP_AMD64:-0}"
BUILD_FULL_DELIVERY="${BUILD_FULL_DELIVERY:-1}"
ONLY_SERVICES="${ONLY_SERVICES:-}"
REPACK_ONLY="${REPACK_ONLY:-0}"

# ── REPACK_ONLY 名副其实 (8/1) ────────────────────────────────────
# 原来它只管 Phase 3, **不影响 Phase 1/2**。于是光写 `REPACK_ONLY=1 bash ...`
# 会老老实实从头 build 两个架构 —— 90 分钟, 跟"only repack"这个名字正好相反。
# 想真的只重打包, 得写全 `SKIP_ARM64=1 SKIP_AMD64=1 REPACK_ONLY=1` 三个。
#
# 而它的定义原文就是"用**已有** image tar" —— 那就跟 build 互斥。让名字兑现:
# REPACK_ONLY=1 ⇒ 两个 Phase 都不 build, Phase 3 拿现成 tar 重打 FULL。
# (显式再写 SKIP_* 的老命令行为不变 —— 本来就是 1。)
if [ "$REPACK_ONLY" = "1" ]; then
    SKIP_ARM64=1
    SKIP_AMD64=1
fi

# ── ARCH (8/1 · 鸿波 "一个一个平台来生成") ──────────────────────────
# arm64 | amd64 | both. 默认必须显式选一个, 不再默默打两个。
#
# ## 为什么改
#
# 7/31 那次: Phase 1 arm64 从头打完, image tar 也存下来了 (422M, ✅)。紧接着
# Phase 2 第一步 `docker pull --platform linux/amd64 postgres:16-alpine` 撞上
# Docker Hub 的 auth EOF, 本地又只有刚拉的 arm64 版 —— 脚本按设计 exit 1。
#
# 判断本身是对的 (混架构的 tar 更糟)。问题在**它站的位置**: Phase 3 (打 FULL
# 交付包) 排在两个 Phase 后面, 于是 amd64 的一次网络抖动, 把 arm64 那 40 分钟
# 已经完成的构建的**打包**一起带走了 —— 当晚只剩一个 image-only tar,
# FULL 包一个都没出。
#
# 一次一个平台就没有这条传导路径: 一次运行 = 一个平台的 build + 它自己的 FULL,
# 另一个平台挂不挂跟它无关。
#
# ## 代价 (下面末尾的提醒就是为它准备的)
#
# 分两次跑, DATE 会各算各的。第一个平台 23:00 打完是 20260731, 第二个平台
# 跨过零点才跑完就成了 20260801 —— 两个包日期对不上, 而且 Phase 3 拿
# $ARM_OUT 找当天的 image tar 找不到, 只印一行 "⚠ 不存在 · skip" 就接着
# "=== DONE ===", 看输出像成功了。所以第二次务必显式传第一次的 DATE。
ARCH="${ARCH:-}"
case "$ARCH" in
    arm64) SKIP_AMD64=1 ;;
    amd64) SKIP_ARM64=1 ;;
    both)  ;;   # 显式要求一次打两个
    "")
        # 不选平台又确实要 build (REPACK_ONLY / 自己写了 SKIP_* 的除外) —— 拦下。
        # 这种情况多半是"忘了写 ARCH", 而不是"真想打两个"。
        if [ "$REPACK_ONLY" != "1" ] && [ "$SKIP_ARM64" = "0" ] && [ "$SKIP_AMD64" = "0" ]; then
            echo "❌ 没指定 ARCH · 一次只打一个平台:"
            echo "     ARCH=arm64 bash $(basename "$0")"
            echo "     ARCH=amd64 DATE=<第一个平台那次的日期> bash $(basename "$0")"
            echo ""
            echo "   真要一次打两个 (不推荐 · 后一个平台挂会连累前一个的打包):"
            echo "     ARCH=both bash $(basename "$0")"
            exit 1
        fi
        ;;
    *) echo "❌ ARCH=$ARCH 不认识 · 只能是 arm64 / amd64 / both"; exit 1 ;;
esac

CENTRAL="$HOME/person_task/catfish/central"
REPO_ROOT="$HOME/person_task/catfish"
DELIVERY_DIR="$REPO_ROOT/delivery/dahua-poc"
DELIVERY="$HOME/Downloads/catfish-达华POC-0715"

# 目标 tar (dynamic date · gzip 压缩)
ARM_OUT="$DELIVERY/dahua-poc-central-arm64-${DATE}.tar.gz"
AMD_OUT="$DELIVERY/dahua-poc-central-amd64-${DATE}.tar.gz"

# 完整交付 tar (Phase 3 · image + setup + config + companion + docs 一坨)
FULL_ARM_OUT="$DELIVERY/dahua-poc-FULL-arm64-${DATE}.tar.gz"
FULL_AMD_OUT="$DELIVERY/dahua-poc-FULL-amd64-${DATE}.tar.gz"

cd "$CENTRAL"

# ── 版本戳 (8/1) ──────────────────────────────────────────────────
# 现在**开始**时抓一次, 不是打包时 —— 这才对得上 docker 读构建上下文的时间点。
# 工作区脏时必须说出来: SHA 只描述已提交的部分, 有未提交改动时它描述不了
# 这个包, 那这个戳就是在撒谎。
_git() { git -C "$REPO_ROOT" "$@" 2>/dev/null; }
GIT_SHA=$(_git rev-parse --short HEAD || echo unknown)
GIT_DIRTY=$(_git status --porcelain || true)

# ── 前置检查: Docker daemon 必须活着 (P3.5.80 · 7/28) ──────────────
#
# 7/28 撞过: Docker Desktop 没启动就跑本脚本. `docker compose config` 是纯
# 客户端操作, 照样把 image 列表打印出来了 —— 看着一切正常; 直到几十秒后
# 第一次 docker pull 才报 socket 连不上. 在 QEMU 跨架构构建里, 这种"跑了一段
# 才失败"特别浪费时间.
#
# 提前 3 秒探一次, 立刻失败.
if ! docker info > /dev/null 2>&1; then
    echo "❌ 连不上 Docker daemon."
    echo "   mac : 启动 Docker Desktop, 等鲸鱼图标不再转"
    echo "   linux: sudo systemctl start docker"
    echo "   确认: docker info | head -5"
    exit 1
fi

# buildx 是跨架构构建的前提, 缺了会在 Phase 1 中途才炸.
if ! docker buildx version > /dev/null 2>&1; then
    echo "❌ 没有 docker buildx · 跨架构构建做不了."
    echo "   Docker Desktop 自带; 独立 docker 需装 docker-buildx-plugin."
    exit 1
fi

mkdir -p "$DELIVERY"

# 动态从 docker-compose.yml 拿 image list · 免硬编码错. Sort -u 去重.
IMAGES=$(docker compose config --images 2>/dev/null | grep -v '^$' | sort -u | tr '\n' ' ')
if [ -z "$IMAGES" ]; then
    echo "❌ docker compose config --images 空 · 检查 docker-compose.yml"
    exit 1
fi

# 6 个自造 image · 需 build (从 IMAGES 里过滤 catfish- 开头)
BUILT_SERVICES="identity gateway skills-hub web mcp-registry wiki-hub"

# ── ONLY_SERVICES 生效 · 缩小 build 集合 (7/23 P2 后加 · 单 service 增量更新场景) ──
if [ -n "$ONLY_SERVICES" ]; then
    # verify · 每个都在 BUILT_SERVICES 列表 (fail-loud typo)
    for svc in $ONLY_SERVICES; do
        if ! echo " $BUILT_SERVICES " | grep -q " $svc "; then
            echo "❌ ONLY_SERVICES=$ONLY_SERVICES · '$svc' 不在合法列表: $BUILT_SERVICES"
            exit 1
        fi
    done
    BUILT_SERVICES="$ONLY_SERVICES"
    # IMAGES 也 filter · 只保留这几个 service 的 image (从 docker-compose.yml 抽 tag)
    FILTERED_IMAGES=""
    for svc in $ONLY_SERVICES; do
        tag="catfish-${svc}:$(grep -A 20 "^  ${svc}:$" docker-compose.yml | grep '^    image:' | head -1 | awk -F: '{print $NF}')"
        FILTERED_IMAGES="$FILTERED_IMAGES $tag"
    done
    IMAGES=$(echo "$FILTERED_IMAGES" | xargs)
    echo "=== ONLY_SERVICES 模式 · 只 build/save: $BUILT_SERVICES ==="
    echo "=== filtered image list ==="
    echo "$IMAGES" | tr ' ' '\n'
    # 跳过 postgres/nginx pull (它们不在 ONLY_SERVICES 里 · 且假设本地已有)
    SKIP_UPSTREAM_PULL=1

    # ── 7/23 达华 POC 血案 fix · ONLY_SERVICES 场景防呆 ──────
    # 1. tar 名加 -only-<services> 后缀 · 别覆盖全套 image tar (全套是"新客户装机" ·
    #    only 是"已装客户增量更新" · 两码事 · 不能混).
    _svcs_suffix=$(echo "$ONLY_SERVICES" | tr ' ' '_')
    ARM_OUT="$DELIVERY/dahua-poc-central-arm64-${DATE}-only-${_svcs_suffix}.tar.gz"
    AMD_OUT="$DELIVERY/dahua-poc-central-amd64-${DATE}-only-${_svcs_suffix}.tar.gz"
    echo "  tar 名带 -only-${_svcs_suffix} 后缀 · 防跟全套 tar 混 (image-only tar)"

    # 2. 自动关 BUILD_FULL_DELIVERY · FULL tar 场景是"新客户装机 · 需全 image" ·
    #    ONLY_SERVICES 只出部分 image · 打 FULL 会给客户"缺 image 的假 FULL" · 装不起.
    if [ "$BUILD_FULL_DELIVERY" = "1" ]; then
        echo "  ⚠ ONLY_SERVICES 模式 · 自动关 BUILD_FULL_DELIVERY"
        echo "     只出 image-only tar (增量更新) · 不打 FULL (FULL 需全 image)"
        echo "     若真要给新客户 FULL 装机包 · unset ONLY_SERVICES 全打"
        BUILD_FULL_DELIVERY=0
    fi
else
    SKIP_UPSTREAM_PULL=0
    echo "=== 目标 image list (从 docker-compose.yml 抽) ==="
    echo "$IMAGES" | tr ' ' '\n'
fi
echo ""

# ================================================================
# Phase 1 · arm64 从头打
# ================================================================
if [ "$SKIP_ARM64" = "1" ]; then
    echo "⏭  SKIP_ARM64=1 · 跳过 arm64 Phase 1"
else
echo ""
echo "=== Phase 1 · arm64 · 从头打 ==="
export DOCKER_DEFAULT_PLATFORM=linux/arm64

echo "--- 1.1 · pull upstream postgres + nginx (arm64) ---"
# P3.5.79+ (7/23): docker pull 失败 fallback 到本地 image · 别 set -e 死.
# Mac 网络抖 / clash 拦 / docker.io EOF 时若本地已有可用 · 继续跑 · 不阻塞.
# ONLY_SERVICES 模式跳 pull (增量更新 · 上游 image 假设已在)
# 7/23 v2 · 加 verify 本地 image 架构 · 若跟当前 phase 架构不 match (可能上一 phase
# 拉了别架构) · fallback 前 fail-loud · 别混. 老逻辑 fallback 本地不检架构 · 打出的
# tar 里 postgres/nginx 架构可能不对 · verify 段才发现 · 白 build 一轮.
if [ "$SKIP_UPSTREAM_PULL" = "1" ]; then
    echo "  ⏭  ONLY_SERVICES 模式 · skip upstream pull (postgres/nginx 假设本地已有)"
else
for img in postgres:16-alpine nginx:1.27-alpine; do
    # 显式 --platform 强指定 · 别赌 DOCKER_DEFAULT_PLATFORM
    if docker pull --platform linux/arm64 "$img"; then
        echo "  ✓ pull $img (arm64)"
    else
        # 网络挂 · 检本地是否已是**arm64** (若是别的 arch · 不能用 · verify 会挂)
        local_arch=$(docker inspect "$img" --format '{{.Architecture}}' 2>/dev/null || echo "")
        if [ "$local_arch" = "arm64" ]; then
            echo "  ⚠ pull $img 挂 · 但本地已有 arm64 版 · skip pull · 继续"
        elif [ -n "$local_arch" ]; then
            echo "  ❌ pull $img 挂 · 本地是 $local_arch 版 (不匹配 arm64) · 手动重拉:"
            echo "     docker pull --platform linux/arm64 $img"
            exit 1
        else
            echo "  ❌ pull $img 挂 且本地无 · 需连外网 · 或先 docker load 老 tar"
            exit 1
        fi
    fi
done
fi

echo ""
echo "--- 1.2 · build 6 自造 image (arm64) · no-cache 完全干净 ---"
docker compose build --no-cache $BUILT_SERVICES 2>&1

echo ""
echo "--- 1.3 · verify 6 image 都是 arm64/linux ---"
FAIL=0
# ONLY_SERVICES 模式跳 postgres/nginx verify · 它们没被重打 · 本地什么架构都无所谓
if [ "$SKIP_UPSTREAM_PULL" = "1" ]; then
    VERIFY_LIST="$BUILT_SERVICES"
else
    VERIFY_LIST="$BUILT_SERVICES postgres nginx"
fi
for svc in $VERIFY_LIST; do
    case $svc in
        postgres) tag="postgres:16-alpine" ;;
        nginx)    tag="nginx:1.27-alpine" ;;
        *)        # P3.5.79+ (7/23): -A 4 抓不到 · gateway 段中间加了 2 行注释 · image 挤到
                  # 第 7 行 · tag 变空 · docker inspect 挂 set -e. 改 -A 20 (够任何 image 位置)
                  # + 精准 '^    image:' (顶格 4 空格 · 避免 image_name 出现在别的字段里被误抓).
                  tag="catfish-${svc}:$(grep -A 20 "^  ${svc}:$" docker-compose.yml | grep '^    image:' | head -1 | awk -F: '{print $NF}')" ;;
    esac
    arch=$(docker inspect "$tag" --format '{{.Architecture}}/{{.Os}}' 2>/dev/null || echo "MISSING")
    echo "  $tag: $arch"
    if [ "$arch" != "arm64/linux" ] && [ "$arch" != "MISSING" ]; then
        echo "  ⚠ 架构不对 · 应 arm64/linux"
        FAIL=1
    fi
done
[ $FAIL -eq 1 ] && { echo "❌ arm64 verify 失败"; exit 1; }

echo ""
echo "--- 1.4 · save arm64 · gzip 压 ---"
docker save $IMAGES | gzip > "$ARM_OUT"
ls -lh "$ARM_OUT"
echo "✅ arm64 完成"
fi   # end SKIP_ARM64

# ================================================================
# Phase 2 · amd64 从头打 (QEMU emulation)
# ================================================================
if [ "$SKIP_AMD64" = "1" ]; then
    echo "⏭  SKIP_AMD64=1 · 跳过 amd64 Phase 2"
else
echo ""
echo "=== Phase 2 · amd64 · 从头打 (QEMU 慢) ==="
export DOCKER_DEFAULT_PLATFORM=linux/amd64

echo "--- 2.1 · pull upstream postgres + nginx (amd64) ---"
# P3.5.79+ (7/23 v2): 同 Phase 1 · pull 挂 fallback 本地. 显式 --platform + 架构 verify.
# 老 bug · Phase 1 拉了 arm64 后 · Phase 2 pull amd64 挂 · fallback 用了本地 arm64 ·
# 打出的 tar amd64 里 nginx 是 arm64 · verify 挂. 7/23 血案.
for img in postgres:16-alpine nginx:1.27-alpine; do
    if docker pull --platform linux/amd64 "$img"; then
        echo "  ✓ pull $img (amd64)"
    else
        local_arch=$(docker inspect "$img" --format '{{.Architecture}}' 2>/dev/null || echo "")
        if [ "$local_arch" = "amd64" ]; then
            echo "  ⚠ pull $img 挂 · 但本地已有 amd64 版 · skip pull · 继续"
        elif [ -n "$local_arch" ]; then
            echo "  ❌ pull $img 挂 · 本地是 $local_arch 版 (不匹配 amd64) · 手动重拉:"
            echo "     docker pull --platform linux/amd64 $img"
            exit 1
        else
            echo "  ❌ pull $img 挂 且本地无 · 需连外网 · 或先 docker load 老 tar"
            exit 1
        fi
    fi
done

echo ""
echo "--- 2.2 · build 6 自造 image (amd64) · no-cache ---"
docker compose build --no-cache $BUILT_SERVICES 2>&1

echo ""
echo "--- 2.3 · verify 6 image 都是 amd64/linux ---"
FAIL=0
# ONLY_SERVICES 模式跳 postgres/nginx verify · 同 Phase 1.3
if [ "$SKIP_UPSTREAM_PULL" = "1" ]; then
    VERIFY_LIST="$BUILT_SERVICES"
else
    VERIFY_LIST="$BUILT_SERVICES postgres nginx"
fi
for svc in $VERIFY_LIST; do
    case $svc in
        postgres) tag="postgres:16-alpine" ;;
        nginx)    tag="nginx:1.27-alpine" ;;
        *)        # P3.5.79+ (7/23): -A 4 抓不到 · gateway 段中间加了 2 行注释 · image 挤到
                  # 第 7 行 · tag 变空 · docker inspect 挂 set -e. 改 -A 20 (够任何 image 位置)
                  # + 精准 '^    image:' (顶格 4 空格 · 避免 image_name 出现在别的字段里被误抓).
                  tag="catfish-${svc}:$(grep -A 20 "^  ${svc}:$" docker-compose.yml | grep '^    image:' | head -1 | awk -F: '{print $NF}')" ;;
    esac
    arch=$(docker inspect "$tag" --format '{{.Architecture}}/{{.Os}}' 2>/dev/null || echo "MISSING")
    echo "  $tag: $arch"
    if [ "$arch" != "amd64/linux" ] && [ "$arch" != "MISSING" ]; then
        echo "  ⚠ 架构不对 · 应 amd64/linux"
        FAIL=1
    fi
done
[ $FAIL -eq 1 ] && { echo "❌ amd64 verify 失败"; exit 1; }

echo ""
echo "--- 2.4 · save amd64 · gzip 压 ---"
docker save $IMAGES | gzip > "$AMD_OUT"
ls -lh "$AMD_OUT"
echo "✅ amd64 完成"
fi   # end SKIP_AMD64

# ================================================================
# Phase 3 · 打完整交付 tar (P3.5.79+ 7/23 加)
# ================================================================
# image tar + setup.sh + docker-compose.yml + .env.example + companion + docs
# 客户 IT 拿一个 tar 解压 · `bash setup.sh` 秒装 · 不用凑零件.
if [ "$BUILD_FULL_DELIVERY" = "1" ]; then
    echo ""
    echo "=== Phase 3 · 打完整交付 tar (image + setup + config + companion + docs) ==="

    if [ ! -d "$DELIVERY_DIR" ]; then
        echo "❌ $DELIVERY_DIR 找不到 · skip Phase 3"
    else
        # 临时把 image tar 拷进 delivery/dahua-poc/images/ (打完清)
        TEMP_IMAGES="$DELIVERY_DIR/images"
        mkdir -p "$TEMP_IMAGES"

        # ── P3.5.79+ (7/23 达华 199 blood catch) · 补 config 目录 ──
        # 老 bug: delivery/dahua-poc/ 里没 identity-server/ 和 llm-gateway/ 目录 ·
        # docker-compose.yml mount ./identity-server/config:/app/config · host 端
        # 空目录 · 容器 /app/config 空 · users.yaml 不存在 · identity seed 0 用户 ·
        # admin@catfish.com 完全无法创建 · 客户装完根本登不进.
        # 修: tar czf 前 · 把 central/{identity-server,llm-gateway}/config 拷进
        # delivery/dahua-poc/ 对应位置 · 打进 tar. 客户装机就有 seed 源.
        #
        # ── P3.5.79+ (7/23 达华 POC · overlay 机制) ─────────────────────
        # 两步 rsync 实现 · 通用 baseline + 客户定制 override:
        #   1. central/$svc/config             → delivery/$svc/config      (baseline)
        #   2. delivery/$svc/config-overlay/*  → delivery/$svc/config/*    (覆盖)
        # overlay 目录里只放**跟通用不同**的文件 · git track 差异 · 打 tar
        # 时 exclude · 客户看不到 overlay · 只见 merge 后 config/.
        # 现有 overlay · delivery/dahua-poc/llm-gateway/config-overlay/roles.yaml
        # (达华单 model qwen3.7-plus · 覆盖通用 7-role 版)
        echo ""
        echo "--- 3.pre · sync central config → delivery + apply overlay ---"
        for svc in identity-server llm-gateway; do
            SRC_CFG="$CENTRAL/$svc/config"
            DST_CFG="$DELIVERY_DIR/$svc/config"
            OVERLAY="$DELIVERY_DIR/$svc/config-overlay"
            if [ -d "$SRC_CFG" ]; then
                mkdir -p "$DST_CFG"
                # a. 通用 baseline · 排敏感 + 排 .dahua 后缀 (老 · 已挪 overlay)
                # 7/23 加 database.yaml exclude · 军规血泪 · central/*/config/database.yaml
                # 含开发环境 PG 密码 (URL-encoded 明文) · rsync 不排会拷进 delivery ·
                # 若 tar 也不排会打进客户包. 客户不该看到我们本地 dev DB 密码. 加 exclude
                # + 客户装机时 setup.sh 从 database.yaml.example 生成新的 (走 env 注入).
                rsync -a --exclude='.env' --exclude='.DS_Store' \
                      --exclude='users.yaml' --exclude='clients.yaml' \
                      --exclude='database.yaml' \
                      --exclude='*.bak' \
                      --exclude='*.dahua' \
                      "$SRC_CFG/" "$DST_CFG/" 2>/dev/null || \
                cp -R "$SRC_CFG/"* "$DST_CFG/" 2>/dev/null
                echo "  ✓ $svc/config baseline ($(ls "$DST_CFG" | wc -l | tr -d ' ') files)"
            else
                echo "  ⚠ $SRC_CFG 不存在 · skip baseline"
            fi
            # b. overlay 覆盖 · 排 README (说明文档 · 客户不需要)
            if [ -d "$OVERLAY" ]; then
                OVERLAY_COUNT=$(find "$OVERLAY" -type f ! -name 'README.md' | wc -l | tr -d ' ')
                if [ "$OVERLAY_COUNT" -gt 0 ]; then
                    rsync -a --exclude='README.md' "$OVERLAY/" "$DST_CFG/" 2>/dev/null
                    echo "  ✓ $svc overlay applied ($OVERLAY_COUNT files · 达华定制)"
                fi
            fi
        done

        for arch in arm64 amd64; do
            SRC_TAR=""
            OUT_TAR=""
            # 7/23 fix · SKIP 严格默认 (跟 Phase 1/2 语义一致 · 用户显式说不打就不打).
            # 仅当 REPACK_ONLY=1 时忽略 SKIP · 复用老 image tar (config-only 更新场景).
            if [ "$arch" = "arm64" ]; then
                if [ "$SKIP_ARM64" = "1" ] && [ "$REPACK_ONLY" != "1" ]; then
                    echo "  ⏭  SKIP_ARM64=1 · skip 3.arm64 (若要用老 image tar 重打 · REPACK_ONLY=1)"
                    continue
                fi
                SRC_TAR="$ARM_OUT"; OUT_TAR="$FULL_ARM_OUT"
            elif [ "$arch" = "amd64" ]; then
                if [ "$SKIP_AMD64" = "1" ] && [ "$REPACK_ONLY" != "1" ]; then
                    echo "  ⏭  SKIP_AMD64=1 · skip 3.amd64 (若要用老 image tar 重打 · REPACK_ONLY=1)"
                    continue
                fi
                SRC_TAR="$AMD_OUT"; OUT_TAR="$FULL_AMD_OUT"
            fi
            # image tar 不存在 (Phase 1/2 都没跑过 · 且 $DATE 不匹配老 tar) · 明报
            if [ ! -f "$SRC_TAR" ]; then
                echo "  ⚠ $SRC_TAR 不存在 · skip $arch"
                continue
            fi

            echo ""
            echo "--- 3.$arch · 打完整 tar → $(basename "$OUT_TAR") ---"

            # ── 版本戳 (8/1) ───────────────────────────────────────────
            #
            # 分两次打平台之后, DATE 相同**不代表代码相同**: 7/31 晚上就撞了 ——
            # arm64 22:44 打完, 中间修了个前端 bug, amd64 再打就带上了修复,
            # 而两个包都叫 -20260731。上一版的漂移检测只比日期, 这种情况下
            # 它会打出"两个平台都齐了", 而且是错的。
            #
            # 所以把 git SHA 写进包里, 让"是不是同一次代码"可验证 —— 既给
            # 下面的跨平台比对用, 也给半年后"客户装的到底是哪一版"用。
            #
            # SHA 是**脚本启动时**抓的 (见文件上方), 不是这一刻 —— 那才对得上
            # docker 读构建上下文的时间点。
            {
                echo "arch=$arch"
                echo "date=$DATE"
                echo "git_sha=$GIT_SHA"
                echo "git_dirty=$([ -n "$GIT_DIRTY" ] && echo yes || echo no)"
                echo "built_at=$(date '+%Y-%m-%d %H:%M:%S %Z')"
                echo "built_on=$(uname -sm)"
            } > "$DELIVERY_DIR/BUILD-INFO.txt"
            if [ -n "$GIT_DIRTY" ]; then
                echo "  ⚠ 工作区有未提交改动 · git_sha=$GIT_SHA 描述不全这个包:"
                echo "$GIT_DIRTY" | head -10 | sed 's|^|      |'
            else
                echo "  · 版本戳 git_sha=$GIT_SHA"
            fi
            # P3.5.79+ (7/23 catch): 每循环前清 images/ · 别累加 · 否则第 2 arch
            # 循环开始时 images/ 里还有第 1 arch 的 tar · 一起打进 FULL · 大 400M+.
            rm -f "$TEMP_IMAGES"/*.tar.gz
            cp "$SRC_TAR" "$TEMP_IMAGES/"

            # 打 tar (从 repo 根 · tar 里路径 delivery/dahua-poc/...)
            # P3.5.79+ (7/23 鸿波 catch '最简 · 多余不要 · 缺的必带'):
            # 排 · docs      (客户自己写 SOP)
            #    · README    (太长 · setup.sh 里已注释)
            #    · companion (员工装 Companion 客户端用 · 员工侧单独分发 · 服务端 IT 不需要)
            #    · certs     (自签 cert setup.sh --ENABLE_HTTPS=1 现生成)
            #    · .env      (敏感 · 客户自填)
            # 保 · identity-server/config (users.yaml.example 必带 · setup.sh cp 到 users.yaml)
            #    · llm-gateway/config     (models.yaml + roles.yaml 必带 · gateway mount 用)
            cd "$REPO_ROOT"
            # P3.5.81 (7/28) 追加 3 条敏感兜底 (rsync 已排 · tar 再挡一道:
            # 21:01 的包实际带出过 database.yaml · 内含真实 dev PG 密码):
            tar czf "$OUT_TAR" \
                --exclude='delivery/dahua-poc/certs' \
                --exclude='delivery/dahua-poc/.env' \
                --exclude='delivery/dahua-poc/docs' \
                --exclude='delivery/dahua-poc/README.md' \
                --exclude='delivery/dahua-poc/companion' \
                --exclude='delivery/dahua-poc/*/config-overlay' \
                --exclude='delivery/dahua-poc/*/config/database.yaml' \
                --exclude='*.bak' \
                --exclude='.env.bak.*' \
                delivery/dahua-poc/
            cd "$CENTRAL"

            ls -lh "$OUT_TAR"

            # ── P3.5.81 (7/28): 打完立刻验包 · fail-loud ─────────────────
            # 7/28 两个方向都翻过车: 该带的没带 (clients.yaml.example 缺 →
            # 客户装机全翻 401 invalid_client) + 不该带的带了 (database.yaml
            # 真实 dev PG 密码). 打包脚本自己验 · 不过不出包 · 不靠人肉 tar tzf.
            TLIST=$(tar tzf "$OUT_TAR")
            VERIFY_FAIL=0
            # 三个计数器: 通过那行的 "必带 7 / 敏感 4 / 标记 3" 从前是写死的数字,
            # 8/1 往 marker 里加了第 4 条, 那行还印着 "3" —— 报告的条数和真查的
            # 条数对不上, 而且是往少了报, 看着像少查了一条. 这种小谎最烦人:
            # 它让人开始怀疑整行输出. 改成边跑边数.
            MUST_N=0; MUSTNOT_N=0; MARKER_N=0
            for must in \
                "delivery/dahua-poc/setup.sh" \
                "delivery/dahua-poc/verify-login.sh" \
                "delivery/dahua-poc/docker-compose.yml" \
                "delivery/dahua-poc/docker-compose.https.yml" \
                "delivery/dahua-poc/.env.example" \
                "delivery/dahua-poc/BUILD-INFO.txt" \
                "delivery/dahua-poc/identity-server/config/users.yaml.example" \
                "delivery/dahua-poc/identity-server/config/clients.yaml.example"; do
                MUST_N=$((MUST_N + 1))
                if ! echo "$TLIST" | grep -qx "$must"; then
                    echo "  ❌ 验包: 缺 $must"; VERIFY_FAIL=1
                fi
            done
            for mustnot in \
                "delivery/dahua-poc/.env" \
                "delivery/dahua-poc/identity-server/config/users.yaml" \
                "delivery/dahua-poc/identity-server/config/clients.yaml" \
                "delivery/dahua-poc/identity-server/config/database.yaml"; do
                MUSTNOT_N=$((MUSTNOT_N + 1))
                if echo "$TLIST" | grep -qx "$mustnot"; then
                    echo "  ❌ 验包: 不该带 $mustnot (敏感 / 应装机时生成)"; VERIFY_FAIL=1
                fi
            done
            # setup.sh 是不是**新**版本 · 抽出来查几个本轮修复的标记
            #
            # ⚠ 这份 marker 清单是**会过期的** —— 它只认得写它那天的"新版本"。
            #   8/1 加 CATFISH_SECRET_KEY 生成时就发现: 三个 marker 全是 7/28 的,
            #   哪怕打进去的是漏了整段密钥生成的老 setup.sh, 验包照样打勾。
            #   以后每往 setup.sh 加一段**装不上就废**的逻辑, 这里补一条。
            SETUP_IN_TAR=$(tar xzf "$OUT_TAR" -O delivery/dahua-poc/setup.sh)
            for marker in "clients.yaml 生成" "DASHSCOPE_API_KEY 是空的" "CERT_DAYS=397" \
                          "CATFISH_SECRET_KEY 已生成"; do
                MARKER_N=$((MARKER_N + 1))
                if ! echo "$SETUP_IN_TAR" | grep -q "$marker"; then
                    echo "  ❌ 验包: setup.sh 缺标记「$marker」→ 打进去的是老版本"; VERIFY_FAIL=1
                fi
            done
            if [ "$VERIFY_FAIL" = "1" ]; then
                echo "  ❌ $arch 验包不过 · 这个 tar 不能发"
                exit 1
            fi
            echo "  ✓ 验包过 · 必带 $MUST_N 在 / 敏感 $MUSTNOT_N 不在 / setup.sh $MARKER_N 标记在"
            echo "  ✅ $arch 完整 tar 完成"
        done

        # 清临时 image (delivery/dahua-poc/images/ 里不留 tar · git 也 ignore)
        rm -f "$TEMP_IMAGES"/*.tar.gz
        echo ""
        echo "→ 客户 IT 装机 3 步:"
        echo "    1. tar xzf dahua-poc-FULL-<arch>-${DATE}.tar.gz"
        echo "    2. cd delivery/dahua-poc/"
        echo "    3. IMAGE_TAR=./images/dahua-poc-central-<arch>-${DATE}.tar.gz bash setup.sh"
    fi
fi

# ================================================================
# DONE
# ================================================================
echo ""
echo "=== DONE ==="
echo ""
# ── 产物清单 (P3.5.80 · 7/28 重写) ────────────────────────────────
#
# 老逻辑用 SKIP_* 判断该不该列 —— 但 REPACK_ONLY=1 恰恰是 "SKIP=1 却确实产出了
# FULL 包" 的组合, 于是两栏都不打印; 紧接着一句光秃秃的
#     ls -lh "$ARM_OUT" "$AMD_OUT"
# 把 image-only 包倒在了 "FULL delivery tar" 这个标题底下 ——
# 谁照这个输出挑文件发给客户, 就会把 image-only 当成 FULL 发出去, 客户解开
# 没有 setup.sh, 装不了.
#
# 改成按**文件在不在**列 (产物就是产物, 跟跑了哪个 phase 无关), 且每行自带
# 类型标签, 不靠标题分组.
# 8/1 · 这个函数原来有两个毛病, 都是"看着没事其实说不清":
#
# 1. 大小用 du, 上面 3.$arch 那行用 ls -lh —— **同一个文件两个数**.
#    7/31 arm64 那次: ls 打 419M, 这里打 433M. du 报的是磁盘分配块,
#    ls 报的是字节数; 要把文件传到客户服务器上, 看的是后者. 两个数摆在
#    同一份输出里, 谁也不知道该信哪个, 传完对不上还得回来查半天.
#    统一走 ls, 跟上面那行天然一致.
#
# 2. printf 的 %-8s 是按**字节**补空格的, 中文一个字 3 字节:
#    "仅镜像" = 9 字节 > 8 → 一个空格都不补, 而 "FULL" = 4 字节补到 8.
#    结果两行对不齐 (7/31 的输出里就能看到"仅镜像"那行整个左移).
#    干脆把中文标签挪到行尾 —— 前面两列都是 ASCII, %-Ns 才算得准.
_list_artifact() {   # $1=路径  $2=类型说明
    [ -f "$1" ] || return 0
    # ls -lh 的第 5 列是 size (perms links owner group size ...)
    printf "    %-52s %7s   %s\n" \
        "$(basename "$1")" "$(ls -lh "$1" | awk '{print $5}')" "$2"
}

echo "→ 产物 (只列真实存在的文件):"
echo "    文件名                                                   大小   类型"
_list_artifact "$FULL_ARM_OUT" "FULL"
_list_artifact "$FULL_AMD_OUT" "FULL"
_list_artifact "$ARM_OUT"      "仅镜像"
_list_artifact "$AMD_OUT"      "仅镜像"
echo ""
echo "    FULL   = 给新客户装机 (含 setup.sh + config + 镜像) · 解开就能 bash setup.sh"
echo "    仅镜像 = 给已装机客户换镜像 · 里面**没有** setup.sh, 单独发过去装不了"

# ── 一次一个平台 · 把另一半的命令连 DATE 一起印出来 (8/1) ──────────
#
# 分两次跑最大的坑是 **DATE 漂移** (见文件头 ARCH 那段)。这里不指望人记得
# 加 DATE=, 直接把下一条命令连日期一起打出来, 照抄即可。
#
# 顺带扫一眼另一个平台是不是有**别的日期**的包 —— 有的话说明漂移已经发生,
# 这两个包不是同一次代码, 不能当一对发给客户。
case "$ARCH" in
    arm64) _other="amd64" ;;
    amd64) _other="arm64" ;;
    *)     _other="" ;;
esac
if [ -n "$_other" ]; then
    echo ""
    _other_full="$DELIVERY/dahua-poc-FULL-${_other}-${DATE}.tar.gz"
    if [ -f "$_other_full" ]; then
        # 日期相同**不代表代码相同** —— 7/31 就是这么撞的: arm64 打完之后
        # 修了个 bug, amd64 再打就带上了修复, 两个包却都叫 -20260731。
        # 所以比对 BUILD-INFO.txt 里的 git_sha, 不是比日期。
        _other_sha=$(tar xzf "$_other_full" -O delivery/dahua-poc/BUILD-INFO.txt 2>/dev/null \
                     | sed -n 's/^git_sha=//p' || true)
        if [ -z "$_other_sha" ]; then
            echo "→ 两个平台的包都在 (${DATE}), 但 ${_other} 那个是**加版本戳之前**打的,"
            echo "  没法确认两边是同一次代码。要保险就把 ${_other} 重打一遍。"
        elif [ "$_other_sha" = "$GIT_SHA" ]; then
            echo "→ 两个平台都齐了 (${DATE} · git ${GIT_SHA}) —— 同一次代码 ✓"
        else
            echo "  ❌ 两个平台**不是同一次代码**:"
            echo "       ${ARCH}: git ${GIT_SHA}"
            echo "       ${_other}: git ${_other_sha}"
            echo "     日期一样但代码不一样 —— 别当一对发出去。"
            echo "     把落后的那个平台按当前代码重打一遍。"
        fi
        if [ -n "$GIT_DIRTY" ]; then
            echo "  ⚠ 而且本次构建时工作区是脏的, git ${GIT_SHA} 描述不全这个包。"
        fi
    else
        echo "→ 还差 ${_other} 平台。下一条命令 (DATE 照抄, 别让它跨零点变成明天):"
        echo "    cd $CENTRAL"
        echo "    DATE=${DATE} ARCH=${_other} nohup bash $(basename "$0") \\"
        echo "        > /tmp/build-${_other}-${DATE}.log 2>&1 &"
    fi
    # ls 没匹配 / grep 全过滤掉都会返回非 0 · set -e 下必须兜住
    _drift=$(ls "$DELIVERY"/dahua-poc-FULL-"${_other}"-*.tar.gz 2>/dev/null \
             | grep -v -- "-${DATE}.tar.gz" || true)
    if [ -n "$_drift" ]; then
        echo ""
        echo "  ⚠ ${_other} 有 FULL 包, 但日期不是 ${DATE}:"
        echo "$_drift" | sed 's|^|      |'
        echo "      两个平台日期对不上 = 很可能不是同一次代码, 别当一对发出去。"
    fi
fi
echo ""
# P3.5.80 (7/28): 这里原来写死 "20260720" 的文件名, 还给了一套
# "cd /path/to/catfish/central && docker compose up -d" 的指令 ——
# 跟上面刚打印的 3 步装机流程互相矛盾, 且 central 目录根本不在交付包里.
# 同一份输出给两套冲突指令, 客户 IT 必然照错的那套做. 删掉, 只留增量更新场景.
echo "已装机客户做增量更新 (只换 image · 不动 .env / 数据卷):"
echo "  1. 把 dahua-poc-central-<ARCH>-${DATE}.tar.gz 传到服务器"
echo "  2. gunzip -c dahua-poc-central-<ARCH>-${DATE}.tar.gz | docker load"
echo "  3. cd <装机目录>/delivery/dahua-poc/"
echo "     docker compose up -d --force-recreate      # HTTP 模式"
echo "     docker compose -f docker-compose.yml -f docker-compose.https.yml \\"
echo "                    up -d --force-recreate      # HTTPS 模式"
echo "  4. bash verify-login.sh                       # 验登录链路"
