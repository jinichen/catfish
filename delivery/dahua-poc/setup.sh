#!/usr/bin/env bash
# 达华 POC · Catfish 中央服务一键装机脚本
#
# 用途:
#   客户 IT 拿到 delivery tar 后 · 一条命令完成:
#     1. 探测 server IP (或指定)
#     2. 从 .env.example 生成 .env · 自动替换 <server-ip>
#     3. 装 image tar (若未装)
#     4. 生成自签 cert (若走 HTTPS · 可选)
#     5. docker compose up · verify
#
# 用法:
#   # 自动探测 IP · 走 HTTP (dev/test 用)
#   bash setup.sh
#
#   # 显式指定 IP + 自签 cert (推荐 · 生产 POC 走 HTTPS)
#   SERVER_IP=192.168.100.50 ENABLE_HTTPS=1 bash setup.sh
#
#   # 只重生成 .env (不动 docker · 已装好想改 IP 时用)
#   REGEN_ENV_ONLY=1 SERVER_IP=192.168.100.50 bash setup.sh
#
# 前置:
#   - Ubuntu 22.04+ / CentOS 8+ · x86_64
#   - Docker 24+ + docker compose plugin
#   - 4 vCPU / 8GB RAM / 100GB disk (推荐)
#   - 内网可访问 (不需公网)
#
# 装完验证 (脚本尾自动跑):
#   - identity discovery 返新 issuer
#   - gateway /healthz 200
#   - web /config.js 含本机 IP
#   - identity 两 worker 的签名 kid 一致
#   - HTTPS 模式额外验 web:443 的 OIDC 反代
#
# ── HTTP vs HTTPS 怎么选 ──────────────────────────
#
#   裸 IP + HTTP  → 员工浏览器会**卡在"加载中…"且不报错**.
#                   前端 oidc-client 走 PKCE 要 crypto.subtle, 浏览器只在
#                   安全上下文 (https 或 localhost/127.0.0.1) 才提供它.
#                   绕法是每台机器加 chrome://flags 的
#                   unsafely-treat-insecure-origin-as-secure —— Safari 没这个 flag.
#
#   ENABLE_HTTPS=1 → web 容器额外监听 443 (自签证书, SAN 含服务器 IP).
#                   员工首次访问点一次"继续前往"即可; 规模化建议 IT 把
#                   certs/cert.pem 推到员工机器信任库, 一次导入永久免警告.
#
#   注 · 443 由 **web 容器自己扛**, 不再有独立的 nginx 服务.

set -euo pipefail

# ── 参数 · env 可 override ──────────────────────────────
SERVER_IP="${SERVER_IP:-}"
ENABLE_HTTPS="${ENABLE_HTTPS:-0}"
REGEN_ENV_ONLY="${REGEN_ENV_ONLY:-0}"
IMAGE_TAR="${IMAGE_TAR:-}"   # 若未装 image · 指到 image tar 路径

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── sed -i 的 GNU / BSD 差异 ────────────────────────────────
#
# BSD sed (macOS) 的 -i **必须**带备份后缀参数, GNU sed 不带。于是 GNU 写法
#     sed -i -e "s|a|b|" f
# 在 mac 上被解析成: -i 的后缀 = "-e", 脚本 = "s|a|b|", 输入文件 = "-e" 和 "f"
# → `sed: -e: No such file or directory`  (8/4 鸿波在 mac 上试跑踩到)
#
# 脚本头写的是 Ubuntu/CentOS, 客户现场确实是 Linux —— 但交付前在本机试跑是常态,
# 在这里绊一跤纯属浪费。而且 set -e 会让脚本停在半路: .env 已经被 .env.example
# 覆盖、真密钥只剩在 .env.bak.* 里, 现场看到的是"装到一半没了", 很吓人。
if sed --version >/dev/null 2>&1; then
    sed_i() { sed -i "$@"; }        # GNU
else
    sed_i() { sed -i '' "$@"; }     # BSD / macOS
fi

echo "═══════════════════════════════════════════════════════"
echo "  Catfish 中央服务一键装机 · 达华 POC"
echo "═══════════════════════════════════════════════════════"

# ── 1. 探测 / 确认 server IP ─────────────────────────────
if [ -z "$SERVER_IP" ]; then
    # 探测: 取第一条非 loopback / 非 docker 的 IPv4
    SERVER_IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | \
        grep -vE '^(127\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[0-1]\.|169\.254\.)' | \
        head -1)
    if [ -z "$SERVER_IP" ]; then
        echo "❌ 无法自动探测 server IP · 请显式指定:"
        echo "   SERVER_IP=192.168.x.x bash setup.sh"
        exit 1
    fi
    echo "→ 自动探测 server IP: $SERVER_IP"
    read -rp "  确认? (y/n · 回车 = y): " confirm
    if [ "${confirm:-y}" != "y" ] && [ "${confirm:-y}" != "Y" ]; then
        echo "  请显式指定: SERVER_IP=192.168.x.x bash setup.sh"
        exit 1
    fi
else
    echo "→ 使用指定 IP: $SERVER_IP"
fi

# ── 2. 从 .env.example 生成 .env ──────────────────────────
if [ ! -f .env.example ]; then
    echo "❌ 找不到 .env.example · 请确认在 delivery/dahua-poc/ 目录跑"
    exit 1
fi

# HTTP vs HTTPS
# 宿主机 443 常被已有 nginx/apache 占用, 允许换端口.
# 换了端口, issuer / CORS 三项必须带同一端口, 否则 OIDC issuer 对不上验签失败.
HTTPS_PORT="${CATFISH_HTTPS_PORT:-443}"
if [ "$ENABLE_HTTPS" = "1" ]; then
    if [ "$HTTPS_PORT" = "443" ]; then
        ISSUER_URL="https://$SERVER_IP"
        WEB_URL="https://$SERVER_IP"
    else
        ISSUER_URL="https://$SERVER_IP:$HTTPS_PORT"
        WEB_URL="https://$SERVER_IP:$HTTPS_PORT"
    fi
else
    ISSUER_URL="http://$SERVER_IP:8998"
    WEB_URL="http://$SERVER_IP:5173"
fi

