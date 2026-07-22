#!/usr/bin/env bash
# 达华 POC 完整 image build · 双架构 (linux/amd64 + linux/arm64) · docker save tar.
#
# 前置:
#   - Docker Desktop (Mac) 或 docker-ce + buildx (Linux)
#   - QEMU emulation 装 (M1/M2 Mac 默认含; 若缺跑
#     `docker run --privileged --rm tonistiigi/binfmt --install all`)
#   - buildx builder create 好 · `docker buildx use catfishbuilder`
#
# 输出:
#   ~/person_task/catfish/delivery/dahua-poc/images/
#     catfish-{identity,gateway,skills-hub,web,mcp-registry,wiki-hub}-<version>-<arch>.tar
#     postgres-16-alpine-<arch>.tar
#
# 达华 IT 分发 install:
#   uname -m       # → x86_64 = amd64 / aarch64 = arm64
#   docker load < catfish-gateway-0.1.1-<arch>.tar
#   ... 6 个 catfish + 1 个 postgres
#   docker compose up -d
#
# BL-DAHUA-POC-IMAGE (7/18 鸿波): 0.1.1 加 orjson (Task #68) · docker-compose.yml
# default 双 audience (Task #66) · pyproject.toml orjson>=3.10 依赖.

set -euo pipefail
cd "$(dirname "$0")/.."   # 到 catfish/central/

DELIVERY_ROOT="${HOME}/person_task/catfish/delivery/dahua-poc"
IMG_DIR="${DELIVERY_ROOT}/images"
mkdir -p "$IMG_DIR"

# 双架构
PLATFORMS=(amd64 arm64)

# ── build + save 一个 catfish service ────────────────────────────
# 参数: context · image_name (无 tag) · tag_version
build_and_save() {
    local ctx="$1" name="$2" tag="$3"

    for arch in "${PLATFORMS[@]}"; do
        echo ""
        echo "════════════════════════════════════════════════════"
        echo "=== $name:$tag  ·  linux/$arch  ==="
        echo "════════════════════════════════════════════════════"

        docker buildx build \
            --platform "linux/$arch" \
            -t "$name:$tag-$arch" \
            --load \
            "$ctx" 2>&1 | tail -30

        local out="$IMG_DIR/${name}-${tag}-${arch}.tar"
        echo "→ docker save: $out"
        docker save "$name:$tag-$arch" -o "$out"

        # 清 image 免撑爆磁盘 (下一个 arch 需重跑 · buildx cache 保加速)
        docker image rm "$name:$tag-$arch" >/dev/null 2>&1 || true

        echo "✓ $(du -h "$out" | cut -f1)  $out"
    done
}

# ── catfish 6 service ──────────────────────────────────────────
build_and_save ./identity-server   catfish-identity      0.1.0
build_and_save ./llm-gateway       catfish-gateway       0.1.1
build_and_save ./skills-hub        catfish-skills-hub    0.1.0
build_and_save ./web               catfish-web           0.1.0
build_and_save ./mcp-registry      catfish-mcp-registry  0.1.0
build_and_save ./wiki-hub          catfish-wiki-hub      0.1.0

# ── postgres 官方 image · 双架构 pull + save ──────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "=== postgres:16-alpine  ·  双架构 ==="
echo "════════════════════════════════════════════════════"
for arch in "${PLATFORMS[@]}"; do
    docker pull --platform "linux/$arch" postgres:16-alpine
    # docker pull 覆盖同 tag · 需先 tag 保留:
    docker tag postgres:16-alpine "postgres:16-alpine-${arch}"
    out="$IMG_DIR/postgres-16-alpine-${arch}.tar"
    docker save "postgres:16-alpine-${arch}" -o "$out"
    docker image rm "postgres:16-alpine-${arch}" >/dev/null 2>&1 || true
    echo "✓ $(du -h "$out" | cut -f1)  $out"
done

# ── 收尾 · 复制 docker-compose.yml + .env.example ─────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "=== 打包 config 文件 ==="
echo "════════════════════════════════════════════════════"

cp docker-compose.yml "$DELIVERY_ROOT/docker-compose.yml"
echo "✓ $DELIVERY_ROOT/docker-compose.yml"

if [[ -f .env.example ]]; then
    cp .env.example "$DELIVERY_ROOT/.env.example"
    echo "✓ $DELIVERY_ROOT/.env.example"
else
    # 生成一个 sample · 达华 IT 填 secrets
    cat > "$DELIVERY_ROOT/.env.example" <<'EOF'
# 达华 POC · Catfish Central 服务器 config · IT 填写以下 secret 后 mv 成 .env
# 敏感字段 · 客户 IT 自己生成 · 不从 Catfish 团队分发.

# PG · Postgres 密码 (随机字符串 · 至少 24 字符)
PG_PASSWORD=

# JWT signing key · openssl rand -hex 32 生成
JWT_SIGNING_KEY=

# LLM provider keys (客户 IT 自己申请 · 用不到留空)
DASHSCOPE_API_KEY=
GEMINI_API_KEY=

# 内网私有 LLM (若客户有 · 一般不填 · 用公网 provider)
INTERNAL_LLM_BASE_QWEN_MAIN=
INTERNAL_LLM_BASE_QWEN_VISION=
INTERNAL_LLM_BASE_BGE_M3=
INTERNAL_LLM_KEY=

# OIDC · issuer/audience default 已在 docker-compose.yml 覆 (Task #66)
# CATFISH_OIDC_ISSUER=http://<server-ip>:8998
# CATFISH_OIDC_AUDIENCE=catfish-companion,catfish-gateway
# CATFISH_ENV=prod
EOF
    echo "✓ $DELIVERY_ROOT/.env.example (生成 sample)"
fi

echo ""
echo "════════════════════════════════════════════════════"
echo "达华 POC image bundle 完成:"
echo "════════════════════════════════════════════════════"
ls -lh "$IMG_DIR"/*.tar
echo ""
echo "总 size:"
du -sh "$IMG_DIR"
echo ""
echo "分发 tar (合并成一个包给 IT):"
echo "  cd $DELIVERY_ROOT"
echo "  tar czf ~/dahua-poc-central-\$(date +%Y%m%d).tar.gz images docker-compose.yml .env.example"
