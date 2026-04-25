#!/usr/bin/env bash
# 把 catfish 命令装到 ~/.local/bin/catfish。
# 软链方式，改 catfish 脚本立即生效。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/catfish"
DST="$HOME/.local/bin/catfish"

GREEN='\033[32m'; YELLOW='\033[33m'; RESET='\033[0m'

mkdir -p "$(dirname "$DST")"
chmod +x "$SRC"
ln -sf "$SRC" "$DST"
echo -e "    ${GREEN}OK${RESET} $DST -> $SRC"

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
        echo -e "    ${YELLOW}注意${RESET} ~/.local/bin 不在 PATH，加到 ~/.zshrc："
        echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo "        source ~/.zshrc"
        ;;
esac

cat <<'EOF'

=== 装好了 ===

试一下：
    catfish version
    catfish status
    catfish                    # 进入鲶鱼平台（带 banner 的 hermes）
    catfish --no-banner        # 跳过 banner

EOF
