#!/bin/bash
# 打包脚本共用的下载 helper (10/14 从 build-mac-resources.sh 抽出来, build-windows-hermes-resources.sh 也用)。
# 用法: . scripts/lib-download.sh ; download_with_retry <url> <输出文件>
# ─── 下载 helper · retry + gzip verify + ghfast.top 代理 ─────
# 镜像 build-intel-dmg.sh 里的 download_with_retry (7/16), 修 uv 下载 truncated 挂.
# 国内下 GitHub Release 慢/易断 · ghfast.top 加速. 国外可 GH_PROXY="" 直连.
# `-` 不是 `:-`: 显式设成空 (GH_PROXY="") 才能直连 —— 原来 `:-` 把空当没设, 注释说的直连从没生效过
GH_PROXY="${GH_PROXY-https://ghfast.top/}"

# 10/1: 代理环境变量指向本机一个没人监听的端口 (鸿波 ~/.zshrc 里写的是 7890, Clash 实际
# 在 7897) 时, curl 每次都 "Failed to connect to 127.0.0.1 port 7890" 秒挂, 10 次全白试。
# 开下之前先探一下, 死端口就提示并直接走直连。
proxy_env_dead() {
    local p="${HTTPS_PROXY:-${https_proxy:-${ALL_PROXY:-${all_proxy:-}}}}"
    [ -n "$p" ] || return 1
    local hostport="${p#*://}"; hostport="${hostport%%/*}"; hostport="${hostport##*@}"
    local host="${hostport%:*}" port="${hostport##*:}"
    case "$host" in 127.0.0.1|localhost) ;; *) return 1 ;; esac
    [ "$port" != "$hostport" ] || return 1
    command -v nc >/dev/null 2>&1 || return 1   # Windows Git Bash 没有 nc: 不判, 照常走代理
    ! nc -z -G 2 "$host" "$port" >/dev/null 2>&1
}

_dl_curl() {  # $1=url $2=output, 其余参数原样给 curl
    local u="$1" o="$2"; shift 2
    # -C - 断点续传, --retry 3 内部小 retry, --retry-max-time 15 min
    # --speed-limit/--speed-time: 连上了但 60 秒平均不到 10KB/s 就算这次失败,
    # 交给外面的重试。没有这两个参数时, 挂住的连接会让 curl 永远等下去 (9/23 实测)。
    curl -fL --retry 3 --retry-delay 5 --retry-max-time 900 --continue-at - \
        --connect-timeout 20 --speed-limit 10240 --speed-time 60 \
        "$@" -o "$o" "$u"
}

download_with_retry() {
    local url="$1"
    local output="$2"
    local url_proxied="${GH_PROXY}${url}"
    local direct=""

    if proxy_env_dead; then
        echo "  ⚠ 代理环境变量指向的本机端口没人监听: ${HTTPS_PROXY:-${https_proxy:-${ALL_PROXY:-${all_proxy:-}}}}"
        echo "    (检查 ~/.zshrc 里的 HTTPS_PROXY 端口跟代理软件实际端口是否一致) —— 这次先不走代理"
        direct=1
    fi

    # 每次删旧 · 避免坏 cache 复用
    rm -f "$output"

    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        echo "  [attempt $attempt/10] $output"
        if if [ -n "$direct" ]; then _dl_curl "$url_proxied" "$output" --noproxy '*'; else _dl_curl "$url_proxied" "$output"; fi; then
            # verify gzip 完整性 (若 tar.gz)
            if [[ "$output" == *.gz ]] || [[ "$output" == *.tar.gz ]]; then
                if gzip -t "$output" 2>/dev/null; then
                    echo "  ✓ 下载完整 (gzip verify OK · $(ls -lh "$output" | awk '{print $5}'))"
                    return 0
                else
                    echo "  ✗ tar 坏 · 删了重下"
                    rm -f "$output"
                fi
            else
                echo "  ✓ 下载完整 ($(ls -lh "$output" | awk '{print $5}'))"
                return 0
            fi
        fi
        # 第 5 次后换直连: 原地址 + 不走任何代理。10/1 前只是去掉 ghfast 前缀,
        # curl 照样读 HTTPS_PROXY 环境变量, 日志写"直连"其实还在走代理。
        if [ $attempt -eq 5 ]; then
            echo "  switch to direct (原地址, 不走任何代理)"
            url_proxied="$url"
            direct=1
        fi
        sleep 10
    done
    echo "!!! 下 10 次都挂: $url"
    return 1
}

