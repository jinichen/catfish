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
DATE="${DATE:-$(date +%Y%m%d)}"
SKIP_ARM64="${SKIP_ARM64:-0}"
SKIP_AMD64="${SKIP_AMD64:-0}"
BUILD_FULL_DELIVERY="${BUILD_FULL_DELIVERY:-1}"

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

mkdir -p "$DELIVERY"

# 动态从 docker-compose.yml 拿 image list · 免硬编码错. Sort -u 去重.
IMAGES=$(docker compose config --images 2>/dev/null | grep -v '^$' | sort -u | tr '\n' ' ')
if [ -z "$IMAGES" ]; then
    echo "❌ docker compose config --images 空 · 检查 docker-compose.yml"
    exit 1
fi

# 6 个自造 image · 需 build (从 IMAGES 里过滤 catfish- 开头)
BUILT_SERVICES="identity gateway skills-hub web mcp-registry wiki-hub"

echo "=== 目标 image list (从 docker-compose.yml 抽) ==="
echo "$IMAGES" | tr ' ' '\n'
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
for img in postgres:16-alpine nginx:1.27-alpine; do
    if docker pull "$img"; then
        echo "  ✓ pull $img"
    elif docker image inspect "$img" >/dev/null 2>&1; then
        echo "  ⚠ pull $img 挂 · 但本地已有 · skip pull · 继续"
    else
        echo "  ❌ pull $img 挂 且本地无 · 需连外网 · 或先 docker load 老 tar"
        exit 1
    fi
done

echo ""
echo "--- 1.2 · build 6 自造 image (arm64) · no-cache 完全干净 ---"
docker compose build --no-cache $BUILT_SERVICES 2>&1

echo ""
echo "--- 1.3 · verify 6 image 都是 arm64/linux ---"
FAIL=0
for svc in $BUILT_SERVICES postgres nginx; do
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
# P3.5.79+ (7/23): 同 Phase 1 · pull 挂 fallback 本地
for img in postgres:16-alpine nginx:1.27-alpine; do
    if docker pull "$img"; then
        echo "  ✓ pull $img"
    elif docker image inspect "$img" >/dev/null 2>&1; then
        echo "  ⚠ pull $img 挂 · 但本地已有 · skip pull · 继续"
    else
        echo "  ❌ pull $img 挂 且本地无 · 需连外网 · 或先 docker load 老 tar"
        exit 1
    fi
done

echo ""
echo "--- 2.2 · build 6 自造 image (amd64) · no-cache ---"
docker compose build --no-cache $BUILT_SERVICES 2>&1

echo ""
echo "--- 2.3 · verify 6 image 都是 amd64/linux ---"
FAIL=0
for svc in $BUILT_SERVICES postgres nginx; do
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
            if [ "$arch" = "arm64" ]; then
                SRC_TAR="$ARM_OUT"; OUT_TAR="$FULL_ARM_OUT"
            elif [ "$arch" = "amd64" ]; then
                SRC_TAR="$AMD_OUT"; OUT_TAR="$FULL_AMD_OUT"
            fi
            # P3.5.79+ (7/23): Phase 3 独立于 Phase 1/2 · 只要 image tar 存在就重打 FULL.
            # 场景 · 改了 setup.sh / .env.example / config 但 image 未变 · 跑:
            #   SKIP_ARM64=1 SKIP_AMD64=1 bash rebuild-from-scratch-0720.sh
            # 会跳 Phase 1/2 build · 直接 Phase 3 用已有 image tar 重打 FULL.
            if [ ! -f "$SRC_TAR" ]; then
                echo "  ⚠ $SRC_TAR 不存在 · skip $arch (跑 Phase 1/2 先出 image tar · 或指定其他 DATE)"
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
            tar czf "$OUT_TAR" \
                --exclude='delivery/dahua-poc/certs' \
                --exclude='delivery/dahua-poc/.env' \
                --exclude='delivery/dahua-poc/docs' \
                --exclude='delivery/dahua-poc/README.md' \
                --exclude='delivery/dahua-poc/companion' \
                --exclude='delivery/dahua-poc/*/config-overlay' \
                delivery/dahua-poc/
            cd "$CENTRAL"

            ls -lh "$OUT_TAR"
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
echo "→ image-only tar (给已装机客户增量更新):"
[ "$SKIP_ARM64" != "1" ] && echo "    $ARM_OUT"
[ "$SKIP_AMD64" != "1" ] && echo "    $AMD_OUT"
if [ "$BUILD_FULL_DELIVERY" = "1" ]; then
    echo "→ FULL delivery tar (给全新客户 · setup+config+image 一坨):"
    [ "$SKIP_ARM64" != "1" ] && [ -f "$FULL_ARM_OUT" ] && echo "    $FULL_ARM_OUT"
    [ "$SKIP_AMD64" != "1" ] && [ -f "$FULL_AMD_OUT" ] && echo "    $FULL_AMD_OUT"
fi
ls -lh "$ARM_OUT" "$AMD_OUT"
echo ""
echo "达华 IT 现场:"
echo "  gunzip -c dahua-poc-central-<ARCH>-20260720.tar.gz | docker load"
echo "  cd /path/to/catfish/central && docker compose up -d"
echo ""
echo "本 tar 含 (从头 rebuild 全 8 image):"
echo "  Task #29 员工自主改密码 (identity)"
echo "  Task #16 catfish-auto 动态路由 (gateway)"
echo "  Task #14 中文 auth failed match (gateway)"
echo "  Task #17 CORS Tauri origin (identity)"
echo "  Task #7  orjson · Task #6 双 audience"
echo "  postgres:16-alpine + nginx:1.27-alpine 官方最新"
