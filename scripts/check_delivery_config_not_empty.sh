#!/usr/bin/env bash
#
# 交付包里的 gateway 配置不能是空的。
#
# ── 为什么需要这一条 ────────────────────────────────────────────────
#
# 9/22 打包时真实发生过: config-overlay 里的三个文件被改成"带说明的空模板",
# 而 overlay 机制不看内容, 只要文件在就整份 rsync 到 config/ 上 —— 于是
# **整份 baseline 被注释文件冲掉**:
#
#     models.yaml    6 个 model  →  0 个
#     mcp_registry / skills_hub / wiki_hub 三段上游  →  没了
#
# 而包照样打完、照样报成功。装上去 gateway 起得来、门户打得开、登录正常,
# 只有员工一发消息才会发现没有任何模型可用 —— 又一个"绿灯下的坏结果"。
#
# 根因已经修掉 (模板存成 *.example, overlay 默认为空), 但那只是修了这一次。
# 这条检查守的是**结果**: 不管中间经过什么机制, 交付出去的 config 必须有内容。
#
# 退出码: 0 = OK / 1 = 空了
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CFG="delivery/catfish-poc/llm-gateway/config/models.yaml"
if [ ! -f "$CFG" ]; then
    echo "⚠ $CFG 不存在 —— 它是打包时从 central/ 合成的生成物, 本地没有是正常的。"
    echo "  跳过检查。(打包脚本里另有一道同样的校验, 见 build-package.sh)"
    exit 0
fi

python3 - "$CFG" <<'PY'
import sys, yaml
path = sys.argv[1]
try:
    d = yaml.safe_load(open(path)) or {}
except yaml.YAMLError as e:
    print(f"❌ {path} 解 YAML 失败: {e}")
    sys.exit(1)

fail = 0
models = d.get("models") or []
if not models:
    print(f"❌ {path} 里一个 model 都没有。")
    print("   最可能的原因: config-overlay 里有文件把 baseline 整份覆盖了。")
    print("   overlay 的模板应该叫 *.example (不参与覆盖) —— 见 9/22 那次。")
    fail = 1
else:
    print(f"  ✓ {len(models)} 个 model")

# 这三段是任何 docker 部署都必需的: 容器里 127.0.0.1 指向 gateway 自己,
# 不走 compose 服务名就是 502, 而症状只在「系统管理」页显示"不可达"。
for key, want in (("mcp_registry", "mcp-registry"),
                  ("skills_hub", "skills-hub"),
                  ("wiki_hub", "wiki-hub")):
    url = (d.get(key) or {}).get("upstream_url", "")
    if not url:
        print(f"❌ 缺 {key}.upstream_url —— 装完「系统管理」页会显示不可达")
        fail = 1
    elif want not in url:
        print(f"❌ {key}.upstream_url = {url} · 应指向 compose 服务名 {want}")
        print("   写 127.0.0.1 的话容器里指向 gateway 自己 → 502")
        fail = 1

sys.exit(fail)
PY
echo "✓ 交付 config 有内容"
