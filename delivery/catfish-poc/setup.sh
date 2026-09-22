#!/usr/bin/env bash
# Catfish 中央服务一键装机脚本
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
#   # 自动探测 IP · 默认走 HTTPS (交付/生产推荐)
#   bash setup.sh
#
#   # 明确选择 HTTP (仅本机/测试, 裸 IP 浏览器会卡 PKCE)
#   SERVER_IP=192.168.100.50 ENABLE_HTTPS=0 bash setup.sh
#
#   # 显式指定 IP + 自签 cert (推荐 · 生产 POC 走 HTTPS)
#   SERVER_IP=192.168.100.50 ENABLE_HTTPS=1 bash setup.sh
#
#   # 只重生成 .env (不动 docker · 已装好想改 IP 时用)
#   REGEN_ENV_ONLY=1 SERVER_IP=192.168.100.50 bash setup.sh
#
#   # 升级镜像 (保留现有 .env / 数据 / 证书, 不从 .env.example 覆盖)
#   UPGRADE=1 IMAGE_TAR=./images/catfish-poc-central-amd64-<date>.tar.gz \
#     SERVER_IP=192.168.100.50 ENABLE_HTTPS=1 bash setup.sh
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
ENABLE_HTTPS="${ENABLE_HTTPS:-}"
REGEN_ENV_ONLY="${REGEN_ENV_ONLY:-0}"
UPGRADE="${UPGRADE:-0}"
IMAGE_TAR="${IMAGE_TAR:-}"   # 若未装 image · 指到 image tar 路径
GATEWAY_WORKERS="${GATEWAY_WORKERS:-}"
IDENTITY_WORKERS="${IDENTITY_WORKERS:-}"

