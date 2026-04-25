#!/usr/bin/env bash
# 把 Hermes 的 browser 模式切回"local Chromium"（卸载 attach 配置）。
# 只动 ~/.hermes/config.yaml 的 browser.cdp_url 键，不关你的 Chrome。

set -euo pipefail
HERMES_CONFIG="$HOME/.hermes/config.yaml"

if [ ! -f "$HERMES_CONFIG" ]; then
    echo "$HERMES_CONFIG 不存在，无需处理。"
    exit 0
fi

python3 - "$HERMES_CONFIG" <<'PYEOF'
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("需要 pyyaml，跳过。或者手动编辑 config.yaml 删掉 browser.cdp_url。")
    sys.exit(1)

cfg = Path(sys.argv[1])
text = cfg.read_text(encoding="utf-8")
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict):
    print("config.yaml 格式异常，手动检查")
    sys.exit(2)

browser = data.get("browser") or {}
if isinstance(browser, dict) and "cdp_url" in browser:
    removed = browser.pop("cdp_url")
    if browser:
        data["browser"] = browser
    else:
        data.pop("browser", None)
    cfg.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"已删除 browser.cdp_url（原值：{removed[:60]}...）")
    print("Hermes 下次 browser_navigate 会回到 local Chromium 模式。")
else:
    print("未找到 browser.cdp_url，无需处理。")
PYEOF

echo
echo "你的 Chrome 没被动。要是想让 Chrome 也关掉调试端口，自己重启一下 Chrome 即可。"
