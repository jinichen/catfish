#!/usr/bin/env bash
# Piper TTS 一键装 (BL-VOICE2 fix2, 5/10).
#
# 鸿波 5/10 mac 踩坑: brew install piper-tts formula 不存在; brew install python3
# 走 127.0.0.1:7890 代理失败; pip3 install piper-tts 被 PEP 668 拦.
#
# 这个脚本走 venv (不依赖代理装新 brew 包, 也绕 PEP 668), 装到 catfish 约定
# 路径 ~/.catfish/piper-venv/. Companion 自动探测.
#
# 用法:
#   bash scripts/install-piper-tts.sh
#
# 跳过下载 voice (如果想自己挑别的):
#   bash scripts/install-piper-tts.sh --no-voice

set -e

VENV_DIR="$HOME/.catfish/piper-venv"
VOICE_DIR="$HOME/.catfish/piper-voices"
DEFAULT_VOICE="zh_CN-huayan-medium"

# ── 0. 检测前置 ──────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ python3 找不到. 装一个:"
    echo "    brew install python3        (需要代理)"
    echo "    或用 pyenv / asdf / xcode-select 装的 python3"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "→ python3 = $(which python3) ($PY_VER)"

# ── 1. 创建 venv ─────────────────────────────
if [ -d "$VENV_DIR" ]; then
    echo "→ venv 已存在: $VENV_DIR (跳过创建)"
else
    echo "→ 创建 venv → $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# ── 2. 装 piper-tts ──────────────────────────
echo "→ 升级 pip + 装 piper-tts (不依赖代理, 走 PyPI 直连)"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install piper-tts || {
    echo
    echo "⚠️  piper-tts 装失败. 试备用包名 (OHF-Voice 维护的 piper1-gpl):"
    "$VENV_DIR/bin/pip" install piper1-gpl || {
        echo "❌ piper1-gpl 也失败. 看上面 pip 报错诊断."
        exit 1
    }
}

# ── 3. 验证 piper 命令 ───────────────────────
PIPER_BIN="$VENV_DIR/bin/piper"
if [ ! -x "$PIPER_BIN" ]; then
    echo "❌ piper 二进制没生成: $PIPER_BIN"
    echo "   ls $VENV_DIR/bin/"
    ls "$VENV_DIR/bin/" | head -20
    exit 1
fi
echo "✓ piper 装好: $PIPER_BIN"
"$PIPER_BIN" --help | head -3 || true

# ── 4. 下载默认 voice 模型 ───────────────────
if [ "$1" = "--no-voice" ]; then
    echo "→ --no-voice 跳过模型下载"
else
    mkdir -p "$VOICE_DIR"
    cd "$VOICE_DIR"

    ONNX="$VOICE_DIR/$DEFAULT_VOICE.onnx"
    JSON="$VOICE_DIR/$DEFAULT_VOICE.onnx.json"

    BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium"

    # BL-VOICE2 fix5 (5/10): size 校验函数 — 防 HuggingFace LFS pointer 假文件
    # (鸿波 5/10 下 high 拿到 15 字节 pointer text, piper 打开报 JSONDecodeError)
    check_voice_size() {
        local f="$1"
        local min_kb="$2"
        local size
        size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f")
        local size_kb=$((size / 1024))
        if [ "$size_kb" -lt "$min_kb" ]; then
            echo "❌ $f size=$size 字节 (< ${min_kb}KB), 像 LFS pointer 不是真模型"
            echo "   头几行内容:"
            head -3 "$f" | sed 's/^/   /'
            return 1
        fi
        echo "✓ $f ($size_kb KB)"
        return 0
    }

    if [ -f "$ONNX" ] && [ -f "$JSON" ] && check_voice_size "$ONNX" 5000 && check_voice_size "$JSON" 1; then
        echo "→ voice 模型已下载且 size 正常, 跳过"
    else
        echo "→ 删旧文件 (可能是 LFS pointer 假文件) 重下"
        rm -f "$ONNX" "$JSON"
        echo "→ 下载默认中文女声 voice (~30MB) → $VOICE_DIR/"
        curl -fL --progress-bar -o "$ONNX" "$BASE/$DEFAULT_VOICE.onnx" || {
            echo "❌ .onnx 下载失败. 检查网络 / curl 代理设置."
            exit 1
        }
        curl -fL --progress-bar -o "$JSON" "$BASE/$DEFAULT_VOICE.onnx.json" || {
            echo "❌ .onnx.json 下载失败."
            exit 1
        }
        # 下完再 size 校验, 防 curl HTTP 200 但内容是 LFS pointer
        check_voice_size "$ONNX" 5000 || { echo "→ 真模型应该 ~30MB, 重下"; exit 1; }
        check_voice_size "$JSON" 1 || exit 1
    fi
fi

# ── 5. 试合成一句话 ──────────────────────────
echo
echo "→ 试合成一句中文 → /tmp/catfish-piper-test.wav"
echo "你好，我是鲶鱼，很高兴见到你。" | "$PIPER_BIN" \
    -m "$VOICE_DIR/$DEFAULT_VOICE.onnx" \
    -f /tmp/catfish-piper-test.wav 2>&1 | tail -5 || {
    echo "⚠️  合成失败. 看 piper 输出."
    exit 1
}

if [ -f /tmp/catfish-piper-test.wav ]; then
    SIZE=$(stat -f%z /tmp/catfish-piper-test.wav 2>/dev/null || stat -c%s /tmp/catfish-piper-test.wav)
    echo "✓ /tmp/catfish-piper-test.wav 生成 ($SIZE bytes)"
    if command -v afplay &>/dev/null; then
        echo "→ 播放试听 (3 秒后):"
        sleep 1
        afplay /tmp/catfish-piper-test.wav &
    fi
fi

# ── 完工 ─────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════"
echo "✅ Piper TTS 装完"
echo "════════════════════════════════════════════════════════"
echo
echo "  piper:   $PIPER_BIN"
echo "  voice:   $VOICE_DIR/$DEFAULT_VOICE.onnx (~30MB)"
echo
echo "Companion 会自动探测 ~/.catfish/piper-venv/bin/piper, 不需要"
echo "改 yaml 或设 env. 重启 Companion → AI 回答右下角 🔊 按钮 → 鲶鱼说话."
echo
echo "想换男声 (bizhao) 或其他 voice:"
echo "  cd $VOICE_DIR"
echo "  curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/bizhao/medium/zh_CN-bizhao-medium.onnx"
echo "  curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/bizhao/medium/zh_CN-bizhao-medium.onnx.json"
echo "  在 ~/.catfish/companion.yaml 加 tts.voice: zh_CN-bizhao-medium"
