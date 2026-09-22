#!/usr/bin/env bash
#
# delivery/*/images/ 里不许留 image tar。
#
# ── 为什么需要这一条 ────────────────────────────────────────────────
#
# 9/22 发现: images/ 里躺着 8/29 的 15 个 .tar, 合计 3.9GB。
#
# 打包时会把整个 delivery/catfish-poc/ 打进 FULL tar, 所以**从 8/29 起每一个
# 交付包都白白多带了这 3.9GB** (压缩后约 1GB)。9/22 那个 1.7G 的包就是这么来的。
#
# 为什么三周多没人发现:
#
#   · 清理规则写的是 `rm -f images/*.tar.gz` —— 只匹配 .tar.gz, 而那些是 .tar
#   · delivery/.gitignore 把 images/*.tar 排掉了 → git status 永远干净
#   · 包大了一倍没人觉得奇怪 —— 镜像本来就大, 多 1GB 看不出来
#
# 三条加起来: **这件事没有任何外部迹象, 只有人主动 ls 才看得见。**
# 所以加一条主动去看的检查。
#
# 根因已经修了 (打包前先清空, 且两种后缀都清), 这条守的是结果。
#
# 退出码: 0 = 干净 / 1 = 有残留
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

found=0
while IFS= read -r d; do
    n=$(find "$d" -maxdepth 1 -type f \( -name '*.tar' -o -name '*.tar.gz' \) | wc -l | tr -d ' ')
    [ "$n" -eq 0 ] && continue
    if [ "$found" -eq 0 ]; then
        echo "❌ 交付目录的 images/ 里有 image tar 残留 —— 它们会被打进交付包:"
        echo ""
    fi
    sz=$(du -sh "$d" 2>/dev/null | cut -f1)
    echo "   $d  ($n 个文件, $sz)"
    find "$d" -maxdepth 1 -type f \( -name '*.tar' -o -name '*.tar.gz' \) \
        -exec ls -lh {} \; | awk '{print "     "$5"  "$9}'
    found=1
done < <(find delivery -type d -name images 2>/dev/null)

if [ "$found" -eq 1 ]; then
    cat <<'MSG'

   打包时整个 delivery/<包名>/ 会被打进 FULL tar, 所以这些残留会**原样跟着发给客户**。

   它们是打包过程的中间产物, 不该长期躺在这儿。删掉:
       find delivery -type d -name images -exec sh -c \
         'find "$1" -maxdepth 1 -type f \( -name "*.tar" -o -name "*.tar.gz" \) -delete' _ {} \;

   ⚠ delivery/.gitignore 把它们排掉了, 所以 git status 看不见 ——
     这正是 8/29 到 9/22 那三周多没人发现的原因。
MSG
    exit 1
fi

echo "✓ 交付目录的 images/ 干净"
