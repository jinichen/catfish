#!/usr/bin/env bash
# 打达华 POC 交付包 —— 双架构, 且**打不出坏包**。
#
# ── 为什么要有这个脚本 ──────────────────────────────────────────────
#
# 7/30 复盘: 手上那个 20260729 的包, 装完连续踩三个坑, 每一个的修复都比包新:
#
#   1. gateway → identity 反代走 127.0.0.1  → 「系统管理」页 502
#      (容器里 127.0.0.1 是 gateway 自己; 修复 0937bb5 · 7/29 19:20)
#   2. gateway → mcp/hub/wiki 上游同样问题  → 同上
#   3. web 镜像里 catfish-logo.svg 是 600   → 顶栏 logo 403 坏图
#      (构建机 umask 077 带进去的; 修复在 central/web/Dockerfile:45)
#
# 三个的共同点: **装完之前一切正常**。容器全 healthy、登录正常、聊天正常,
# 只有点开特定页面才看得见。而打包环节没有任何一步会发现它们。
#
# 更糟的是打包流程本身也在漂:
#   - central/rebuild-full-image-tars.sh 是 7/17 为某次特定修复写的, 写死旧路径
#   - central/DEPLOYMENT-MANUAL.md:123 的 docker save 清单写着 gateway:0.1.0
#   - **而 docker-compose.yml 里是 0.1.1**
#   照文档打包会把旧版 gateway 装进新包, 且全程无报错。
#
# 所以这个脚本的设计原则只有一条: **凡是能在打包时验的, 就在打包时验;
# 验不过就不许打出包。** 不把"大概没问题"交出去。
#
# ── 用法 ────────────────────────────────────────────────────────────
#
#   bash delivery/build-package.sh both      # 两个架构都打 (交付用)
#   bash delivery/build-package.sh amd64
#   bash delivery/build-package.sh arm64
#
# 产出: ~/Downloads/dahua-poc-FULL-<arch>-<日期>.tar.gz
#
# 前提: Docker 在跑; Apple Silicon 上打 amd64 靠 buildx + QEMU, 会慢很多
#       (十几分钟到半小时, 取决于机器), 属正常。

set -uo pipefail

TARGET="${1:-}"
case "$TARGET" in
    amd64|arm64) ARCHES="$TARGET" ;;
    both)        ARCHES="amd64 arm64" ;;
    *)
        echo "❌ 用法: bash delivery/build-package.sh <amd64|arm64|both>"
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
CENTRAL="$REPO/central"
TEMPLATE="$SCRIPT_DIR/dahua-poc"          # 交付目录模板 (配置 / setup.sh / 文档)
COMPOSE="$TEMPLATE/docker-compose.yml"
OUT_DIR="${OUT_DIR:-$HOME/Downloads}"
DATE="$(date +%Y%m%d)"

echo "═══════════════════════════════════════════════════════"
echo "  达华 POC 交付包 · $DATE · 架构: $ARCHES"
echo "═══════════════════════════════════════════════════════"

# ── 0. 前置检查 ─────────────────────────────────────────────────────
if ! docker version >/dev/null 2>&1; then
    echo "❌ docker 不可用 —— 先启动 Docker Desktop / dockerd"
    exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
    echo "❌ 没有 docker buildx —— 跨架构构建必须要它"
    echo "   Docker Desktop 自带; Linux 上装 docker-buildx-plugin"
    exit 1
fi
if [ ! -f "$COMPOSE" ]; then
    echo "❌ 找不到 $COMPOSE"
    exit 1
fi

# ── 1. 从 docker-compose.yml 读服务清单 ─────────────────────────────
#
# **不写死版本号。** 文档里那份清单已经漂成 gateway:0.1.0 而实际是 0.1.1,
# 照着打就会把旧镜像装进新包。compose 是装机时真正用的那份, 以它为准。
#
# 输出两组:
#   BUILD_LIST  "服务名|镜像tag|构建源目录"  —— 要 build 的 catfish 镜像
#   PULL_LIST   "镜像tag"                    —— 第三方镜像 (postgres 等)
echo ""
echo "── 从 docker-compose.yml 解析镜像清单 ──"
BUILD_LIST=""
PULL_LIST=""
cur_svc=""; cur_ctx=""      # set -u 下必须先初始化 (postgres 是第一个服务且没有 context)
while IFS= read -r line; do
    case "$line" in
        SVC=*)   cur_svc="${line#SVC=}"; cur_ctx="" ;;
        CTX=*)   cur_ctx="$(basename "${line#CTX=}")" ;;
        IMG=*)   cur_img="${line#IMG=}"
                 # 有 context 且 central 下确有该源码目录 → 自己 build; 否则 → 拉
                 if [ -n "$cur_ctx" ] && [ -d "$CENTRAL/$cur_ctx" ]; then
                     BUILD_LIST="$BUILD_LIST$cur_svc|$cur_img|$cur_ctx"$'\n'
                 else
                     PULL_LIST="$PULL_LIST$cur_img"$'\n'
                 fi
                 cur_ctx="" ;;
    esac