# ── 保留既有 .env 的持久化 secret ────
#
# 早期版本的问题: 下面 `cp .env.example .env` 无条件覆盖 → PG_PASSWORD 变空 →
# 第 101 段判定"空"生成**新**随机密码. 但 pgdata 是**命名卷**,
# `docker compose up` 不会删它; 而 postgres 的 POSTGRES_PASSWORD **只在
# 数据目录为空(首次 initdb)时生效**, 已有库照旧认**旧**密码 →
# identity / gateway / skills-hub / wiki-hub 四个服务全部连库认证失败.
#
# 实测: 第二次跑 setup.sh 必炸, 且报错在容器日志里,
# 装机脚本本身一路绿, IT 完全看不出是密码被换了.
#
# 修: cp 之前把旧值抽出来, 覆盖后写回 (见第 101 段).
OLD_PG_PW=""
OLD_JWT_KEY=""
# CATFISH_SECRET_KEY 也必须在 cp 之前捞出来 —— 8/4 鸿波跑装机时查出来的:
#
#   8/1 加这个 key 时, 保护逻辑写在第 235 段 ("有值就不动"), 但那段是在
#   `cp .env.example .env` **之后**读 .env 的。而 .env.example:19 是
#   `CATFISH_SECRET_KEY=` (空) —— 于是每次重跑必然命中"空"分支, 生成**新**主密钥。
#   第 255 行那句"已有值 · 保持不变 (改了会让存库的 API key 全解不开)"
#   **重跑时永远走不到**。
#
#   后果比 PG_PASSWORD 更狠: 密码错了服务连不上库, 至少会炸给你看; 主密钥换了
#   服务照常起, 只是界面上所有已存的供应商 API key 全部解不开, 而且**旧密文
#   无法恢复** —— 除非有人留着 .env.bak。
#
#   跟第 108 段记录的 PG_PASSWORD 事故是同一个形状: 覆盖在先、保护在后。
#   那次给 PG_PASSWORD 和 JWT_SIGNING_KEY 接上了捞取, 加第三个 key 时漏了。
OLD_SECRET_KEY=""
if [ -f .env ]; then
    echo "→ .env 已存在 · 备份到 .env.bak.$(date +%s)"
    cp .env ".env.bak.$(date +%s)"
    OLD_PG_PW=$(grep -E "^PG_PASSWORD=" .env | head -1 | cut -d= -f2- || true)
    OLD_JWT_KEY=$(grep -E "^JWT_SIGNING_KEY=" .env | head -1 | cut -d= -f2- || true)
    OLD_SECRET_KEY=$(grep -E "^CATFISH_SECRET_KEY=" .env | head -1 | cut -d= -f2- || true)
else
    # ── .env 丢了但备份还在 → 自动救回 ──
    #
    # 场景: 装机目录被清过 / 重新解包到别处 / 误删 .env, 但 pgdata 命名卷
    # 还在. postgres 的密码只在**首次建库**时写入, 新生成的随机密码连不上
    # 已有的库, 四个服务全挂.
    #
    # 之前只是 fail-loud 拦住, 然后让 IT 自己去 grep .env.bak.* —— 但备份
    # 就在旁边, 脚本完全有能力自己捞回来. 让人手动执行一条我们本可以自动
    # 完成的命令, 是把自己的活推给现场.
    #
    # 只在**唯一**一个候选值时自动沿用: 多个不同的旧密码说明历史复杂,
    # 猜错会静默连错库, 那种情况必须人来判断.
    LATEST_BAK=$(ls -t .env.bak.* 2>/dev/null | head -1 || true)
    if [ -n "$LATEST_BAK" ]; then
        CAND=$(grep -h '^PG_PASSWORD=' .env.bak.* 2>/dev/null \
               | cut -d= -f2- | grep -v '^$' | sort -u || true)
        CAND_N=$(echo "$CAND" | grep -c . || true)
        if [ "$CAND_N" = "1" ]; then
            OLD_PG_PW="$CAND"
            OLD_JWT_KEY=$(grep -h '^JWT_SIGNING_KEY=' "$LATEST_BAK" 2>/dev/null \
                          | head -1 | cut -d= -f2- || true)
            OLD_SECRET_KEY=$(grep -h '^CATFISH_SECRET_KEY=' "$LATEST_BAK" 2>/dev/null \
                             | head -1 | cut -d= -f2- || true)
            echo "→ .env 不存在, 但从 $LATEST_BAK 找回了 PG_PASSWORD / JWT_SIGNING_KEY / CATFISH_SECRET_KEY"
            echo "  (装机目录被清过? 密钥必须跟 pgdata 卷里的库一致, 否则四个服务全连不上)"
        elif [ "$CAND_N" -gt 1 ]; then
            echo "→ .env 不存在 · 备份里有 $CAND_N 个不同的 PG_PASSWORD · 不自动猜"
            echo "  (猜错会连错库且报错只在容器日志里. 手动确认后写进 .env:)"
            echo "$CAND" | sed 's/^/       PG_PASSWORD=/'
        fi
    fi
fi

cp .env.example .env
sed_i \
    -e "s|<server-ip>|$SERVER_IP|g" \
    -e "s|^CATFISH_OIDC_ISSUER=.*|CATFISH_OIDC_ISSUER=$ISSUER_URL|" \
    -e "s|^CATFISH_IDENTITY_ISSUER=.*|CATFISH_IDENTITY_ISSUER=$ISSUER_URL|" \
    -e "s|^CATFISH_IDENTITY_CORS_ORIGINS=.*|CATFISH_IDENTITY_CORS_ORIGINS=$WEB_URL|" \
    .env

# HTTPS 开关写进 .env 而不是只 export ——
# 只 export 的话客户之后手动 `docker compose up` 会退回默认 0, HTTPS 悄悄关掉.
sed_i "s|^CATFISH_ENABLE_HTTPS=.*|CATFISH_ENABLE_HTTPS=$ENABLE_HTTPS|" .env
sed_i "s|^CATFISH_HTTPS_PORT=.*|CATFISH_HTTPS_PORT=$HTTPS_PORT|" .env

echo "→ 生成 .env · 关键字段:"
grep -E "^CATFISH_(OIDC|IDENTITY|ENABLE)_" .env | sed 's/^/    /'

# ── 先写回上一次装机的 secret ──────────────
if [ -n "$OLD_PG_PW" ]; then
    sed_i "s|^PG_PASSWORD=.*|PG_PASSWORD=$OLD_PG_PW|" .env
    echo "→ PG_PASSWORD 沿用既有值 (跨装机保留 · 必须与 pgdata 卷里的库一致)"