# ── 参数写法: 环境变量必须在命令**前面** ──────────────────
#
# 9/22 现场撞到: `bash setup.sh SERVER_IP=127.0.0.1`。
# shell 把 `SERVER_IP=127.0.0.1` 当成位置参数传给脚本, 而不是环境变量 ——
# 脚本原样忽略, 然后去自动探测 IP, 报了个跟真实原因完全无关的错。
#
# 这个错法很自然 (很多命令行工具确实收 key=value 参数), 而且静默忽略的
# 代价不只是这一次: 它会让人以为自己指定过了。所以认出来当场拦。
for _arg in "$@"; do
    case "$_arg" in
        *=*)
            _k="${_arg%%=*}"
            echo "❌ 参数写法不对: $_arg"
            echo ""
            echo "   环境变量要写在命令**前面**, 不是后面:"
            echo "       $_arg bash setup.sh          ← 对"
            echo "       bash setup.sh $_arg          ← 错 (被当成位置参数忽略)"
            echo ""
            echo "   写在后面的话 shell 不会把它设成环境变量, 脚本收不到,"
            echo "   然后会以\"你没指定\"继续往下走 —— 报的错跟真实原因无关。"
            [ "$_k" = "SERVER_IP" ] && echo "   (顺带: 别用 127.0.0.1, 那样只有这台机器自己能访问)"
            echo ""
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 镜像 tag 的唯一来源 = docker-compose.yml ──────────────────────
# 9/12: 以前 tag 在这个脚本里写死两处 (identity 生成 admin hash / 关键镜像清单),
# gateway 0.1.0→0.1.1 那次只改了 compose, 这里没跟 —— 就是 build-package.sh 头部
# 复盘说的漂移。build-package.sh 也是从 compose 读 tag, 现在三边只剩一个源头。
# 9/22: 先确认文件在。不查的话 grep 会直接把
#     grep: /path/docker-compose.yml: No such file or directory
# 甩给现场 —— 那行字既不说明该干什么, 也不像是我们的脚本报的。
# (setup.ps1 那边一直有这个检查, 是 sh 这边漏了。)
#
# 最常见的成因是**把补丁包解到了空目录**: 补丁只带 setup.sh / setup.ps1 /
# tools/, 完整交付包才有 compose、.env.example 和 images/。
if [ ! -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    echo "❌ 这个目录里没有 docker-compose.yml: $SCRIPT_DIR"
    echo ""
    if [ -d "$SCRIPT_DIR/tools" ] && [ ! -d "$SCRIPT_DIR/images" ]; then
        echo "   看目录内容, 这里只有装机脚本补丁, 没有完整交付包。"
        echo "   补丁是**覆盖**用的, 要解到已经装过的目录上, 不是单独解一份。"
        echo ""
        echo "   正确顺序:"
        echo "     1. 先解完整包:  tar xzf catfish-poc-FULL-<arch>-<date>.tar.gz"
        echo "     2. 再解补丁到同一层: tar xzf catfish-setup-patch-<date>.tar.gz"
        echo "     3. cd delivery/catfish-poc && bash setup.sh"
    else
        echo "   请在解包后的 delivery/catfish-poc/ 目录里运行。"
        echo "   那个目录里应该同时有 docker-compose.yml / .env.example / images/。"
    fi
    echo ""
    exit 1
fi

compose_images() {
    grep -E '^[[:space:]]+image:[[:space:]]+[^[:space:]]+' "$SCRIPT_DIR/docker-compose.yml" \
        | awk '{print $2}'
}
IDENTITY_IMAGE="$(compose_images | grep '^catfish-identity:' | head -1)"
if [ -z "$IDENTITY_IMAGE" ]; then
    echo "❌ docker-compose.yml 里找不到 catfish-identity 的 image 行"
    exit 1
fi
cd "$SCRIPT_DIR"

if [ "$UPGRADE" = "1" ] && [ ! -f .env ]; then
    echo "❌ UPGRADE=1 但当前目录没有 .env · 拒绝按新装流程生成配置"
    echo "   请回到原安装目录, 或确认旧 .env 已备份后再升级"
    exit 1
fi

# 升级命令未显式传 ENABLE_HTTPS 时, 沿用现场 .env 的模式, 避免 HTTPS
# 部署被无意切回 HTTP. 老包没有模式字段时, 有证书按 HTTPS, 否则升级保守走 HTTP;
# 新装没有 .env 时默认 HTTPS, 避免交付门户因 HTTP 裸 IP 卡在加载中.
if [ -z "$ENABLE_HTTPS" ] && [ -f .env ]; then
    ENABLE_HTTPS=$(grep -E '^CATFISH_ENABLE_HTTPS=' .env | head -1 | cut -d= -f2- || true)
fi
if [ -z "$ENABLE_HTTPS" ]; then
    if [ "$UPGRADE" = "1" ] && [ -s certs/cert.pem ] && [ -s certs/key.pem ]; then
        ENABLE_HTTPS=1
    elif [ "$UPGRADE" = "1" ]; then
        ENABLE_HTTPS=0
    else
        ENABLE_HTTPS=1
    fi
fi

# 升级时沿用现场自定义 HTTPS 端口; 新装默认 443.
if [ -z "${CATFISH_HTTPS_PORT:-}" ] && [ -f .env ]; then
    CATFISH_HTTPS_PORT=$(grep -E '^CATFISH_HTTPS_PORT=' .env | head -1 | cut -d= -f2- || true)
fi

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

# 替换或追加 .env 字段。只给部署派生参数使用, 不碰密码/API key.
set_env_value() {
    local key="$1"
    local value="$2"
    local escaped
    escaped=$(printf '%s' "$value" | sed 's/[&|\\]/\\&/g')
    if grep -qE "^${key}=" .env; then
        sed_i "s|^${key}=.*|${key}=${escaped}|" .env
    else
        printf '\n%s=%s\n' "$key" "$value" >> .env
    fi
}

# ── 随机密钥的来源 ─────────────────────────────────────────
#
# 9/22 改。原来是:
#     openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | base64 | ...
# 旁边还写着一句 "openssl 装了 docker 的机器上都有"。
#
# 那句话在 Linux / macOS 上成立, 在 **Windows 上不成立** —— Windows 既没有
# openssl 也没有 /dev/urandom。这正是 Windows 一直没法照着装的原因之一
# (另一个是 deploy.ps1 去跑 docker compose build, 而客户包里没有源码)。
#
# 现在优先用**我们自己镜像里的 python**: Docker 本来就是硬前提, 镜像在
# 上一步 (2.pre) 已经 load 过, 所以这条路在三个平台上完全一样 ——
# setup.ps1 里也是同一行命令。
#
# 后面两级是给开发机 / REGEN_ENV_ONLY 用的 (那时没 load 镜像)。
# **降级会出声**: 不响的降级最后总会变成"为什么它在我这儿不一样"。
_RAND_SOURCE=""
_pick_rand_source() {
    [ -n "$_RAND_SOURCE" ] && return 0
    if docker image inspect "$IDENTITY_IMAGE" >/dev/null 2>&1; then
        _RAND_SOURCE="container"
    elif command -v openssl >/dev/null 2>&1; then
        _RAND_SOURCE="openssl"
        # ⚠ 这些提示必须走 stderr。rand_hex / rand_fernet 都是在 $(...) 里调用的,
        #   往 stdout 打一个字都会被当成密钥值捕获 —— 实测现象是
        #   `sed: -e expression #1, char 93: unterminated 's' command`,
        #   因为密钥变成了多行。错得很隐蔽: 提示文字看着无害。
        echo "  ⓘ 随机数走宿主机 openssl —— 镜像还没 load。" >&2
        echo "    正常装机路径会先 load 镜像再生成密钥, 走容器里的 python。" >&2
    elif [ -r /dev/urandom ]; then
        _RAND_SOURCE="urandom"
        echo "  ⚠ 宿主机没有 openssl, 退到 /dev/urandom。" >&2
    else
        echo "❌ 找不到可用的随机数来源 (容器 / openssl / /dev/urandom 都不可用)。" >&2
        echo "   密钥必须是真随机 —— 这里不给\"凑合能跑\"的兜底值, 因为那种值" >&2
        echo "   会一路装完、绿灯通过, 然后成为所有客户共用的同一把钥匙。" >&2
        exit 1
    fi
    return 0
}

# $1 = 字节数; 输出 2*N 个 hex 字符
rand_hex() {
    _pick_rand_source
    case "$_RAND_SOURCE" in
        container) docker run --rm "$IDENTITY_IMAGE" python3 -c \
                     'import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))' "$1" ;;
        openssl)   openssl rand -hex "$1" ;;
        urandom)   od -An -tx1 -N "$1" /dev/urandom | tr -d ' \n'; echo ;;
    esac
}

