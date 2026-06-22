#!/usr/bin/env bash
# Deploy catfish-bookkeep plugin: 软链到 ~/.hermes/plugins/ + ensure config.yaml enabled.
# 跟 catfish-xcatfish-user/deploy.sh 同 pattern.
#
# P3.5.75 (6/22 鸿波): 本地语音记账 plugin.
set -euo pipefail

SRC_DIR="$HOME/person_task/catfish/edge/hermes-plugins/catfish-bookkeep"
HERMES_PLUGINS_DIR="$HOME/.hermes/plugins"
HERMES_CONFIG="$HOME/.hermes/config.yaml"
TARGET="$HERMES_PLUGINS_DIR/catfish-bookkeep"
PLUGIN_NAME="catfish-bookkeep"

echo "================================================================"
echo "  Deploy $PLUGIN_NAME plugin (P3.5.75)"
echo "================================================================"

# 1. 源目录在
if [[ ! -d "$SRC_DIR" ]]; then
    echo "✗ plugin 源目录不存在: $SRC_DIR"
    exit 1
fi
if [[ ! -f "$SRC_DIR/__init__.py" ]] || [[ ! -f "$SRC_DIR/bookkeep.py" ]]; then
    echo "✗ plugin 源目录缺文件 (__init__.py / bookkeep.py)"
    exit 1
fi

# 2. ~/.hermes/plugins 存在
mkdir -p "$HERMES_PLUGINS_DIR"

# 3. 幂等软链
if [[ -L "$TARGET" ]]; then
    cur="$(readlink "$TARGET")"
    if [[ "$cur" == "$SRC_DIR" ]]; then
        echo "✓ 软链已正确, 跳过: $TARGET → $SRC_DIR"
    else
        echo "→ 软链指向不对 ($cur), 重建"
        rm "$TARGET"
        ln -s "$SRC_DIR" "$TARGET"
        echo "✓ 重建: $TARGET → $SRC_DIR"
    fi
elif [[ -d "$TARGET" ]]; then
    echo "✗ $TARGET 是真目录不是软链, 手动处理: rm -rf $TARGET 后重跑"
    exit 1
else
    ln -s "$SRC_DIR" "$TARGET"
    echo "✓ 软链已建: $TARGET → $SRC_DIR"
fi

# 4. 跑 unit test 自检 (hermes venv 不需要 — bookkeep.py 0 外部依赖, 用 system python3)
echo ""
echo "→ 跑 plugin unit tests ..."
PY_BIN="$HOME/.hermes/hermes-agent/venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
    PY_BIN="$(command -v python3 || true)"
fi
if [[ -x "$PY_BIN" ]]; then
    if "$PY_BIN" -m pytest "$SRC_DIR/tests/" -q 2>&1 | tail -10; then
        echo "✓ unit tests 通过"
    else
        echo "✗ unit tests 失败 — 看上面 traceback, 修代码再重跑"
        exit 1
    fi
else
    echo "⚠ 没找到 python3, 跳过 unit test (不影响装载)"
fi

# 5. ensure config.yaml plugins.enabled 含 catfish-bookkeep
echo ""
echo "→ ensure $HERMES_CONFIG plugins.enabled 含 $PLUGIN_NAME ..."
mkdir -p "$(dirname "$HERMES_CONFIG")"
touch "$HERMES_CONFIG"

CFG_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
if [[ ! -x "$CFG_PY" ]]; then
    CFG_PY="$(command -v python3 || true)"
fi

if [[ -x "$CFG_PY" ]]; then
    "$CFG_PY" - "$HERMES_CONFIG" "$PLUGIN_NAME" <<'PYEOF'
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("    ⚠ pyyaml 没装, 跳过 config.yaml 自动改. 手动编辑 ~/.hermes/config.yaml:")
    print("        plugins:")
    print("          enabled:")
    print("          - catfish-bookkeep")
    sys.exit(0)

cfg = Path(sys.argv[1])
plugin = sys.argv[2]
text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict):
    data = {}

plugins = data.get("plugins") or {}
if not isinstance(plugins, dict):
    plugins = {}
enabled = plugins.get("enabled") or []
if not isinstance(enabled, list):
    enabled = []
disabled = plugins.get("disabled") or []
if not isinstance(disabled, list):
    disabled = []

# 从 disabled 移除 (若在那)
if plugin in disabled:
    disabled = [p for p in disabled if p != plugin]
    print(f"    从 plugins.disabled 移除 {plugin}")

# 加到 enabled (幂等)
if plugin in enabled:
    print(f"    ✓ {plugin} 已在 plugins.enabled, 不变")
else:
    enabled.append(plugin)
    print(f"    ✓ 加 {plugin} 到 plugins.enabled")
    # 备份
    backup = cfg.with_suffix(cfg.suffix + ".bak.bookkeep")
    backup.write_text(text, encoding="utf-8")
    print(f"    备份: {backup}")

plugins["enabled"] = enabled
plugins["disabled"] = disabled
data["plugins"] = plugins

cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
PYEOF
else
    echo "    ⚠ 找不到 python, 手动编辑 ~/.hermes/config.yaml 加 catfish-bookkeep 到 plugins.enabled"
fi

# 6. 完成提示
cat <<EOF

================================================================
  Deploy 完成 ✓

  1. 重启 hermes 让 plugin 加载:
       launchctl kickstart -k gui/\$(id -u)/ai.hermes.gateway

  2. 看启动 log 验证 plugin 注册:
       grep 'catfish-bookkeep' ~/.hermes/logs/agent.log | tail -5
     期望:
       catfish-bookkeep tool registered: 💸 bookkeep_add
       catfish-bookkeep tool registered: 🔍 bookkeep_query
       catfish-bookkeep tool registered: 📊 bookkeep_summarize
       catfish-bookkeep plugin registered ✓ (3/3 tools, ...)

  3. 真试 (Companion chat 🎤 语音或文字):
       "今天买了 5 块煎饼"
     期望: LLM 调 bookkeep_add → ~/.catfish/bookkeep.jsonl 落一条

       "我这周花了多少"
     期望: LLM 调 bookkeep_query → 列最近 7 天支出

       "六月账单"
     期望: LLM 调 bookkeep_summarize → 出汇总报表

  4. 看落账:
       cat ~/.catfish/bookkeep.jsonl
EOF
