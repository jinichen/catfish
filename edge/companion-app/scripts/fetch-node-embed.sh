#!/usr/bin/env bash
# 由 build-mac-resources.sh source: 把 Node $NODE_VERSION 下到 $NODE_TMP (9/23)。
#
# 需要的变量: NODE_VERSION / NODE_FNAME / NODE_TMP (调用方已设好)。
# 可选: NODE_MIRRORS="源1 源2" 覆盖下载源顺序。
#
# 9/23 实测: 以前只连 nodejs.org, 而且 curl 没设低速超时 —— 连接挂住就一直停在 0%,
# 不报错也不重试。现在每个源卡 60 秒 (平均 <10KB/s) 就换下一个。
# 按顺序试这几个源 (NODE_MIRRORS 可覆盖, 空格分隔), 每个源卡 60 秒就换下一个;
# 下完用同一个源的 SHASUMS256.txt 核对 —— 镜像只挡传输损坏, 不代替官方签名。
NODE_MIRRORS="${NODE_MIRRORS:-https://nodejs.org/dist https://npmmirror.com/mirrors/node}"
node_sha_ok() {
    local base="$1" want got
    want="$(curl -fsSL --connect-timeout 20 --max-time 60 "$base/v${NODE_VERSION}/SHASUMS256.txt" \
            | awk -v f="$NODE_FNAME" '$2 == f {print $1}')"
    [ -n "$want" ] || { echo "  ⚠ 取不到 $base 的 SHASUMS256.txt, 只做 gzip 校验"; return 0; }
    got="$(shasum -a 256 "$NODE_TMP" | awk '{print $1}')"
    [ "$want" = "$got" ] || { echo "  ✗ sha256 对不上 ($base)"; return 1; }
    echo "  ✓ sha256 与 $base/SHASUMS256.txt 一致"
}
if [ -f "$NODE_TMP" ] && gzip -t "$NODE_TMP" 2>/dev/null; then
    echo "  ↻ 用已下载的 $NODE_TMP"
else
    node_ok=0
    for base in $NODE_MIRRORS; do
        echo "  → $base"
        rm -f "$NODE_TMP"
        if curl -fL --retry 2 --retry-delay 5 --connect-timeout 20 \
                --speed-limit 10240 --speed-time 60 \
                -o "$NODE_TMP" "$base/v${NODE_VERSION}/${NODE_FNAME}" \
            && gzip -t "$NODE_TMP" 2>/dev/null && node_sha_ok "$base"; then
            node_ok=1
            break
        fi
        echo "  ✗ $base 不行, 换下一个"
    done
    [ "$node_ok" = 1 ] || { rm -f "$NODE_TMP"; echo "❌ Node $NODE_VERSION 所有源都下载失败 ($NODE_MIRRORS)"; exit 1; }
fi