# Fernet key = 32 字节随机的 url-safe base64。CATFISH_SECRET_KEY 用。
rand_fernet() {
    _pick_rand_source
    case "$_RAND_SOURCE" in
        container) docker run --rm "$IDENTITY_IMAGE" python3 -c \
                     'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())' ;;
        openssl)   openssl rand -base64 32 | tr '+/' '-_' ;;
        urandom)   head -c 32 /dev/urandom | base64 | tr '+/' '-_' ;;
    esac
}

echo "═══════════════════════════════════════════════════════"
echo "  Catfish 中央服务一键装机"
echo "═══════════════════════════════════════════════════════"

# ── 1. 探测 / 确认 server IP ─────────────────────────────
if [ -z "$SERVER_IP" ]; then
    # 探测: 取第一条非 loopback / 非 docker 的 IPv4
    # ⚠ 结尾那个 `|| true` 不是装饰。
    #
    # grep 没匹配到任何东西时返回 1, 而 `set -euo pipefail` 会让整条管道的
    # 失败**直接杀掉脚本** —— 于是下面那句"无法自动探测 server IP"的提示,
    # 恰恰在它该出现的那种情况下永远打不出来, 现场看到的是脚本印完标题
    # 就没声了。
    #
    # 9/22 实测确认 (WSL 里 hostname -I 返回 172.29.x.x, 被下面的排除规则
    # 全部滤掉 → grep 返回 1 → 静默退出, 一个字都不打)。
    SERVER_IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | \
        grep -vE '^(127\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[0-1]\.|169\.254\.)' | \
        head -1 || true)
    if [ -z "$SERVER_IP" ]; then
        echo "❌ 无法自动探测 server IP"
        echo ""
        # WSL 要单独说。WSL2 虚拟机的 IP 通常落在 172.16-172.31, 正好被上面
        # 那条"排除 Docker NAT 网段"的规则滤掉 —— 但**就算探到也是错的**:
        # Docker Desktop 把端口发布在 Windows 宿主机上, 员工连的是 Windows
        # 的局域网 IP, 不是 WSL 虚拟机的。填了 WSL 的 IP 会装完就绿, 然后
        # 除了这台机器谁也连不上。
        if grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null; then
            echo "   检测到这里是 WSL。"
            echo ""
            echo "   ⚠ WSL 虚拟机自己的 IP **不能**用 —— Docker Desktop 把端口发布在"
            echo "     Windows 宿主机上, 员工要连的是 Windows 那边的局域网 IP。"
            echo "     填了 WSL 的 IP, 装完会全绿, 但除了这台机器谁也访问不了。"
            echo ""
            echo "   取 Windows 侧的局域网 IP (在 WSL 里就能跑):"
            echo "       ipconfig.exe | grep -A4 -iE 'ethernet|wi-?fi' | grep -i 'IPv4'"
            echo ""
            echo "   然后:"
            echo "       SERVER_IP=<上面那个 IP> bash setup.sh"
        else
            echo "   请显式指定 (注意写在命令前面):"
            echo "       SERVER_IP=192.168.x.x bash setup.sh"
            echo ""
            echo "   本机有哪些地址:"
            (hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' | sed 's/^/       /') || true
        fi
        echo ""
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

# ── 2.pre · 先装 image, 再写任何配置 ───────────────────────
#
# 9/22 从「步骤 4」挪到这里。两个原因:
#
#  1. 缺镜像应该在**写任何配置之前**就报。原来的顺序是先生成 .env、
#     users.yaml、证书, 走到第 4 步才发现 tar 不在 —— 现场看到的是
#     "装到一半停了", 得先搞清楚哪些文件已经被改过才敢重来。
#
#  2. 镜像 load 完之后, 后面生成密钥就能借容器里的 python 干活, 不再
#     依赖宿主机有没有 openssl / /dev/urandom。这是 Windows 能照着装的
#     前提 —— 见下面 rand_hex 那段。
#
# REGEN_ENV_ONLY=1 只重写 .env, 不该为此等几分钟 load, 所以跳过。
if [ "$REGEN_ENV_ONLY" = "1" ]; then
    echo "→ REGEN_ENV_ONLY=1 · 跳过 image load"
else
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
        # 老逻辑: `docker image inspect catfish-gateway:<tag>` 成功就 skip load.
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
    # 清单从 compose 读 (含 postgres:16-alpine), 不再手抄。
    for img in $(compose_images); do
        if ! docker image inspect "$img" >/dev/null 2>&1; then
            MISSING_IMG="$MISSING_IMG $img"
        fi
    done
    if [ -n "$MISSING_IMG" ]; then
        echo ""
        echo "❌ 本地缺 image ·$MISSING_IMG"
        echo "   fix · 指定 IMAGE_TAR 或放 tar 到 images/ 目录:"
        echo "     IMAGE_TAR=./images/catfish-poc-central-<arch>-<date>.tar.gz bash setup.sh"
        echo "   (内网机不能连 docker.io · 必须本地 load)"
        exit 1
    fi

fi

# ── 对外地址 —— 后面端口检查和验证都要用 ──────────────────
#
# ⚠ 这几行必须跟 tools/envgen.py 里那段**算法一致**。写两遍是因为:
#   envgen 要把它们写进 .env, 而这个脚本后面要拿它们去探健康。
#   (给 envgen 加一个"把算好的值吐回来"的出口也行, 但那样每个 wrapper
#    都要解析它的输出 —— 反而多一处会跑偏的地方。)
#
# HTTP 和 HTTPS 下 issuer 和前端**不是同一个地址**:
#   HTTPS: 443 由 web 容器自己扛, 两者同一个入口
#   HTTP:  identity 和 web 各暴露各的端口 (8998 / 5173)
# 9/22 重构时一度把 HTTP 模式也写成同一个地址, HTTPS 路径看不出来,
# 而当时的回归测试全是 HTTPS。现在两种模式都有测试。
HTTPS_PORT="${CATFISH_HTTPS_PORT:-443}"
if [ "$ENABLE_HTTPS" = "1" ]; then
    if [ "$HTTPS_PORT" = "443" ]; then
        ISSUER_URL="https://$SERVER_IP"
    else
        ISSUER_URL="https://$SERVER_IP:$HTTPS_PORT"
    fi
    WEB_URL="$ISSUER_URL"
else
    ISSUER_URL="http://$SERVER_IP:8998"
    WEB_URL="http://$SERVER_IP:5173"
fi

# ── 2. 生成 / 升级 .env ───────────────────────────────────
#
# 9/22: 这里原来是 235 行 bash —— 捞旧密钥、备份、cp 模板、写回、判空生成、
# 刷派生配置。整段搬进 tools/envgen.py, **setup.ps1 调的是同一个文件**。
#
# 为什么不把它翻译一份 PowerShell: 这段代码的价值全在几条不变量上, 而每条
# 都是踩出来的 ——
#
#   · 先捞旧值再覆盖 (8/1 和 8/4 各踩一次: 保护逻辑写在 cp 之后, 于是
#     "已有值·保持不变"那个分支**重跑时永远走不到")
#   · PG_PASSWORD 换了 → 连不上已有的库, 四个服务全挂, 报错只在容器日志里
#   · CATFISH_SECRET_KEY 换了 → 服务照常起, 但库里所有供应商 API key 全部
#     解不开, 且旧密文无法恢复
#
# 两份实现里漏掉任何一条, 症状都是"装完看起来正常"。所以只留一份。
#
# 跑法: 优先容器 (镜像在 2.pre 已 load, 宿主机不用装 python), 宿主机有
# python3 时直接跑 —— test_setup_env.sh 走的是后一条, CI 里不需要 docker。
ENVGEN_ARGS=(
    --server-ip "$SERVER_IP"
    --https "$ENABLE_HTTPS"
    --https-port "${CATFISH_HTTPS_PORT:-443}"
    --upgrade "$UPGRADE"
    --gateway-workers "${GATEWAY_WORKERS:-}"
    --identity-workers "${IDENTITY_WORKERS:-}"
    --identity-url "${CATFISH_IDENTITY_URL:-}"
)

# 既有 pgdata 卷 —— 查卷要 docker, 所以在这边查; **判断**在 envgen 里,
# 免得两个平台各写一遍"有卷但没密码该怎么办"。
PG_VOL=$(docker volume ls -q 2>/dev/null | grep -E '_pgdata$' | head -1 || true)
[ -n "$PG_VOL" ] && ENVGEN_ARGS+=(--pg-volume "$PG_VOL")

if docker image inspect "$IDENTITY_IMAGE" >/dev/null 2>&1; then
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -v "$SCRIPT_DIR:/work" \
        -v "$SCRIPT_DIR/tools:/tools:ro" \
        "$IDENTITY_IMAGE" \
        python3 /tools/envgen.py --dir /work "${ENVGEN_ARGS[@]}"
elif command -v python3 >/dev/null 2>&1; then
    echo "  ⓘ 镜像还没 load, 用宿主机的 python3 生成 .env" >&2
    python3 "$SCRIPT_DIR/tools/envgen.py" --dir "$SCRIPT_DIR" "${ENVGEN_ARGS[@]}"
else
    echo "❌ 既没有 load 好的 catfish-identity 镜像, 宿主机也没有 python3。"
    echo "   .env 生成需要其中之一。正常装机路径会先 load 镜像 (见 2.pre),"
    echo "   所以走到这里多半是 image tar 没放进 images/ 目录。"
    exit 1
fi

# ── 若只重生 .env · 到此为止 ───────────────────────────
if [ "$REGEN_ENV_ONLY" = "1" ]; then
    echo ""
    echo "✓ .env 已重生 · REGEN_ENV_ONLY=1 模式退出"
    echo "  下一步 · 重启涉及服务:"
    echo "    docker compose up -d --force-recreate identity gateway web"
    exit 0
fi

# ── 2.5 · identity 的 users.yaml / clients.yaml ───────────
#
# 9/22 搬进 tools/seedgen.py, setup.ps1 调同一个文件。原来这里是两段 bash,
# 其中 users.yaml 那段还带一个硬编码的 bcrypt hash 兜底 —— 镜像没 load 时
# 用它, 并把密码强制回 catfish_2026。那个兜底现在不需要了: 2.pre 已经保证
# 镜像在位, 走不到"没镜像"的情况; 而留着一个所有客户共用的 hash 本身就是
# 个隐患。
ADMIN_PW="${ADMIN_PASSWORD:-catfish_2026}"
if ! docker run --rm \
        --user "$(id -u):$(id -g)" \
        -v "$SCRIPT_DIR:/work" \
        -v "$SCRIPT_DIR/tools:/tools:ro" \
        "$IDENTITY_IMAGE" \
        python3 /tools/seedgen.py --dir /work --admin-password "$ADMIN_PW"; then
    echo "❌ identity 种子配置生成失败 (详情见上)"
    exit 1
fi

# ── 3. HTTPS 自签 cert (若 ENABLE_HTTPS=1) ────────────────
#
# 9/22: 原来这里是 130 行 openssl 命令 (建 CA、签 CSR、拼 fullchain、再用
# openssl x509/verify 做三项自检)。整段搬进 tools/certgen.py, 跑在 identity
# 镜像里。
#
# 为什么搬:
#
#   · Windows 没有 openssl —— 这是中央端一直没法在 Windows 上照着装的原因之一。
#     certgen.py 用的是 identity 的直接依赖 cryptography, 一定在镜像里,
#     三个平台调用方式一模一样 (setup.ps1 里是同一行)。
#   · openssl < 1.1.1 不支持 -addext, 现场撞到过; 新写法没有这个变量。
#   · "要不要重签" 的判断 (SAN 对不对 / 快到期没有 / CA 存不存在) 原来散在
#     shell 的 if 里, 现在跟生成逻辑待在一起, 不用在两种语言里各写一遍。
#
# 证书体系没变: CA 十年只建一次 (重建会让员工机器上已装的 ca.pem 全失效),
# 服务器证书 397 天 (Apple 的上限是 398, 超了连装进信任库都会被拒)。
# 自检也没少 —— certgen.py 写完会**独立回读**核对签发关系、SAN、fullchain 顺序。
#
# Docker 会自己造 certs 目录 (Linux 上属主 root), 留下野目录. 先建好省事.
# HTTP 模式下目录是空的, web 容器的 41-catfish-ssl.sh 检测不到证书会降级只跑 :80.
mkdir -p certs
if [ "$ENABLE_HTTPS" = "1" ]; then
    echo "→ 证书 (SAN 含 $SERVER_IP)"
    # --user: 让产物属主是当前用户, 否则 Linux 上会变成镜像里的 uid 1000,
    # 下次非 root 的运维想动 certs/ 会 permission denied。
    # Windows/macOS 的 Docker Desktop 自己处理属主, 那边不传这个参数 (见 setup.ps1)。
    if ! docker run --rm \
            --user "$(id -u):$(id -g)" \
            -v "$SCRIPT_DIR/certs:/certs" \
            -v "$SCRIPT_DIR/tools:/tools:ro" \
            "$IDENTITY_IMAGE" \
            python3 /tools/certgen.py --ip "$SERVER_IP" --out /certs; then
        echo "❌ 证书生成失败 (详情见上)"
        exit 1
    fi
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

# ── gateway 到其它容器的反代 (P3.5.84 · 7/29 现场) ──────────────
#
# gateway 把这几类请求转发给同网内的别的容器. 它们的上游地址默认写的是
# 127.0.0.1 —— 开发机上四个服务同机, 这个默认恰好对; 容器里 127.0.0.1 是
# gateway 自己, 于是全部 502.
#
# 为什么必须单独验: 这条链路挂了**不影响登录、不影响聊天、门户照常打开**,
# 只有中央门户的「系统管理」页会显示 "不可达". 7/29 现场装完才被人点开发现,
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

# ── 7. 记下"这次装的到底是哪一版" ──────────────────────────
#
# 9/22 加。六个镜像的 tag 从 9/12 起一直是 0.1.2, 每次交付都一样 —— 于是
# `docker images` 看不出装的是哪一版, 出了问题第一句"你装的哪个包"就答不上来。
#
# 9/22 之后 tag 也带上了身份 (日期 + 源码内容哈希, 由 scripts/central_version.sh
# 算出来, 不是人填的), 所以 docker images 那一列已经能区分了。
#
# 但 digest 仍然记 —— tag 是**构建时**贴上去的标签, digest 是镜像内容本身。
# 两者对不上的情况真实存在: 有人手工 docker tag 过, 或者 load 了一个别处
# 打的同名包。报障时认 digest 不会错。
#
# 写成文件而不是只打屏幕: 装完半个月后来查的人不会有当时的终端。
{
    echo "# Catfish 中央端装机记录 —— setup.sh 自动生成, 请勿手改"
    echo "installed_at=$(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "server_ip=$SERVER_IP"
    echo "https=$ENABLE_HTTPS"
    if [ -f BUILD-INFO.txt ]; then
        echo "# ↓ 来自交付包的 BUILD-INFO.txt"
        sed 's/^/package_/' BUILD-INFO.txt
    fi
    echo "# ↓ 每个服务实际跑的镜像 (tag 可能重名, image_id 不会)"
    for img in $(compose_images); do
        id=$(docker image inspect "$img" --format '{{.Id}}' 2>/dev/null || echo "<查不到>")
        created=$(docker image inspect "$img" --format '{{.Created}}' 2>/dev/null || echo "?")
        echo "image=$img id=${id#sha256:} created=$created"
    done
} > INSTALLED-BUILD.txt 2>/dev/null || true

echo ""
echo "── 这次装的是哪一版 ──"
echo "  已写入 INSTALLED-BUILD.txt (报障时把这个文件发给我们)"
for img in $(compose_images); do
    id=$(docker image inspect "$img" --format '{{.Id}}' 2>/dev/null | cut -c8-19 || echo "??")
    printf '    %-34s %s\n' "$img" "$id"
done
echo "  ⚠ tag 相同不代表镜像相同 —— 认上面那串 id, 不是认 tag。"

echo "═══════════════════════════════════════════════════════"