done < <(awk '
    /^  [a-z][a-z0-9-]*:$/ { svc=$1; sub(/:$/,"",svc); print "SVC=" svc; ctx=""; next }
    /^      context:/      { print "CTX=" $2; next }
    /^    image:/          { print "IMG=" $2; next }
' "$COMPOSE")

# awk 是先看到 context 再看到 image (compose 里 build 段在 image 之前),
# 所以上面的状态机靠 cur_ctx 判断"这个 image 是自己 build 的还是拉的"。
BUILD_COUNT=$(printf '%s' "$BUILD_LIST" | grep -c . || true)
PULL_COUNT=$(printf '%s' "$PULL_LIST" | grep -c . || true)
if [ "$BUILD_COUNT" -eq 0 ]; then
    echo "❌ 从 compose 里没解析出任何要 build 的镜像 —— 解析逻辑跟 compose 格式对不上了"
    echo "   检查 $COMPOSE 的缩进是否变过"
    exit 1
fi
echo "  要构建 ($BUILD_COUNT):"
printf '%s' "$BUILD_LIST" | while IFS='|' read -r svc img ctx; do
    [ -n "$svc" ] && printf "    %-14s %-28s ← central/%s\n" "$svc" "$img" "$ctx"
done
echo "  要拉取 ($PULL_COUNT):"
printf '%s' "$PULL_LIST" | while read -r img; do
    [ -n "$img" ] && echo "    $img"
done

# 构建源必须都在
MISSING=""
printf '%s' "$BUILD_LIST" | while IFS='|' read -r svc img ctx; do
    [ -n "$ctx" ] && [ ! -f "$CENTRAL/$ctx/Dockerfile" ] && echo "$ctx"
done > /tmp/.pkg-missing-$$
MISSING="$(cat /tmp/.pkg-missing-$$; rm -f /tmp/.pkg-missing-$$)"
if [ -n "$MISSING" ]; then
    echo "❌ 这些构建源缺 Dockerfile: $MISSING"
    exit 1
fi

# ── 2. 交付目录内容的静态检查 (跟架构无关, 只做一次) ────────────────
#
# 这几项是 7/30 现场踩出来的。它们都是**配置文件**里的值, 装机后要到点开
# 特定页面才看得见 —— 所以在这里钉死, 错了就不许打包。
echo ""
echo "── 交付配置检查 ──"
CFG_FAIL=0

# (a) gateway → identity 反代。bundled stack 的默认值是 Docker DNS, 但允许
#     现场通过 CATFISH_IDENTITY_URL 覆盖成外部 Identity 地址。
if grep -qE '^[[:space:]]*CATFISH_IDENTITY_URL:[[:space:]]*(http://identity:8998|\$\{CATFISH_IDENTITY_URL:-http://identity:8998\})[[:space:]]*$' "$COMPOSE"; then
    echo "  ✓ CATFISH_IDENTITY_URL → runtime configurable (default identity:8998)"
else
    echo "  ❌ docker-compose.yml 缺可运行时覆盖的 CATFISH_IDENTITY_URL"
    echo "     (default 应为 http://identity:8998; 外部 Identity 可在运行时覆盖)"
    CFG_FAIL=1
fi

# (a2) setup.sh 的 sed 只能改有效配置行。若模板把这些行写成注释,
# 装机脚本会显示"生成"但 compose 实际仍回退到 host.docker.internal/127.0.0.1.
for key in CATFISH_OIDC_ISSUER CATFISH_IDENTITY_ISSUER \
           CATFISH_IDENTITY_CORS_ORIGINS CATFISH_ENABLE_HTTPS \
           CATFISH_HTTPS_PORT; do
    if grep -qE "^${key}=" "$TEMPLATE/.env.example"; then
        echo "  ✓ .env.example → $key 有效字段"
    else
        echo "  ❌ .env.example 缺有效字段: $key"
        echo "     (不能只有注释, 否则 setup.sh 无法写入实际部署配置)"
        CFG_FAIL=1
    fi
done

# (b) mcp / hub / wiki 三个上游。只能从 yaml 读, 且**必须是挂载进去的那份** ——
#     config-overlay/ 没有任何东西读它 (7/30 查实), 改错文件等于没改。
GW_YAML="$TEMPLATE/llm-gateway/config/models.yaml"
if [ ! -f "$GW_YAML" ]; then
    echo "  ❌ 找不到 $GW_YAML"
    CFG_FAIL=1
else
    for pair in "mcp_registry|mcp-registry:8996" "skills_hub|skills-hub:8997" "wiki_hub|wiki-hub:8994"; do
        key="${pair%%|*}"; want="${pair#*|}"
        got=$(awk -v k="^$key:" '$0 ~ k {f=1; next} f && /upstream_url:/ {print $2; exit} f && /^[a-z]/ {exit}' "$GW_YAML")
        if [ "$got" = "http://$want" ]; then
            echo "  ✓ $key → $want"
        else
            echo "  ❌ $key 的 upstream_url = ${got:-<空>} · 应为 http://$want"
            echo "     (写 127.0.0.1 的话容器里指向 gateway 自己 → 502)"
            CFG_FAIL=1
        fi
    done
fi

if [ "$CFG_FAIL" = "1" ]; then
    echo ""
    echo "❌ 交付配置有问题 —— 不打包。修完再跑。"
    exit 1
fi

# ── 3. 逐架构构建 ───────────────────────────────────────────────────
#
# ⚠ 两个架构**必须串行做完整流程**, 不能先 build 两个架构再一起 save。
#   同一个 tag 在本地只能存一份, 后 build 的会覆盖先 build 的 ——
#   这跟 7/30 companion 打包时 chromium 装错架构是同一类错误 (共享状态 +
#   看目录名不看内容)。所以每个架构: build → 验架构 → save → 打包, 走完再下一个。
for ARCH in $ARCHES; do
    echo ""
    echo "═══ $ARCH ═══"
    PLATFORM="linux/$ARCH"

    echo "→ 构建 $BUILD_COUNT 个镜像 ($PLATFORM)"
    printf '%s' "$BUILD_LIST" | while IFS='|' read -r svc img ctx; do
        [ -z "$svc" ] && continue
        echo "  · $img"
        if ! docker buildx build --platform "$PLATFORM" --load \
             -t "$img" "$CENTRAL/$ctx" >/tmp/.pkg-build-$$.log 2>&1; then
            echo "    ❌ 构建失败, 末 20 行:"
            tail -20 /tmp/.pkg-build-$$.log | sed 's/^/      /'
            rm -f /tmp/.pkg-build-$$.log
            exit 1
        fi
    done || exit 1
    rm -f /tmp/.pkg-build-$$.log

    echo "→ 拉取第三方镜像 ($PLATFORM)"
    printf '%s' "$PULL_LIST" | while read -r img; do
        [ -z "$img" ] && continue
        echo "  · $img"
        docker pull --platform "$PLATFORM" -q "$img" >/dev/null || {
            echo "    ❌ 拉取失败: $img"; exit 1; }
    done || exit 1

    # ── 验架构 ──────────────────────────────────────────────────────
    #
    # 只信镜像里实际记的 Architecture, 不信"我传了 --platform 所以应该对"。
    # 7/30 那次 chromium 就是"命令看着对、产物是另一个架构", 而且是因为
    # 工具在用另一套规则判断平台。这里直接问镜像自己。
    echo "→ 核对镜像架构"
    ALL_IMGS="$(printf '%s%s' "$BUILD_LIST" "$PULL_LIST" | awk -F'|' 'NF{print (NF>1?$2:$1)}')"
    ARCH_FAIL=0
    for img in $ALL_IMGS; do
        actual="$(docker image inspect "$img" --format '{{.Architecture}}' 2>/dev/null || echo '<读不出>')"
        if [ "$actual" != "$ARCH" ]; then
            echo "  ❌ $img → $actual (期望 $ARCH)"
            ARCH_FAIL=1
        fi
    done
    if [ "$ARCH_FAIL" = "1" ]; then
        echo ""
        echo "❌ 有镜像架构不符 —— 这个包不能发。"
        echo "   多半是 buildx 没真正按 --platform 走, 或本地同 tag 的旧镜像没被覆盖。"
        exit 1
    fi
    echo "  ✓ 全部 $ARCH"

    # ── 验镜像内部 ──────────────────────────────────────────────────
    #
    # logo 权限: dist/ 里 assets/*.js 是容器内 build 出来的 (644), 而
    # catfish-logo.svg 来自 public/, COPY 时**保留宿主机权限位** —— 构建机
    # umask 077 就是 600, nginx 非 root 读不了 → 403 坏图。
    # git 只记 644/755, 所以全新 clone 复现不了, 只有特定构建机会出。
    # central/web/Dockerfile:45 的 `chmod -R a+rX` 是修复, 这里验它真生效了。
    echo "→ 核对镜像内部"
    WEB_IMG="$(printf '%s' "$BUILD_LIST" | awk -F'|' '$1=="web"{print $2}')"
    if [ -n "$WEB_IMG" ]; then
        perm="$(docker run --rm --entrypoint sh "$WEB_IMG" -c \
                'stat -c "%a" /usr/share/nginx/html/catfish-logo.svg 2>/dev/null' 2>/dev/null || echo "")"
        if [ -z "$perm" ]; then
            echo "  ❌ web 镜像里没有 catfish-logo.svg —— 顶栏会是坏图"
            exit 1
        fi
        # 末位 (other) 必须有读权限
        case "$perm" in
            *4|*5|*6|*7) echo "  ✓ catfish-logo.svg 权限 $perm (nginx 读得到)" ;;
            *)
                echo "  ❌ catfish-logo.svg 权限 $perm —— nginx 非 root 用户读不了 → 403 坏图"
                echo "     central/web/Dockerfile 里应有: RUN chmod -R a+rX /usr/share/nginx/html"
                exit 1
                ;;
        esac
    fi

    # ── save + 组装 ────────────────────────────────────────────────
    STAGE="/tmp/dahua-pkg-$ARCH-$$"
    rm -rf "$STAGE"
    mkdir -p "$STAGE/delivery"
    # 交付目录 = 模板的副本, 但不带模板里可能残留的旧镜像
    cp -R "$TEMPLATE" "$STAGE/delivery/dahua-poc"
    rm -rf "$STAGE/delivery/dahua-poc/images"
    mkdir -p "$STAGE/delivery/dahua-poc/images"
    # 装机时生成的东西不该跟着包走 (会覆盖客户现场的值)
    rm -f "$STAGE/delivery/dahua-poc/.env" \
          "$STAGE/delivery/dahua-poc/.env.bak."* \
          "$STAGE/delivery/dahua-poc/users.yaml" \
          "$STAGE/delivery/dahua-poc/clients.yaml"
    rm -rf "$STAGE/delivery/dahua-poc/certs"
    find "$STAGE" -name ".DS_Store" -delete 2>/dev/null || true

    IMG_TAR="$STAGE/delivery/dahua-poc/images/dahua-poc-central-$ARCH-$DATE.tar.gz"
    echo "→ docker save → $(basename "$IMG_TAR")"
    # shellcheck disable=SC2086
    docker save $ALL_IMGS | gzip -1 > "$IMG_TAR" || { echo "❌ save 失败"; exit 1; }
    echo "  ✓ $(du -h "$IMG_TAR" | cut -f1)"

    OUT="$OUT_DIR/dahua-poc-FULL-$ARCH-$DATE.tar.gz"
    mkdir -p "$OUT_DIR"
    echo "→ 打包 → $OUT"
    tar czf "$OUT" -C "$STAGE" delivery || { echo "❌ tar 失败"; exit 1; }

    # ── 验产物 ──────────────────────────────────────────────────────
    #
    # 按 INSTALL.md 的解压步骤反推: 解开后必须是 delivery/dahua-poc/,
    # 客户才能照文档 `cd delivery/dahua-poc/`。结构错了现场会卡在第二步。
    for must in "delivery/dahua-poc/setup.sh" \
                "delivery/dahua-poc/docker-compose.yml" \
                "delivery/dahua-poc/llm-gateway/config/models.yaml" \
                "delivery/dahua-poc/images/dahua-poc-central-$ARCH-$DATE.tar.gz"; do
        if ! tar tzf "$OUT" | grep -qx "$must"; then
            echo "  ❌ 包里缺 $must"
            exit 1
        fi
    done
    echo "  ✓ 结构核对通过 · $(du -h "$OUT" | cut -f1)"
    rm -rf "$STAGE"
done

echo ""
echo "═══════════════════════════════════════════════════════"
echo "✅ 完成"
ls -lh "$OUT_DIR"/dahua-poc-FULL-*-"$DATE".tar.gz 2>/dev/null | sed 's/^/   /'
echo ""
echo "客户侧装机 (照 INSTALL.md):"
echo "    tar xzf dahua-poc-FULL-<arch>-$DATE.tar.gz"
echo "    cd delivery/dahua-poc/"
echo "    SERVER_IP=<服务器内网IP> ENABLE_HTTPS=1 bash setup.sh"
echo ""
echo "⚠ 给包之前先确认对方机器架构 —— x86_64 服务器用 amd64,"
echo "  鲲鹏/飞腾/Graviton/Apple Silicon 用 arm64。选错装不起来。"
