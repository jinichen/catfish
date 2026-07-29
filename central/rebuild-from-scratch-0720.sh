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
# 用法:
#   nohup bash rebuild-from-scratch-0720.sh > /tmp/full-rebuild-clean-0720.log 2>&1 &
#   disown
#
# 睡醒验:
#   grep -E "===|✅|❌" /tmp/full-rebuild-clean-0720.log
#   ls -lh ~/Downloads/catfish-达华POC-0715/dahua-poc-central-*-20260720.tar.gz

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
DATE="${DATE:-$(date +%Y%m%d)}"
SKIP_ARM64="${SKIP_ARM64:-0}"
SKIP_AMD64="${SKIP_AMD64:-0}"
BUILD_FULL_DELIVERY="${BUILD_FULL_DELIVERY:-1}"
ONLY_SERVICES="${ONLY_SERVICES:-}"
REPACK_ONLY="${REPACK_ONLY:-0}"

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
            for must in \
                "delivery/dahua-poc/setup.sh" \
                "delivery/dahua-poc/verify-login.sh" \
                "delivery/dahua-poc/docker-compose.yml" \
                "delivery/dahua-poc/docker-compose.https.yml" \
                "delivery/dahua-poc/.env.example" \
                "delivery/dahua-poc/identity-server/config/users.yaml.example" \
                "delivery/dahua-poc/identity-server/config/clients.yaml.example"; do
                if ! echo "$TLIST" | grep -qx "$must"; then
                    echo "  ❌ 验包: 缺 $must"; VERIFY_FAIL=1
                fi
            done
            for mustnot in \
                "delivery/dahua-poc/.env" \
                "delivery/dahua-poc/identity-server/config/users.yaml" \
                "delivery/dahua-poc/identity-server/config/clients.yaml" \
                "delivery/dahua-poc/identity-server/config/database.yaml"; do
                if echo "$TLIST" | grep -qx "$mustnot"; then
                    echo "  ❌ 验包: 不该带 $mustnot (敏感 / 应装机时生成)"; VERIFY_FAIL=1
                fi
            done
            # setup.sh 是不是**新**版本 · 抽出来查 3 个本轮修复的标记
            SETUP_IN_TAR=$(tar xzf "$OUT_TAR" -O delivery/dahua-poc/setup.sh)
            for marker in "clients.yaml 生成" "DASHSCOPE_API_KEY 是空的" "CERT_DAYS=397"; do
                if ! echo "$SETUP_IN_TAR" | grep -q "$marker"; then
                    echo "  ❌ 验包: setup.sh 缺标记「$marker」→ 打进去的是老版本"; VERIFY_FAIL=1
                fi
            done
            if [ "$VERIFY_FAIL" = "1" ]; then
                echo "  ❌ $arch 验包不过 · 这个 tar 不能发"
                exit 1
            fi
            echo "  ✓ 验包过 · 必带 7 在 / 敏感 4 不在 / setup.sh 3 标记在"
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
_list_artifact() {   # $1=路径  $2=类型说明
    [ -f "$1" ] || return 0
    printf "    %-8s %-58s %s\n" \
        "$2" "$(basename "$1")" "$(du -h "$1" | cut -f1)"
}

echo "→ 产物 (只列真实存在的文件):"
echo "    类型     文件名                                                     大小"
_list_artifact "$FULL_ARM_OUT" "FULL"
_list_artifact "$FULL_AMD_OUT" "FULL"
_list_artifact "$ARM_OUT"      "仅镜像"
_list_artifact "$AMD_OUT"      "仅镜像"
echo ""
echo "    FULL   = 给新客户装机 (含 setup.sh + config + 镜像) · 解开就能 bash setup.sh"
echo "    仅镜像 = 给已装机客户换镜像 · 里面**没有** setup.sh, 单独发过去装不了"
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