fi
if [ -n "$OLD_JWT_KEY" ]; then
    sed_i "s|^JWT_SIGNING_KEY=.*|JWT_SIGNING_KEY=$OLD_JWT_KEY|" .env
    echo "→ JWT_SIGNING_KEY 沿用既有值 (换了会让已签发的 token 全失效)"
fi

# PG_PASSWORD 若仍空 · 生成随机 (首次装机路径)
if grep -qE "^PG_PASSWORD=$|^PG_PASSWORD= *$" .env; then
    # fail-loud: 库卷还在却没有可沿用的密码 → 生成新的必然连不上, 提前拦住.
    # 不拦的话脚本会一路绿灯装完, 报错只出现在容器日志里, IT 查不到根因.
    EXISTING_VOL=$(docker volume ls -q 2>/dev/null | grep -E '_pgdata$' | head -1 || true)
    if [ -n "$EXISTING_VOL" ]; then
        echo ""
        echo "❌ 检测到既有数据库卷: $EXISTING_VOL"
        echo "   但 .env 里没有可沿用的 PG_PASSWORD."
        echo ""
        echo "   postgres 的密码只在**首次建库**时写入, 现在生成新密码"
        echo "   连不上已有的库 (identity/gateway/skills-hub/wiki-hub 全挂)."
        echo ""
        echo "   二选一:"
        echo "     A. 保数据 — 从备份找回旧密码, 写进 .env 后重跑本脚本:"
        echo "          grep -h '^PG_PASSWORD=' .env.bak.* | sort -u"
        echo "     B. 清库重来 — 删卷后重跑 (⚠ 库内数据全丢):"
        echo "          docker compose down -v && bash setup.sh"
        echo ""
        exit 1
    fi
    RAND_PW=$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    sed_i "s|^PG_PASSWORD=.*|PG_PASSWORD=$RAND_PW|" .env
    echo "→ PG_PASSWORD 空 · 已生成随机: $RAND_PW  ← ★ 记好 · 数据库唯一密码"
fi

# JWT_SIGNING_KEY 若仍空 · 生成随机 (首次装机路径)
if grep -qE "^JWT_SIGNING_KEY=$" .env; then
    JWT_KEY=$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | base64 | tr -d '/+=' | head -c 64)
    sed_i "s|^JWT_SIGNING_KEY=.*|JWT_SIGNING_KEY=$JWT_KEY|" .env
    echo "→ JWT_SIGNING_KEY 空 · 已生成随机 (64 字符)"
fi

# ── CATFISH_SECRET_KEY (8/1) · 供应商 API key 的加密主密钥 ──────────
#
# ⚠⚠ 这一项跟 PG_PASSWORD / JWT_SIGNING_KEY 的最大区别: **绝对不能重新生成**。
#    换掉它 = 所有存在数据库里的供应商 API key 全部解不开, 只能逐个去
#    dashscope / deepseek / gemini 后台重新申请再重填。所以下面只在"确实
#    还没有"时生成, 已有值一个字节都不碰。
#
# 值的格式 = Fernet key = 32 字节随机的 url-safe base64。用 openssl 而不是
# python cryptography: 客户服务器上不一定装了那个包 (它在容器里), 而 openssl
# 装了 docker 的机器上都有。tr 把标准 base64 的 +/ 换成 url-safe 的 -_。
#
# 老 .env 里可能压根没有这一行 (8/1 之前的交付包), 所以要区分"没这行"和
# "有这行但为空", 两种都要补。
if ! grep -qE "^CATFISH_SECRET_KEY=" .env; then
    echo "" >> .env
    echo "CATFISH_SECRET_KEY=" >> .env
fi
# 先写回上一次装机的主密钥 —— 必须在下面"空则生成"之前。
# 少了这一步, 下面那个判空必然成立 (cp 刚把它清成 .env.example 的空值),
# 于是每次重跑都换一把新钥匙, 而"已有值·保持不变"那个 else 分支永远走不到。
if [ -n "$OLD_SECRET_KEY" ]; then
    sed_i "s|^CATFISH_SECRET_KEY=.*|CATFISH_SECRET_KEY=$OLD_SECRET_KEY|" .env
fi
if grep -qE "^CATFISH_SECRET_KEY=$|^CATFISH_SECRET_KEY= *$" .env; then
    SECRET_KEY=$(openssl rand -base64 32 2>/dev/null | tr '+/' '-_')
    if [ -z "$SECRET_KEY" ]; then
        echo "❌ 生成 CATFISH_SECRET_KEY 失败 · 这台机器上没有 openssl?"
        echo "   手工生成一个 44 字符的 url-safe base64 填进 .env:"
        echo "     head -c 32 /dev/urandom | base64 | tr '+/' '-_'"
        exit 1
    fi
    sed_i "s|^CATFISH_SECRET_KEY=.*|CATFISH_SECRET_KEY=$SECRET_KEY|" .env
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "→ CATFISH_SECRET_KEY 已生成:"
    echo "     $SECRET_KEY"
    echo ""
    echo "  ★★ 现在就把它存进公司密码管理器 ★★"
    echo "     它丢了的话, 之后在界面上填的所有供应商 API key 都解不开 ——"
    echo "     只能逐个去各家后台重新申请。代码兜不住。"
    echo "════════════════════════════════════════════════════════════"
    echo ""
else
    echo "→ CATFISH_SECRET_KEY 已有值 · 保持不变 (改了会让存库的 API key 全解不开)"
fi

# ── 若只重生 .env · 到此为止 ───────────────────────────
if [ "$REGEN_ENV_ONLY" = "1" ]; then
    echo ""
    echo "✓ .env 已重生 · REGEN_ENV_ONLY=1 模式退出"
    echo "  下一步 · 重启涉及服务:"
    echo "    docker compose up -d --force-recreate identity gateway web"
    exit 0
fi

# ── 2.5 · 生成 users.yaml 含 admin ──────
# delivery tar 里只带 users.yaml.example (真 hash 不进 tar · 安全 · 军规).
# 装机时用 docker load 好的 identity image 里的 bcrypt 生成真 hash · 写 users.yaml.
# 首次 identity 启动 · seed_pg_from_yaml_if_empty() 读 yaml · 灌 admin 进 PG.
# 后续 identity 从 PG 读 · yaml 忽略. 员工首次登进后立即改密.
IDENTITY_CFG="$SCRIPT_DIR/identity-server/config"
mkdir -p "$IDENTITY_CFG"
ADMIN_PW="${ADMIN_PASSWORD:-catfish_2026}"
ADMIN_HASH=""   # 显式初始化 · 防 set -u 未定义变量挂

