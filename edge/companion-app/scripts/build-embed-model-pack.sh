#!/usr/bin/env bash
# 打向量模型组件包 embed-model-<版本>-any.tar.gz (10/6)。
#
#   bash scripts/build-embed-model-pack.sh [版本号, 默认 1.0.0] [输出目录, 默认 ~/catfish-components] [平台 any|windows-x64, 默认 any]
#
# 包里 (文件名跟 src-tauri/src/services/embed_model.rs 的 ONNX_IN_PACK / TOKENIZER_IN_PACK 对齐):
#   bge-m3.onnx     Xenova/bge-m3 的 onnx/model_quantized.onnx (INT8, ~570MB)
#   tokenizer.json  同仓库 tokenizer.json
#   pack.json       { version, model, files }
#
# 来源: 本机 ~/.catfish/models/ 里已有的就直接用 (跟 Companion 默认加载路径一致);
# 没有就从 HuggingFace 下 (需要能出网, 走 HTTPS_PROXY 如果有)。
#
# 平台 any = 纯模型文件 (Apple 芯片 Mac); windows-x64 = 模型 + 微软 onnxruntime.dll ×2。
# 产物放到中央组件目录后跑
# delivery/catfish-poc/tools/build_component_manifest.py 生成清单, Companion 就能下载。
set -euo pipefail

VERSION="${1:-1.0.0}"
OUT_DIR="${2:-$HOME/catfish-components}"
# any (Apple 芯片 Mac, 纯模型) | windows-x64 (多带微软 ONNX Runtime 的两个 DLL,
# 因为 Windows 上 ort 走 load-dynamic —— 见 src-tauri/Cargo.toml target 表)
PLATFORM="${3:-any}"
SRC_DIR="${CATFISH_EMBED_MODEL_DIR:-$HOME/.catfish/models}"
HF_BASE="https://huggingface.co/Xenova/bge-m3/resolve/main"
# 要跟 ort-sys 2.0.0-rc.12 对应的 ORT 版本一致 (它的 dist.txt 写 ms@1.24.2)
ORT_VERSION="${ORT_VERSION:-1.24.2}"
ORT_WIN_ZIP="https://github.com/microsoft/onnxruntime/releases/download/v$ORT_VERSION/onnxruntime-win-x64-$ORT_VERSION.zip"

case "$VERSION" in
  *[!0-9.]*|"") echo "✗ 版本号只能是 x.y.z: $VERSION" >&2; exit 1 ;;
esac
case "$PLATFORM" in
  any|windows-x64) ;;
  *) echo "✗ 平台只能是 any 或 windows-x64: $PLATFORM" >&2; exit 1 ;;
esac

mkdir -p "$OUT_DIR" "$SRC_DIR"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

fetch() { # fetch <本机文件名> <HF 路径>
  local name="$1" remote="$2"
  if [[ -s "$SRC_DIR/$name" ]]; then
    echo "→ 用本机 $SRC_DIR/$name"
  else
    echo "→ 下载 $HF_BASE/$remote → $SRC_DIR/$name"
    curl -L --fail --retry 3 -o "$SRC_DIR/$name.part" "$HF_BASE/$remote"
    mv "$SRC_DIR/$name.part" "$SRC_DIR/$name"
  fi
  cp "$SRC_DIR/$name" "$STAGE/$name"
}

fetch bge-m3.onnx onnx/model_quantized.onnx
fetch tokenizer.json tokenizer.json

# 最低限度的自检: onnx 文件得是 protobuf (前几个字节不是 HTML 错误页), tokenizer 得是 JSON
if head -c 200 "$STAGE/bge-m3.onnx" | grep -qi '<html'; then
  echo "✗ bge-m3.onnx 看起来是个 HTML 页面 (下载被拦了?), 不打包" >&2; exit 1
fi
python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$STAGE/tokenizer.json"

FILES=(bge-m3.onnx tokenizer.json)
if [[ "$PLATFORM" == "windows-x64" ]]; then
  # 微软官方 zip 的 lib/ 里取两个 DLL (CPU 推理只需要 onnxruntime.dll, providers_shared
  # 是它启动时会找的伴侣, 一起带上免得 Windows 报缺 DLL)
  ZIP_CACHE="$SRC_DIR/onnxruntime-win-x64-$ORT_VERSION.zip"
  if [[ ! -s "$ZIP_CACHE" ]]; then
    echo "→ 下载 $ORT_WIN_ZIP"
    curl -L --fail --retry 3 -o "$ZIP_CACHE.part" "$ORT_WIN_ZIP"
    mv "$ZIP_CACHE.part" "$ZIP_CACHE"
  fi
  unzip -q -o -j "$ZIP_CACHE" \
    "onnxruntime-win-x64-$ORT_VERSION/lib/onnxruntime.dll" \
    "onnxruntime-win-x64-$ORT_VERSION/lib/onnxruntime_providers_shared.dll" \
    -d "$STAGE"
  for d in onnxruntime.dll onnxruntime_providers_shared.dll; do
    [[ -s "$STAGE/$d" ]] || { echo "✗ zip 里没取到 $d" >&2; exit 1; }
  done
  FILES+=(onnxruntime.dll onnxruntime_providers_shared.dll)
fi

FILES_JSON=$(printf '"%s", ' "${FILES[@]}"); FILES_JSON="[${FILES_JSON%, }]"
cat > "$STAGE/pack.json" <<JSON
{
  "version": "$VERSION",
  "model": "bge-m3",
  "source": "Xenova/bge-m3 onnx/model_quantized.onnx",
  "platform": "$PLATFORM",
  "onnxruntime": "$([[ "$PLATFORM" == windows-x64 ]] && echo "$ORT_VERSION" || echo null)",
  "files": $FILES_JSON
}
JSON

OUT="$OUT_DIR/embed-model-$VERSION-$PLATFORM.tar.gz"
# COPYFILE_DISABLE: macOS 的 tar 别把 ._* 元数据塞进包
COPYFILE_DISABLE=1 tar -czf "$OUT" -C "$STAGE" "${FILES[@]}" pack.json
if command -v shasum >/dev/null; then SHA=$(shasum -a 256 "$OUT" | cut -c1-64); else SHA=$(sha256sum "$OUT" | cut -c1-64); fi
echo "✓ $OUT"
echo "  size   $(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT") bytes"
echo "  sha256 $SHA"
echo "接下来: cp 到中央组件目录, 再 python3 delivery/catfish-poc/tools/build_component_manifest.py <组件目录>"
