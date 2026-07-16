#!/bin/bash
# BL-TAURI-CORS 后重打完整双架构 image tar (7/17 凌晨).
#
# 场景: identity code 加了 CORS Tauri origin fix. 需重打**完整** image tar
# (不是单发 identity), 达华 IT 一次 docker load 覆盖. 用户对: 大包更 clean.
#
# 前提: 本地 docker 里的 image 是某个架构 (arm64 or amd64, 取决于最后 build).
# 另一个架构的 image 从云端 tar 恢复 · 再 rebuild identity · 再 save 完整 tar.
#
# 用法:
#   nohup bash rebuild-full-image-tars.sh > /tmp/full-rebuild.log 2>&1 &
#   disown
#
# 睡醒 verify:
#   grep -E "===" /tmp/full-rebuild.log
#   ls -lh ~/Downloads/catfish-达华交付-0715/catfish-central-images-*.tar

set -euo pipefail

CENTRAL="$HOME/person_task/catfish/central"
DELIVERY="$HOME/Downloads/catfish-达华交付-0715"

ARM_TAR="$DELIVERY/catfish-central-images-arm64.tar"
AMD_TAR="$DELIVERY/catfish-central-images-amd64.tar"

cd "$CENTRAL"

# 8 image list · save 时用
IMAGES="postgres:16-alpine nginx:1.27-alpine \
        catfish-identity:0.1.0 catfish-gateway:0.1.0 \
        catfish-skills-hub:0.1.0 catfish-web:0.1.0 \
        catfish-mcp-registry:0.1.0 catfish-wiki-hub:0.1.0"

echo "=== 前置检查 · 云端 tar 是否本地已备份 ==="
if [ ! -f "$ARM_TAR" ]; then
    echo "!!! $ARM_TAR 不在, 先从 Nextcloud 下 arm64 tar 到 $DELIVERY/ 再跑此 script"
    exit 1
fi
if [ ! -f "$AMD_TAR" ]; then
    echo "!!! $AMD_TAR 不在, 先从 Nextcloud 下 amd64 tar 到 $DELIVERY/ 再跑此 script"
    exit 1
fi
ls -lh "$ARM_TAR" "$AMD_TAR"

echo ""
echo "=== Phase 1 · arm64 完整重打 ==="
echo "--- Step 1.1 · load arm64 image 底子 (覆盖本地 image, 无论啥架构) ---"
docker load -i "$ARM_TAR"

echo "--- Step 1.2 · rebuild identity (arm64) ---"
export DOCKER_DEFAULT_PLATFORM=linux/arm64
docker compose build identity 2>&1

echo "--- Step 1.3 · verify identity 是 arm64 ---"
docker inspect catfish-identity:0.1.0 --format '{{.Architecture}}/{{.Os}}'

echo "--- Step 1.4 · save 完整 arm64 tar ---"
docker save $IMAGES -o "$ARM_TAR"
ls -lh "$ARM_TAR"

echo ""
echo "=== Phase 2 · amd64 完整重打 ==="
echo "--- Step 2.1 · load amd64 image 底子 (覆盖 arm64) ---"
docker load -i "$AMD_TAR"

echo "--- Step 2.2 · rebuild identity (amd64, QEMU emulation, 5-10 min) ---"
export DOCKER_DEFAULT_PLATFORM=linux/amd64
docker compose build identity 2>&1

echo "--- Step 2.3 · verify identity 是 amd64 ---"
docker inspect catfish-identity:0.1.0 --format '{{.Architecture}}/{{.Os}}'

echo "--- Step 2.4 · save 完整 amd64 tar ---"
docker save $IMAGES -o "$AMD_TAR"
ls -lh "$AMD_TAR"

echo ""
echo "=== DONE ==="
ls -lh "$ARM_TAR" "$AMD_TAR"
echo ""
echo "上传两个 tar 到 Nextcloud 覆盖老的, 达华 IT 拿到就是含 CORS fix 的完整包."