if [ ! -f "$IDENTITY_CFG/users.yaml" ]; then
    # 用 identity image 里的 python + bcrypt 生成 hash (host 不需 pip install bcrypt)
    if docker image inspect catfish-identity:0.1.0 >/dev/null 2>&1; then
        ADMIN_HASH=$(docker run --rm catfish-identity:0.1.0 python3 -c \
            "from catfish_identity.users import hash_password; print(hash_password('$ADMIN_PW'))" 2>/dev/null)
    fi
    if [ -z "$ADMIN_HASH" ]; then
        # fallback · catfish_2026 硬编 bcrypt hash (若 image 未 load · 兜底)
        ADMIN_HASH='$2b$12$vN9eb7i7voFcdwzM8W5SOuVmjV3ETViwyA9DDAVQXz1JXhUBFzV2m'
        [ "$ADMIN_PW" != "catfish_2026" ] && \
            echo "  ⚠ image 未 load · 用兜底 hash · 密码强制为 catfish_2026"
        ADMIN_PW="catfish_2026"
    fi

    cat > "$IDENTITY_CFG/users.yaml" <<EOF
# 装机时 setup.sh 自动生成 · $(date '+%Y-%m-%d %H:%M')
# admin 首次登进后立即改密 (Companion 内建改密 UI · 或 sysadmin 面板)
users:
  - email: admin@catfish.com
    password_hash: $ADMIN_HASH
    name: 系统管理员
    department: IT
    role: sysadmin
EOF
    chmod 600 "$IDENTITY_CFG/users.yaml"
    echo "→ users.yaml 生成 · sysadmin: admin@catfish.com / $ADMIN_PW"
    echo "  ⚠ 首次登进立即改密 (delivery/docs 里 SOP 有指引)"
else
    echo "→ users.yaml 已存在 · skip (跨装机保留)"
fi

# ── 2.55 · clients.yaml ──────────────
#
# Companion 启动时用 client_credentials (client_id=hermes-cli) 向 identity 换
# 30 天 service token 给本机 hermes. identity 从 clients.yaml 认 client ——
# 这个文件之前**整个被交付漏了** (打包排除真 clients.yaml 是对的, 但没有
# example 也没有生成步骤), 于是任何一台机器上这条链路都是:
#     401 invalid_client (client 认证失败)
# 模板里的 hash 对应 Companion 内置的 demo secret, 详见 example 头注.
if [ ! -f "$IDENTITY_CFG/clients.yaml" ]; then
    if [ ! -f "$IDENTITY_CFG/clients.yaml.example" ]; then
        echo "❌ 缺 clients.yaml.example · 交付包不完整 (Companion 的 hermes"
        echo "   service token 链路会全挂 invalid_client). 重新解包或联系交付方."
        exit 1
    fi
    cp "$IDENTITY_CFG/clients.yaml.example" "$IDENTITY_CFG/clients.yaml"
    echo "→ clients.yaml 生成 (hermes-cli · Companion service token 用)"
else
    echo "→ clients.yaml 已存在 · skip (跨装机保留)"
fi

# ── 2.6 · gateway config 存在性 check ─
# gateway 启动读 /app/config/models.yaml + roles.yaml. 若 delivery tar 里没打进
# llm-gateway/config · docker mount 空目录 · gateway worker startup 挂反复 die.
# 检 · 若空 · 明报 error 让 IT 从 delivery tar / rsync 补齐.
GATEWAY_CFG="$SCRIPT_DIR/llm-gateway/config"
mkdir -p "$GATEWAY_CFG"
if [ ! -f "$GATEWAY_CFG/models.yaml" ] || [ ! -f "$GATEWAY_CFG/roles.yaml" ]; then
    echo "❌ llm-gateway/config 缺 models.yaml 或 roles.yaml · gateway 启动会挂"
    echo "   fix (2 选 1):"
    echo "     A) 重解压 delivery tar (最新版含 config)"
    echo "     B) 从 catfish 源码 rsync:"
    echo "        rsync -av <mac>:person_task/catfish/central/llm-gateway/config/ $GATEWAY_CFG/"
    echo ""
    read -rp "  确认已补齐后回车继续? (Ctrl+C 中止): "
    if [ ! -f "$GATEWAY_CFG/models.yaml" ]; then
        echo "❌ 仍缺 models.yaml · 中止"
        exit 1
    fi
fi
echo "→ gateway config OK ($(ls "$GATEWAY_CFG" | wc -l | tr -d ' ') files)"

