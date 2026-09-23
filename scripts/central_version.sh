#!/usr/bin/env bash
#
# 中央端六个镜像的版本号 —— **算出来的, 不是人填的**。
#
#     格式:  YYYYMMDD-<8位内容哈希>
#     例子:  20260922-3f9a1c04
#
# ── 为什么不用 semver ────────────────────────────────────────────────
#
# 9/12 (020f172) 把六个镜像统一钉成 0.1.2。从那之后**每一次交付都还是
# 0.1.2** —— 不是有人偷懒, 是因为"这次算 patch 还是 minor"需要人判断,
# 而需要判断的事在交付前的兵荒马乱里就会被跳过。
#
# semver 的价值在于"我能选择停在哪一版": 大版本不兼容 / 小版本加功能 /
# 补丁位修 bug, 这套语义是给**要在版本之间做选择**的人用的。而我们不做
# 回滚 (9/22 鸿波定的), 永远只跑最新 —— 那套语义一条都用不上。
#
# 剩下的唯一职责是: **告诉我这坨镜像是哪次构建出来的。** 干这件事,
# 一个推导出来的标识比一个要人填的数字可靠得多, 因为它没有"忘了改"这种
# 可能。
#
# ── 哈希什么, 为什么 ──────────────────────────────────────────────
#
# 哈希**六个镜像源码目录里所有文件的内容**。不是整仓 SHA, 也不是 central/
# 整个目录:
#
#   · 整仓 SHA: edge/ (Companion / hermes 插件) 一提交就变, 而交付包里
#     一个字节都没动。rebuild-from-scratch-0720.sh 里那段注释记着 8/1
#     当天就因为这个改过一次 —— "两个平台不是同一次代码"喊几次狼来了
#     之后就没人看了。
#
#   · central/ 整个目录: **会循环**。central/VERSION 和
#     central/docker-compose.yml 都在里面, 写版本号本身就会改变哈希,
#     于是永远算不出一个稳定的值。
#
#   · 六个源码目录: 不含版本文件和 compose, 而且"哈希变了"恰好等价于
#     "镜像内容可能不同" —— 这正是我们要 tag 回答的问题。
#
# 用文件内容而不是 git tree id 来算, 是为了**不依赖提交状态**: 改完还没
# commit 时也能算, 检查脚本和构建脚本看到的是同一个值。文件清单来自
# `git ls-files` (见下面那段 —— 自己维护排除清单那条路试过, 当场漏了)。
#
# 日期放前面是为了**能排序、能说人话**("9 月 22 号那版")。同一份代码隔天
# 重打会得到不同 tag —— 这是有意的: 那确实是两个不同的交付包 (tar 不一样、
# BUILD-INFO 不一样); 而镜像内容相同时 docker load 只是给同一个镜像多挂
# 一个 tag, 不占额外空间。
#
# ── 用法 ──────────────────────────────────────────────────────────
#
#   bash scripts/central_version.sh            # 打印当前应有的版本号
#   bash scripts/central_version.sh --hash     # 只打印内容哈希 (给检查脚本)
#   bash scripts/central_version.sh --write    # 写进 VERSION + 两份 compose
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION_FILE="central/VERSION"
COMPOSES=("central/docker-compose.yml" "delivery/catfish-poc/docker-compose.yml")
IMAGES=(catfish-identity catfish-gateway catfish-skills-hub catfish-web catfish-mcp-registry catfish-wiki-hub)

# 六个镜像的源码目录。顺序无所谓 (下面 sort), 但**集合**要跟实际构建的一致 ——
# 新增镜像时这里漏加, 那个镜像改了版本号不会变, 而这正是要防的事。
SRC_DIRS=(
  central/identity-server
  central/llm-gateway
  central/skills-hub
  central/web
  central/mcp-registry
  central/wiki-hub
)

# ── 哪些文件算"源码" ────────────────────────────────────────────
#
# 用 `git ls-files`, 不自己维护排除清单。
#
# 第一版是 find + 一张 PRUNE 清单 (node_modules / __pycache__ / .venv ...),
# 当场被打脸: 实际目录叫 `.venv-sandbox`, 不匹配 `.venv`, 于是 5707 个文件
# 里 5042 个是虚拟环境 —— 哈希完全被生成物主导, 改一行真代码它都未必动。
#
# 维护排除清单这条路**结构上就是错的**: 它永远漏一个, 而漏了没有任何迹象。
# git 已经替我们维护了一份"什么是源码"的定义 (.gitignore), 直接用它。
#
# ⚠ 代价: 未被 track 的文件不算进哈希, 但 docker build 的上下文**会**包含
#   它们。所以下面显式检查并报出来 —— 那种文件本身就是异常, 该有人知道。
file_list() {
    local d
    for d in "${SRC_DIRS[@]}"; do
        [ -d "$d" ] || { echo "❌ 镜像源码目录不存在: $d" >&2
                         echo "   目录改名/新增镜像时要同步改本脚本的 SRC_DIRS。" >&2
                         exit 1; }
        # 9/23: 排掉"已在工作区删除、还没 git rm"的文件 —— 它们不进 docker 构建
        # 上下文, 而且 sha256sum 读不到会打一行错继续算, 哈希就悄悄变成了另一个值。
        # (那天一次 git stash / pop 把几个删除从暂存区弹回了工作区, 撞上的。)
        comm -23 <(git ls-files -- "$d" | LC_ALL=C sort) \
                 <(git ls-files --deleted -- "$d" | LC_ALL=C sort)
    done | LC_ALL=C sort
}

# 没 track 但也没被 ignore 的文件 —— 会进 docker 构建上下文, 却不进哈希。
untracked_warn() {
    local u
    u="$(git ls-files --others --exclude-standard -- "${SRC_DIRS[@]}" || true)"
    [ -z "$u" ] && return 0
    echo "⚠ 镜像源码目录里有未提交、也未被 ignore 的文件:" >&2
    echo "$u" | sed 's/^/     /' >&2
    echo "  它们会进 docker 构建上下文, 但不进版本哈希 —— 也就是说镜像可能" >&2
    echo "  变了而版本号没变。提交它们, 或加进 .gitignore。" >&2
}

content_hash() {
    # 把"路径 + 内容"一起哈希: 只哈希内容的话, 把一个文件改名成另一个名字
    # 哈希不变, 但镜像里的东西变了。
    file_list | while IFS= read -r f; do
        printf '%s\0' "$f"
        sha256sum "$f" | cut -d' ' -f1
    done | sha256sum | cut -c1-8
}

compute() { printf '%s-%s\n' "$(date +%Y%m%d)" "$(content_hash)"; }

untracked_warn

case "${1:---print}" in
    --print) compute ;;
    --hash)  content_hash ;;
    --files) file_list ;;   # 排查用: 到底哈希了哪些文件
    --write)
        v="$(compute)"
        printf '%s\n' "$v" > "$VERSION_FILE"
        for f in "${COMPOSES[@]}"; do
            [ -f "$f" ] || { echo "❌ 找不到 $f"; exit 1; }
            for img in "${IMAGES[@]}"; do
                # 只动 `image: <名字>:<tag>` 这一处, 不碰别的
                sed -i.bak -E "s|(image:[[:space:]]+${img}):[^[:space:]]+|\1:${v}|" "$f"
            done
            rm -f "$f.bak"
        done
        echo "✓ 版本号 = $v"
        echo "  已写入 $VERSION_FILE 和两份 compose 的 ${#IMAGES[@]} 个 tag"
        echo "  记得把这些改动一起提交。"
        ;;
    *)  echo "用法: $0 [--print | --hash | --files | --write]" >&2; exit 2 ;;
esac
