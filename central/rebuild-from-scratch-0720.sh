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

CENTRAL="$HOME/person_task/catfish/central"
DELIVERY="$HOME/Downloads/catfish-达华POC-0715"

# 目标 tar (今天 7/20 打 · gzip 压缩)
ARM_OUT="$DELIVERY/dahua-poc-central-arm64-20260720.tar.gz"
AMD_OUT="$DELIVERY/dahua-poc-central-amd64-20260720.tar.gz"

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
echo ""
echo "=== Phase 1 · arm64 · 从头打 ==="
export DOCKER_DEFAULT_PLATFORM=linux/arm64

echo "--- 1.1 · pull upstream postgres + nginx (arm64) ---"
docker pull postgres:16-alpine
docker pull nginx:1.27-alpine

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
        *)        tag="catfish-${svc}:$(grep -A 4 "^  ${svc}:$" docker-compose.yml | grep 'image:' | head -1 | awk -F: '{print $NF}')" ;;
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

# ================================================================
# Phase 2 · amd64 从头打 (QEMU emulation)
# ================================================================
echo ""
echo "=== Phase 2 · amd64 · 从头打 (QEMU 慢) ==="
export DOCKER_DEFAULT_PLATFORM=linux/amd64

echo "--- 2.1 · pull upstream postgres + nginx (amd64) ---"
docker pull postgres:16-alpine
docker pull nginx:1.27-alpine

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
        *)        tag="catfish-${svc}:$(grep -A 4 "^  ${svc}:$" docker-compose.yml | grep 'image:' | head -1 | awk -F: '{print $NF}')" ;;
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

# ================================================================
# DONE
# ================================================================
echo ""
echo "=== DONE ==="
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