# ── 3. HTTPS 自签 cert (若 ENABLE_HTTPS=1) ────────────────
# certs/ 无条件建 —— compose 里 web 段挂了 ./certs, 目录不存在时
# Docker 会自己造一个 (Linux 上属主 root), 留下野目录. 先建好省事.
# HTTP 模式下目录是空的, web 容器的 41-catfish-ssl.sh 检测不到证书会降级只跑 :80.
mkdir -p certs
if [ "$ENABLE_HTTPS" = "1" ]; then
    # ── 证书体系 ────────────────────
    #
    # 老做法: 一张 `openssl req -x509 -days 3650` 的自签证书直接给 nginx.
    # 浏览器点一次「继续前往」能用, 但**桌面端 (Companion) 连不上**, 报:
    #     The validity period in the certificate exceeds the maximum allowed.
    # macOS Security.framework (以及 iOS) 对 TLS 服务器证书有最长有效期限制
    # (2020-09-01 之后签发的是 398 天). 10 年的证书一律拒, 而且这个拒绝
    # **跟证书受不受信任无关** —— 就算把它装进信任库照样拒.
    #
    # 现在改成两级:
    #   ca.pem      内部 CA, 10 年       ← 发给员工机器, 只装一次
    #   cert.pem    服务器证书, 397 天   ← nginx 用, 每年在服务器上换, 客户端不用动
    #
    # 这样做的现实意义: 500 台机器只装一次 CA. 若沿用单张短效证书, 每 13 个月
    # 就要把新证书重新推到每一台机器上, 运维上不可行.
    #
    # cert.pem 是 fullchain (服务器证书 + CA), nginx 直接用; 客户端拿 ca.pem.
    CERT_DAYS=397          # < 398, 留 1 天余量
    CA_DAYS=3650

    # CA 只在不存在时生成 —— **绝不能每次重跑都换**, 换了等于让所有已装机器
    # 上的 ca.pem 全部失效, 而它们不会自动更新.
    if [ ! -f certs/ca.pem ] || [ ! -f certs/ca-key.pem ]; then
        echo "→ 生成内部 CA (${CA_DAYS} 天 · 发给员工机器, 只装一次)..."
        # 不加 2>/dev/null: openssl < 1.1.1 不支持 -addext, 吞掉报错的话
        # 脚本只会 set -e 静默退出, IT 完全看不出原因.
        if ! openssl req -x509 -newkey rsa:2048 -sha256 -days "$CA_DAYS" -nodes \
            -keyout certs/ca-key.pem -out certs/ca.pem \
            -subj "/CN=Catfish Internal CA" \
            -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
            -addext "keyUsage=critical,keyCertSign,cRLSign"; then
            echo ""
            echo "❌ CA 生成失败 (openssl 报错见上)."
            echo "   常见原因: openssl < 1.1.1 不支持 -addext.  查版本: openssl version"
            exit 1
        fi
        chmod 600 certs/ca-key.pem
        echo "  ✓ certs/ca.pem · certs/ca-key.pem"
    else
        echo "→ 内部 CA 已存在 · 复用 (换 CA 会让所有已装机器失效)"
    fi

    # 服务器证书: 缺失 / IP 变了 / 快过期 时重签. CA 不动, 所以客户端无感.
    NEED_SERVER_CERT=0
    if [ ! -f certs/cert.pem ] || [ ! -f certs/key.pem ]; then
        NEED_SERVER_CERT=1
        REASON="不存在"
    elif ! openssl x509 -in certs/cert.pem -noout -ext subjectAltName 2>/dev/null \
            | grep -q "IP Address:$SERVER_IP"; then
        NEED_SERVER_CERT=1
        REASON="SAN 里没有 IP:$SERVER_IP (换过 IP?)"
    elif ! openssl x509 -in certs/cert.pem -noout -checkend 2592000 >/dev/null 2>&1; then
        NEED_SERVER_CERT=1
        REASON="30 天内到期"
    fi

    if [ "$NEED_SERVER_CERT" = "1" ]; then
        echo "→ 签发服务器证书 (${CERT_DAYS} 天 · IP $SERVER_IP · 原因: $REASON)..."
        openssl req -new -newkey rsa:2048 -nodes \
            -keyout certs/key.pem -out certs/server.csr \
            -subj "/CN=$SERVER_IP" 2>/dev/null
        # SAN 必须有 IP: 现代浏览器不再回退看 CN, 缺了报
        # ERR_CERT_COMMON_NAME_INVALID, 连"继续前往"都点不了.
        cat > certs/server.ext <<EXT
subjectAltName=IP:$SERVER_IP,DNS:catfish.local
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EXT
        if ! openssl x509 -req -in certs/server.csr \
            -CA certs/ca.pem -CAkey certs/ca-key.pem -CAcreateserial \
            -out certs/server.pem -days "$CERT_DAYS" -sha256 \
            -extfile certs/server.ext; then
            echo ""
            echo "❌ 服务器证书签发失败 (openssl 报错见上)."
            exit 1
        fi
        # nginx 用 fullchain (服务器证书 + CA)
        cat certs/server.pem certs/ca.pem > certs/cert.pem
        chmod 600 certs/key.pem certs/ca-key.pem
        rm -f certs/server.csr certs/server.ext
        echo "  ✓ certs/cert.pem (fullchain) · certs/key.pem"
    else
        echo "→ 服务器证书有效 · skip"
    fi

    # ── 三条硬断言 · 任一不过就别装了 ────────────────────────────
    # 这三条都是实测踩过的, 不验就发给客户 = 现场必然出问题.

    # 1) SAN 含 IP —— 缺了浏览器直接拒且无法绕过
    if ! openssl x509 -in certs/server.pem -noout -ext subjectAltName 2>/dev/null \
         | grep -q "IP Address:$SERVER_IP"; then
        echo "❌ 服务器证书里没有 IP:$SERVER_IP 的 SAN · 浏览器会拒绝且无法绕过."
        openssl x509 -in certs/server.pem -noout -ext subjectAltName 2>&1 | sed 's/^/     /'
        exit 1
    fi

    # 2) 有效期 ≤ 398 天 —— 超了 macOS/iOS 一律拒, 且跟信任与否无关.
    #    实测报错原文: "The validity period in the certificate exceeds
    #    the maximum allowed." 这一条就是本次改动的起因.
    CERT_SPAN=$(python3 - <<'PY' 2>/dev/null || echo 9999
import subprocess, datetime
o = subprocess.run(["openssl","x509","-in","certs/server.pem","-noout","-dates"],
                   capture_output=True, text=True).stdout
d = dict(l.split("=",1) for l in o.strip().split("\n"))
f = datetime.datetime.strptime(d["notBefore"].strip(), "%b %d %H:%M:%S %Y %Z")
t = datetime.datetime.strptime(d["notAfter"].strip(),  "%b %d %H:%M:%S %Y %Z")
print((t-f).days)
PY
)
    if [ "$CERT_SPAN" -gt 398 ] 2>/dev/null; then
        echo "❌ 服务器证书有效期 $CERT_SPAN 天 > 398 · macOS/iOS 会直接拒绝连接"
        echo "   (报错: The validity period in the certificate exceeds the maximum allowed)"
        echo "   删掉 certs/ 重跑本脚本重新签发."
        exit 1
    fi

    # 3) CA 能校验服务器证书 —— 链断了客户端装了 CA 也没用
    if ! openssl verify -CAfile certs/ca.pem certs/server.pem >/dev/null 2>&1; then
        echo "❌ CA 校验服务器证书失败 · 证书链断了"
        openssl verify -CAfile certs/ca.pem certs/server.pem 2>&1 | sed 's/^/     /'
        exit 1
    fi

    echo "  ✓ 证书自检通过 (SAN 含 IP · 有效期 ${CERT_SPAN} 天 ≤ 398 · 链完整)"
fi

