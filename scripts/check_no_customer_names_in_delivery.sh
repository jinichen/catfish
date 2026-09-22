#!/usr/bin/env bash
#
# 交付目录里不许出现客户名。
#
# 9/22 鸿波: "现在开始打包不要再提属于那个客户，统一用 catfish 输出"。
#
# 为什么需要一道自动检查, 而不是改完就算:
#
#   这次清理出来的东西, 绝大多数不是有人**决定**要写客户名, 而是排障时
#   顺手记下的现场笔记 —— "7/29 XX 现场装完才发现"。写的时候它在仓库里,
#   完全合理; 可 delivery/ 这棵树会被打进 tar 发给**下一家**客户, 于是
#   A 客户拆开包就能读到我们在 B 客户那儿踩过什么坑。
#
#   这种事不会被 code review 抓住 —— 注释里多一个专有名词, 看上去就是
#   一条正常的注释。只能靠机器每次都扫一遍。
#
# 扫哪里:
#   delivery/            整棵树 (config-overlay 也算 —— 它虽然不进 tar,
#                        但它是下一家客户配置的起点, 脏了会被抄过去)
#   central/deploy.*     客户 IT 直接运行的部署脚本
#
# 加新客户时: 把名字加进 FORBIDDEN, 一次就好。
#
# 退出码: 0 = 干净 / 1 = 命中 / 2 = 脚本自身有问题
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 客户名 / 客户域名 / 客户专属标识。
# 大小写不敏感匹配, 所以 DAHUA / Dahua / dahua 写一条就够。
FORBIDDEN=(
  "达华"
  "dahua"
)

# 扫描范围。二进制和生成物排掉 —— images/ 里是 docker save 出来的 tar,
# 几百 MB, 而且内容是镜像层不是我们写的文字。
SCAN_PATHS=(
  "delivery"
  "central/deploy.sh"
  "central/deploy.ps1"
)

EXCLUDE_DIRS=("images" ".git" "node_modules" "certs")

hits=0
for name in "${FORBIDDEN[@]}"; do
    for path in "${SCAN_PATHS[@]}"; do
        [ -e "$path" ] || continue
        args=(-rniI --binary-files=without-match)
        for d in "${EXCLUDE_DIRS[@]}"; do args+=(--exclude-dir="$d"); done
        # grep 没命中返回 1, 在 set -e 下会终止脚本 —— 所以显式兜住。
        found="$(grep "${args[@]}" -- "$name" "$path" 2>/dev/null || true)"
        if [ -n "$found" ]; then
            if [ "$hits" -eq 0 ]; then
                echo "❌ 交付目录里出现了客户名 —— 这些内容会打进 tar 发给别的客户:"
                echo ""
            fi
            echo "$found" | sed 's/^/   /'
            hits=$((hits + 1))
        fi
    done
done

if [ "$hits" -gt 0 ]; then
    cat <<'EOF'

怎么改:

  · 注释里的现场记录 —— 技术内容留着, 把客户名去掉就行:
        「7/29 XX 现场装完才发现」→「7/29 现场装完才发现」
    排障线索一个字没少, 客户是谁没了。

  · 域名 / 邮箱 / 密码示例 —— 换占位符:
        catfish.<客户域名>  ·  admin@example.com  ·  改成你的强密码

  · 客户专属的配置内容 (模型/部门/额度) —— 那是 config-overlay 的事,
    而 overlay 默认该是空模板。别把某一家的选择写死成所有人的默认。

  · 确实需要按客户区分的, 走 CATFISH_CUSTOMER 这类 env, 不要写死。

放行某一处 (很少需要, 想清楚再用):
  在那一行末尾加注释  # allow-customer-name
  —— 目前脚本没实现这个开关, 真遇到了再加, 免得先给自己留后门。
EOF
    exit 1
fi

echo "✓ 交付目录里没有客户名"
