#!/usr/bin/env bash
# 卸载 catfish Hermes 插件 catfish-autocompress.
#
# P3.5.17 (6/17 鸿波): catfish-autocompress 退役 — hermes 自带 ContextCompressor
# 已 cover. 这个脚本仍然有用 —— 清掉老员工机器上残留的 dangling 软链 +
# 把 config.yaml engine 字段从 catfish-autocompress 改回 compressor (hermes 默认).
#
# 做两件事：
#   1. 把 ~/.hermes/config.yaml 的 context.engine 改回默认 (compressor)
#   2. 删软链 ~/.hermes/hermes-agent/plugins/context_engine/catfish-autocompress

set -euo pipefail

HERMES_CONFIG="$HOME/.hermes/config.yaml"
LINK="$HOME/.hermes/hermes-agent/plugins/context_engine/catfish-autocompress"

echo "=== catfish Hermes 插件 uninstaller ==="

# 1. config.yaml 改回默认 engine
if [ -f "$HERMES_CONFIG" ]; then
    python3 - "$HERMES_CONFIG" <<'PYEOF' 2>/dev/null || echo "    (config 清理遇到错误，请手动检查)"
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("    pyyaml 不在，跳过 config 清理。")
    sys.exit(0)

cfg = Path(sys.argv[1])
text = cfg.read_text(encoding="utf-8")
data = yaml.safe_load(text) or {}
if not isinstance(data, dict):
    print("    config 格式异常")
    sys.exit(0)

ctx = data.get("context") or {}
if not isinstance(ctx, dict):
    sys.exit(0)
if ctx.get("engine") == "catfish-autocompress":
    del ctx["engine"]
    if ctx:
        data["context"] = ctx
    else:
        data.pop("context", None)
    cfg.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print("    已从 config.yaml 删除 context.engine（回到默认 ContextCompressor）")
else:
    print(f"    context.engine 不是 catfish-autocompress（当前：{ctx.get('engine', '默认')}），无需改动")
PYEOF
fi

# 2. 删软链
if [ -L "$LINK" ]; then
    rm "$LINK"
    echo "    已删软链 $LINK"
elif [ -d "$LINK" ]; then
    echo "    $LINK 是实际目录不是软链，不敢删。请手动处理。"
else
    echo "    $LINK 不存在，跳过"
fi

echo
echo "=== 已卸载 ==="
echo "重启 hermes 后 Hermes 回到默认 ContextCompressor (P3.5.17 走 50% 触发, see ~/.hermes/config.yaml compression.threshold)"