# ── 4. 装 image tar (若指定 / 若 images/ 里有) ──────────
# IMAGE_TAR 未传 · 自动探 images/*.tar.gz.
# 用户命令 `\ ` 续行错时 · IMAGE_TAR 没进 env · 老版直接 skip load · 后面 docker
# compose up 去 docker.io pull · 内网挂. 现在自动探 · 兜底更稳.
if [ -z "$IMAGE_TAR" ]; then
    AUTO_TAR=$(ls "$SCRIPT_DIR/images/"*.tar.gz 2>/dev/null | head -1)
    if [ -n "$AUTO_TAR" ]; then
        echo "→ 自动发现 image tar: $(basename "$AUTO_TAR") (IMAGE_TAR 未传 · 兜底)"
        IMAGE_TAR="$AUTO_TAR"
    fi
fi

if [ -n "$IMAGE_TAR" ]; then
    if [ ! -f "$IMAGE_TAR" ]; then
        echo "❌ IMAGE_TAR=$IMAGE_TAR 找不到"
        exit 1
    fi
    # ── 无条件 load ─────────────────
    #
    # 老逻辑: `docker image inspect catfish-gateway:0.1.1` 成功就 skip load.
    # 判据只看**tag 在不在**, 不看是不是同一个镜像 —— 而升级场景恰恰是
    # "新 image tar + 完全相同的 tag". 结果:
    #   IT 拿新包重装 → 脚本 skip load → 跑的还是旧镜像 → 一路绿灯装完.
    #
    # 实测: 测试机 down -v + 删目录后用新包重装, 6 个 image ID 跟
    # 三小时前那批一模一样, 新构建的修复一个都没进去, 而 verify 全绿.
    # 这种"假绿灯"比报错危险得多 —— 报错至少会叫住人.
    #
    # 改成无条件 load. load 本身是幂等的 (层已存在就秒过), 代价是重装时多等
    # 几分钟解压校验; 拿几分钟换"包里是什么就跑什么", 值.
    # 真要跳过 (比如同一天反复调 .env), 显式 SKIP_IMAGE_LOAD=1.
    if [ "${SKIP_IMAGE_LOAD:-0}" = "1" ]; then
        echo "→ SKIP_IMAGE_LOAD=1 · 跳过 load"
        echo "  ⚠ 本地镜像可能不是包里那份 · 只在明确知道两者一致时才用这个开关"
    else
        echo "→ load image tar: $IMAGE_TAR (~5-15 min)"
        BEFORE_IDS=$(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null \
                     | grep -E '^catfish' | sort || true)
        if [[ "$IMAGE_TAR" =~ \.gz$ ]]; then
            gunzip -c "$IMAGE_TAR" | docker load
        else
            docker load < "$IMAGE_TAR"
        fi
        echo "  ✓ 装完"

        # 把 load 前后的 image ID 差异打出来 —— 让"到底换没换"这件事可见,
        # 不用 IT 自己去比对.
        AFTER_IDS=$(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null \
                    | grep -E '^catfish' | sort || true)
        if [ "$BEFORE_IDS" = "$AFTER_IDS" ]; then
            echo "  · image ID 无变化 (本地原本就是包里这份)"
        else
            echo "  · image ID 有更新:"
            diff <(echo "$BEFORE_IDS") <(echo "$AFTER_IDS") \
                | grep -E '^[<>]' | sed 's/^</      旧 /; s/^>/      新 /' || true
        fi
    fi
    echo "  现有 image:"
    docker images | grep -E "catfish|postgres:16-alpine" | sed 's/^/    /'
fi

# ── 4.5 · verify 关键 image 本地存 (fail loud · 别让 docker compose 去 pull 挂) ──
MISSING_IMG=""
for img in catfish-identity:0.1.0 catfish-gateway:0.1.1 catfish-web:0.1.0 \
           catfish-skills-hub:0.1.0 catfish-mcp-registry:0.1.0 catfish-wiki-hub:0.1.0 \
           postgres:16-alpine; do
    if ! docker image inspect "$img" >/dev/null 2>&1; then
        MISSING_IMG="$MISSING_IMG $img"
    fi
done
if [ -n "$MISSING_IMG" ]; then
    echo ""
    echo "❌ 本地缺 image ·$MISSING_IMG"
    echo "   fix · 指定 IMAGE_TAR 或放 tar 到 images/ 目录:"
    echo "     IMAGE_TAR=./images/dahua-poc-central-<arch>-<date>.tar.gz bash setup.sh"
    echo "   (内网机不能连 docker.io · 必须本地 load)"
    exit 1
fi

# ── 5. docker compose up ──────────────────────────────
# 用 --force-recreate 强重建 container · 让新 .env
# 里的 GATEWAY_WORKERS / CATFISH_OIDC_ISSUER / CATFISH_IDENTITY_CORS_ORIGINS 立即生效.
# `docker compose up -d` 不加 --force-recreate 时 · 若 container 已存 · 只 restart ·
# env 是创 container 时读的 · 老值不换 · IT 反复怀疑 "为啥 workers 还是 4 / CORS 还挂".
#
# nginx 服务已从 compose 删除, 443 收进 web 容器自己扛,
# 所以这里不再需要按模式挑服务, 一律全量起.
# HTTPS 与否由 web 段的 CATFISH_ENABLE_HTTPS env 控制 (下面 export).
#
# 443 端口映射挪进 docker-compose.https.yml.
# 起初写死在主 compose 里 → HTTP 模式也去抢宿主机 443 → 测试机上 443 被占,
# web 容器直接起不来 (bind: address already in use), 连累 HTTP 模式一起挂.
# Compose 没有条件 ports 语法, 用 override 文件叠加是标准解法.
COMPOSE_FILES=(-f docker-compose.yml)
if [ "$ENABLE_HTTPS" = "1" ]; then
    COMPOSE_FILES+=(-f docker-compose.https.yml)
    # 起之前先探一下端口, 免得等到 compose 报底层 bind 错误才知道.
    #
    # v2: 第一版只查"端口有没有人听", 结果**把我们
    # 自己正在跑的 web 容器也当成占用者拦下来了** —— 服务运行中重跑 setup.sh
    # (正常的升级/换证书路径) 必被自己误拦. 而那种情况恰恰不用拦:
    # `docker compose up --force-recreate` 本来就会先释放旧容器的端口再重绑.
    #
    # 所以先问 Docker "443 是不是我们自己的 catfish-web 在发布", 是则放行;
    # 只有**别人**占着才 fail-loud.
    if command -v ss >/dev/null 2>&1 && ss -tln 2>/dev/null | grep -qE ":$HTTPS_PORT\b"; then
        OURS=$(docker ps --filter "name=catfish-web" --format '{{.Ports}}' 2>/dev/null \
               | grep -c ":${HTTPS_PORT}->" || true)
        if [ "$OURS" -ge 1 ]; then
            echo "→ $HTTPS_PORT 由现有 catfish-web 容器占用 · force-recreate 会自动接管"
        else
            echo ""
            echo "❌ 宿主机 $HTTPS_PORT 端口已被**其它程序**占用 · web 容器会起不来"
            echo "   查是谁占着:  sudo ss -tlnp | grep ':$HTTPS_PORT'"
            echo "   二选一:"
            echo "     A. 停掉占用方 (若是不需要的 nginx/apache):"
            echo "          sudo systemctl stop nginx    # 或 apache2, 按上面查到的"
            echo "     B. 换端口重跑 (员工则访问 https://$SERVER_IP:8443):"
            echo "          CATFISH_HTTPS_PORT=8443 ENABLE_HTTPS=1 SERVER_IP=$SERVER_IP bash setup.sh"
            echo ""
            exit 1
        fi
    fi
