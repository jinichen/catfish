#!/bin/bash
# BL-P29-REBUILD (7/20 Task #29): 中央 image tar 重打 · 累积 7 天改动.
#
# 覆盖:
#   - Task #29 identity  · POST /me/password + revoke_all_for_sub + change_password
#   - Task #16 gateway   · catfish-auto model 动态路由
#   - Task #14 gateway   · errors.py 中文 auth failed match
#   - Task #17 identity  · CORS Tauri origin fix
#   - Task #7  gateway   · orjson 补
#
# 与 rebuild-full-image-tars.sh (7/17) 差异:
#   - build **两个** image (identity + gateway) · 7/17 版只 build identity
#   - 源 tar 路径 · ~/Downloads/catfish-达华POC-0715 (7/17 版用 -交付-0715)
#   - .tar.gz gz 压缩 (7/17 版是 .tar)
#
# 用法:
#   nohup bash rebuild-full-image-tars-0720.sh > /tmp/full-rebuild-0720.log 2>&1 &
#   disown
#
# 睡醒验:
#   grep -E "===|✅|❌" /tmp/full-rebuild-0720.log
#   ls -lh ~/Downloads/catfish-达华POC-0715/dahua-poc-central-*-20260720.tar.gz

set -euo pipefail

CENTRAL="$HOME/person_task/catfish/central"
DELIVERY="$HOME/Downloads/catfish-达华POC-0715"

# 源 (7/18 老 tar · 拿来 load 出 postgres/nginx/hub/web/mcp-registry/wiki-hub 底子)
ARM_SRC="$DELIVERY/dahua-poc-central-arm64-20260718.tar.gz"
AMD_SRC="$DELIVERY/dahua-poc-central-amd64-20260718.tar.gz"

# 目标 (今天 7/20 打的 · 覆盖累积改动)
ARM_OUT="$DELIVERY/dahua-poc-central-arm64-20260720.tar.gz"
AMD_OUT="$DELIVERY/dahua-poc-central-amd64-20260720.tar.gz"

cd "$CENTRAL"

# 8 image list · save 时用. Task #29 rebuild identity + gateway 两个 ·
# 其余 6 个从老 tar load 复用 · 不动.
IMAGES="postgres:16-alpine nginx:1.27-alpine \
        catfish-identity:0.1.0 catfish-gateway:0.1.0 \
        catfish-skills-hub:0.1.0 catfish-web:0.1.0 \
        catfish-mcp-registry:0.1.0 catfish-wiki-hub:0.1.0"

echo "=== 前置检查 · 源 tar 是否在 ==="
if [ ! -f "$ARM_SRC" ]; then
    echo "❌ $ARM_SRC 不在"; exit 1
fi
if [ ! -f "$AMD_SRC" ]; then
    echo "❌ $AMD_SRC 不在"; exit 1
fi
ls -lh "$ARM_SRC" "$AMD_SRC"

echo ""
echo "=== Phase 1 · arm64 完整重打 ==="
echo "--- 1.1 · load arm64 底子 (7/18 版) ---"
docker load -i "$ARM_SRC"

echo ""
echo "--- 1.2 · rebuild identity (Task #29) + gateway (Task #16/#14/#7) · arm64 ---"
export DOCKER_DEFAULT_PLATFORM=linux/arm64
docker compose build identity gateway 2>&1

echo ""
echo "--- 1.3 · verify 两个 image 都是 arm64 ---"
IARCH=$(docker inspect catfish-identity:0.1.0 --format '{{.Architecture}}/{{.Os}}')
GARCH=$(docker inspect catfish-gateway:0.1.0 --format '{{.Architecture}}/{{.Os}}')
echo "  identity: $IARCH"
echo "  gateway:  $GARCH"
if [ "$IARCH" != "arm64/linux" ] || [ "$GARCH" != "arm64/linux" ]; then
    echo "❌ 架构不对 · 应 arm64/linux"; exit 1
fi

echo ""
echo "--- 1.4 · save arm64 · gzip 压 (与 7/18 老 tar 格式一致) ---"
docker save $IMAGES | gzip > "$ARM_OUT"
ls -lh "$ARM_OUT"
echo "✅ arm64 完成"

echo ""
echo "=== Phase 2 · amd64 完整重打 ==="
echo "--- 2.1 · load amd64 底子 (7/18 版 · 覆盖 arm64) ---"
docker load -i "$AMD_SRC"

echo ""
echo "--- 2.2 · rebuild identity + gateway · amd64 (QEMU emulation · 8-15 min) ---"
export DOCKER_DEFAULT_PLATFORM=linux/amd64
docker compose build identity gateway 2>&1

echo ""
echo "--- 2.3 · verify 两个 image 都是 amd64 ---"
IARCH=$(docker inspect catfish-identity:0.1.0 --format '{{.Architecture}}/{{.Os}}')
GARCH=$(docker inspect catfish-gateway:0.1.0 --format '{{.Architecture}}/{{.Os}}')
echo "  identity: $IARCH"
echo "  gateway:  $GARCH"
if [ "$IARCH" != "amd64/linux" ] || [ "$GARCH" != "amd64/linux" ]; then
    echo "❌ 架构不对 · 应 amd64/linux"; exit 1
fi

echo ""
echo "--- 2.4 · save amd64 · gzip 压 ---"
docker save $IMAGES | gzip > "$AMD_OUT"
ls -lh "$AMD_OUT"
echo "✅ amd64 完成"

echo ""
echo "=== DONE ==="
ls -lh "$ARM_OUT" "$AMD_OUT"
echo ""
echo "达华 IT 现场用法:"
echo "  gunzip -c dahua-poc-central-<ARCH>-20260720.tar.gz | docker load"
echo "  cd /path/to/catfish/central && docker compose up -d"
echo ""
echo "累积 fix:"
echo "  Task #29 员工自主改密码 · POST /me/password + refresh_token revoke"
echo "  Task #16 catfish-auto model · gateway 动态路由"
echo "  Task #14 中文 auth failed match"
echo "  Task #17 CORS Tauri origin"
echo "  Task #7  orjson 补 · Task #6 双 audience"
