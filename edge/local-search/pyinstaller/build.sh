#!/usr/bin/env bash
# 在 macOS / Linux 上打一个单文件 catfish-search 二进制。
# 产物：dist/catfish-search
#
# 用 Python 3.12 构建（找不到往下退到 3.11 / 3.10）。
# 注意：这个 Python 解释器会被 PyInstaller 塞进二进制里，
# 员工运行时用的就是它，跟员工机器上装没装 Python 无关。
# 所以我们尽量挑新版本，启动更快、markitdown 永远可用、安全补丁最新。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

echo "[1/5] 选 Python"
PYTHON_BIN=""
for bin in python3.12 python3.11 python3.10; do
    if command -v "$bin" >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "$bin")"
        echo "    使用 $PYTHON_BIN ($("$PYTHON_BIN" --version))"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "错误：找不到 Python 3.10+（推荐 3.12）。"
    echo "安装方式："
    echo "    macOS:         brew install python@3.12"
    echo "    Ubuntu 22.04+: sudo apt install python3.12"
    echo "    RHEL 9:        sudo dnf install python3.12"
    exit 1
fi

echo "[2/5] 准备 venv"
VENV_DIR=".build-venv"
# 如果之前的 venv 用的是另一个 Python 版本，重建。
if [ -d "$VENV_DIR" ]; then
    if ! "$VENV_DIR/bin/python" --version 2>&1 | grep -q "$("$PYTHON_BIN" --version | awk '{print $2}' | cut -d. -f1,2)"; then
        echo "    旧 venv Python 版本不同，重建"
        rm -rf "$VENV_DIR"
    fi
fi
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck source=/dev/null
. "$VENV_DIR/bin/activate"

echo "[3/5] 装依赖"
pip install -q -U pip
pip install -q -U pyinstaller pyyaml watchdog 'markitdown[all]'

echo "[4/5] 清 dist / build"
rm -rf dist build

echo "[5/5] PyInstaller 打包"
pyinstaller --clean --noconfirm pyinstaller/catfish-search.spec

BIN="dist/catfish-search"
if [ -f "$BIN" ]; then
    SIZE=$(du -h "$BIN" | awk '{print $1}')
    echo
    echo "完成。"
    echo "产物：$BIN  ($SIZE)"
    echo "内嵌 Python：$("$PYTHON_BIN" --version)"
    echo "验证：$BIN --help"
else
    echo "构建失败，请看上面的 PyInstaller 输出。"
    exit 1
fi