fi

echo ""
echo "→ docker compose up --force-recreate ..."
docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate

# ── 6. verify ─────────────────────────────────────────
echo ""
# 屏幕上就是 "identity discovery: _" 光标停着 —— 当时误以为
# 脚本在等他输入什么东西. 加超时后失败几秒内就明确报错.
CURL_T=(--connect-timeout 3 --max-time 8)
# HTTPS 模式走自签证书, curl 默认会拒 —— 加 -k 跳过校验.
# 这里只验"服务通不通", 证书信任是员工浏览器侧的事.
[ "$ENABLE_HTTPS" = "1" ] && CURL_T+=(-k)

# ── 等服务就绪 ─────────────────────────
#
# 老写法是 `sleep 30` 然后一次性 curl. 实测在 8 GB / 无外网的机器上,
# gateway 要先撞一次 LiteLLM cost map 拉取超时才继续启动, 30 秒根本不够 ——
# 于是脚本把"还没起来"报成"❌ 挂", 服务其实几十秒后自己就好了.
#
# 假警报比不报还糟: 客户 IT 被训练成看见红字就忽略, 真出事时也不当回事.
# 改成轮询, 就绪即返回, 超时才报错并给出实际等了多久.
wait_ready() {
    local name="$1" url="$2" max="${3:-180}"
    local t=0
    printf "  等 %s 就绪 " "$name"
    while [ "$t" -lt "$max" ]; do
        if curl "${CURL_T[@]}" -sf "$url" > /dev/null 2>&1; then
            echo " ✓ ${t}s"
            return 0
        fi
        printf "."
        sleep 3
        t=$((t + 3))
    done
    echo " ✗ 超时 ${max}s"
    return 1
}

echo ""
wait_ready "identity" "$ISSUER_URL/.well-known/openid-configuration" 180 || true
wait_ready "gateway"  "http://$SERVER_IP:8999/healthz"                  180 || true

echo ""
echo "── verify ──"

# ── curl 必须带超时 ──────────────────
# 早期版本的问题: 这四个 curl 都没有 --connect-timeout / --max-time. 服务没起来时
# curl 会一路等到系统默认 TCP 超时(可能几分钟); 而上面是 `echo -n` 不换行,

echo -n "  identity discovery: "
if curl "${CURL_T[@]}" -sf "$ISSUER_URL/.well-known/openid-configuration" -H "Origin: $WEB_URL" > /dev/null 2>&1; then
    curl "${CURL_T[@]}" -s "$ISSUER_URL/.well-known/openid-configuration" | grep -oE '"issuer":"[^"]+"' | head -1
else
    echo "❌ 挂 · 看 docker logs catfish-identity"
fi

echo -n "  gateway healthz:    "
if curl "${CURL_T[@]}" -sf "http://$SERVER_IP:8999/healthz" > /dev/null 2>&1; then
    echo "✓ 200"
else
    echo "❌ 挂 · 看 docker logs catfish-gateway"
fi

echo -n "  web /config.js:     "
if curl "${CURL_T[@]}" -sf "$WEB_URL/config.js" 2>/dev/null | grep -q "$SERVER_IP"; then
    echo "✓ oidcIssuer 含 $SERVER_IP"
else
    echo "⚠ 待查 · 看 docker exec catfish-web cat /usr/share/nginx/html/config.js"
fi

# ── gateway 到其它容器的反代 (P3.5.84 · 7/29 达华现场) ──────────────
#
# gateway 把这几类请求转发给同网内的别的容器. 它们的上游地址默认写的是
# 127.0.0.1 —— 开发机上四个服务同机, 这个默认恰好对; 容器里 127.0.0.1 是
# gateway 自己, 于是全部 502.
#
# 为什么必须单独验: 这条链路挂了**不影响登录、不影响聊天、门户照常打开**,
# 只有中央门户的「系统管理」页会显示 "不可达". 7/29 达华装完才被人点开发现,
# 而错误的默认值从第一版就在, 只是没人验过。
#
# 这里用 401 也算通: 没带 token 时上游返 401 说明**网络这一跳是通的**,
# 正是我们要验的那件事; 502 才是连不上。
echo -n "  内部服务反代:      "
PROXY_FAIL=""
for probe in "mcp:/v1/mcp/registry" "hub:/v1/hub/healthz" "wiki:/v1/wiki/healthz"; do
    name="${probe%%:*}"; path="${probe#*:}"
    code=$(curl "${CURL_T[@]}" -s -o /dev/null -w '%{http_code}' \
           "http://$SERVER_IP:8999$path" 2>/dev/null || echo 000)
    case "$code" in
        502|000) PROXY_FAIL="$PROXY_FAIL $name($code)" ;;
    esac
done
if [ -z "$PROXY_FAIL" ]; then
    echo "✓ mcp / hub / wiki 三跳都通"
else
    echo "❌ 连不上:$PROXY_FAIL"
    echo "     gateway 到这些容器的上游地址配错了 (多半还是 127.0.0.1)."
    echo "     查 llm-gateway/config/models.yaml 末尾的 mcp_registry /"
    echo "     skills_hub / wiki_hub 段, upstream_url 必须是 compose 服务名"
    echo "     (http://mcp-registry:8996 这种), 不能是 127.0.0.1."
fi

# HTTPS 模式下 OIDC 走 web 容器 443 的精确路径反代 —— 单独验一次,
# 因为这条链路(浏览器 → web:443 → identity:8998)跟上面直连 8998 不是一回事.
if [ "$ENABLE_HTTPS" = "1" ]; then
    echo -n "  HTTPS OIDC 反代:    "
    if curl "${CURL_T[@]}" -sf "https://$SERVER_IP/.well-known/openid-configuration" \
         | grep -q '"issuer"'; then
        echo "✓ https://$SERVER_IP/.well-known/openid-configuration 通"
    else
        echo "❌ 挂 · docker compose logs web | grep catfish-web-ssl"
    fi
fi

# ── identity 签名密钥唯一性 ──
#
# 两个 worker 若各生成一把 RSA, JWT 验签会约 50% 失败, 表现为"登录时好时坏".
#
# ⚠ 不能靠 grep 日志里的 kid=:
#   logging.basicConfig 只在 app.py main() 里调, 而 uvicorn 多 worker 是
#   spawn 子进程重新 import, 不走 main() → 子进程没有 logging handler →
#   catfish_identity 的 logger.info 根本进不了 docker logs.
#   拿一条不可能出现的日志做验收 = 假绿灯 / 假红灯.
#
# 改成直接查文件: 修复的核心保证就是"无论几个 worker, 只落一把 private.pem,
# 且不留 .tmp 残file". 这个是确定性的, 不依赖日志.
echo -n "  identity 签名密钥:  "
KEYDIR=/home/catfish/.catfish/identity-server/keys
KEYLS=$(docker compose "${COMPOSE_FILES[@]}" exec -T identity ls -1 "$KEYDIR" 2>/dev/null || true)
if [ -z "$KEYLS" ]; then
    echo "⚠ 读不到 $KEYDIR · 手工查 docker compose exec identity ls -la $KEYDIR"
elif echo "$KEYLS" | grep -q '\.tmp\.'; then
    echo "❌ 有 .tmp 残file · 生成过程被打断:"
    echo "$KEYLS" | sed 's/^/      /'
elif [ "$(echo "$KEYLS" | grep -c '^private\.pem$')" = "1" ] \
  && [ "$(echo "$KEYLS" | grep -c '^public\.pem$')" = "1" ]; then
    echo "✓ 单一密钥对 · 无竞态残留"
else
    echo "❌ 密钥目录异常:"
    echo "$KEYLS" | sed 's/^/      /'
fi

# ── 上游 LLM key 空值显眼提示 ────────────────────
# key 空时全部服务照常起 · healthz 全绿, 直到员工发第一条聊天消息才报
# 「上游 LLM Provider 鉴权挂了」(gateway errors.py) —— 现场极易误判成
# 聊天功能坏了. 不 hard fail: 仅门户/离线演示场景允许无 key 装机.
if ! grep -qE '^DASHSCOPE_API_KEY=[^[:space:]]+' .env 2>/dev/null; then
    echo ""
    echo "⚠⚠ DASHSCOPE_API_KEY 是空的 —— 门户/登录不受影响, 但聊天会在"
    echo "   员工发第一条消息时报「上游 LLM Provider 鉴权挂了」."
    echo "   要演示聊天: 编辑 .env 填 DASHSCOPE_API_KEY=sk-... 然后:"
    echo "     docker compose up -d --force-recreate gateway"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  装机完毕 · 员工浏览器打:"
echo "    $WEB_URL"
echo ""
echo "  默认 sysadmin: admin@catfish.com / catfish_2026"
echo "  ★ 首次登录后立刻改密"
echo ""
if [ "$ENABLE_HTTPS" = "1" ]; then
    echo "  🔒 已启用 HTTPS (web 容器 443 · 自签证书)"
    echo "     首次访问浏览器会提示证书不受信 · 点「高级 → 继续前往」即可."
    echo "     点过之后就是安全上下文, crypto.subtle 可用, 登录正常."
    echo ""
    echo "     ★ 要发给员工的是 certs/ca.pem (内部 CA), **不是** cert.pem."
    echo "       cert.pem 是服务器证书, 每年会换; ca.pem 十年有效, 只装一次."
    echo ""
    echo "       浏览器: 把 ca.pem 推进系统信任库 (域控组策略 / Jamf / Intune),"
    echo "               一次导入永久免警告; 不推的话每台每个浏览器都要手点一次."
    echo ""
    # 桌面端必须单独说 —— 它走 Rust reqwest, 不认浏览器那次
    # 「继续前往」. 不提这条的话现场表现是「浏览器打得开、面板说连不上」,
    # 会被误判成网络问题查半天.
    echo "       桌面端 (Companion): 用 Rust 发请求, 不认浏览器点过的「继续前往」."
    echo "               不处理的话仪表盘显示「中央门户连不上」, 而同一地址"
    echo "               浏览器打得开 —— 极易误判成网络问题."
    echo "               把 ca.pem 复制到员工机器的 ~/.catfish/server-ca.pem 即可"
    echo "               (不需要管理员权限; 装进系统信任库也行, Rust 也读它)."
    echo ""
    echo "     ⚠ 服务器证书 397 天到期. 到期前在**服务器上**重跑 bash setup.sh"
    echo "       即可自动重签 —— CA 不变, 员工机器上的 ca.pem 不用动."
    echo "       (为什么不签 10 年: macOS/iOS 对 TLS 服务器证书有 398 天上限,"
    echo "        超了直接拒连, 且跟证书受不受信任无关.)"
else
    echo "  ⚠ 走 HTTP · 员工浏览器用**裸 IP**访问时前端会卡在「加载中…」"
    echo "     原因: oidc-client 的 PKCE 要 crypto.subtle, 浏览器只在安全上下文"
    echo "           (https 或 localhost/127.0.0.1) 才提供它."
    echo "     两条出路:"
    echo "       A. 重跑装机开 HTTPS (推荐):  ENABLE_HTTPS=1 bash setup.sh"
    echo "       B. 每台机器加 chrome://flags 的"
    echo "          unsafely-treat-insecure-origin-as-secure (Safari 无此 flag)"
fi
echo "═══════════════════════════════════════════════════════"
