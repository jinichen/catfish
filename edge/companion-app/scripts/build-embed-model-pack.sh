#!/usr/bin/env bash
# 打向量模型组件包 embed-model-<版本>-any.tar.gz (10/6)。
#
#   bash scripts/build-embed-model-pack.sh [版本号, 默认 1.0.0] [输出目录, 默认 ~/catfish-components]
#
# 包里 (文件名跟 src-tauri/src/services/embed_model.rs 的 ONNX_IN_PACK / TOKENIZER_IN_PACK 对齐):
#   bge-m3.onnx     Xenova/bge-m3 的 onnx/model_quantized.onnx (INT8, ~570MB)
#   tokenizer.json  同仓库 tokenizer.json
#   pack.json       { version, model, files }
#
# 来源: 本机 ~/.catfish/models/ 里已有的就直接用 (跟 Companion 默认加载路径一致);
# 没有就从 HuggingFace 下 (需要能出网, 走 HTTPS_PROXY 如果有)。
#
# 平台是 any (纯模型文件)。产物放到中央组件目录后跑
# delivery/catfish-poc/tools/build_component_manifest.py 生成清单, Companion 就能下载。
set -euo pipefail

VERSION="${1:-1.0.0}"
OUT_DIR="${2:-$HOME/catfish-components}"
SRC_DIR="${CATFISH_EMBED_MODEL_DIR:-$HOME/.catfish/models}"
HF_BASE="https://huggingface.co/Xenova/bge-m3/resolve/main"

case "$VERSION" in
  *[!0-9.]*|"") echo "✗ 版本号只能是 x.y.z: $VERSION" >&2; exit 1 ;;
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

cat > "$STAGE/pack.json" <<JSON
{
  "version": "$VERSION",
  "model": "bge-m3",
  "source": "Xenova/bge-m3 onnx/model_quantized.onnx",
  "files": ["bge-m3.onnx", "tokenizer.json"]
}
JSON

OUT="$OUT_DIR/embed-model-$VERSION-any.tar.gz"
# COPYFILE_DISABLE: macOS 的 tar 别把 ._* 元数据塞进包
COPYFILE_DISABLE=1 tar -czf "$OUT" -C "$STAGE" bge-m3.onnx tokenizer.json pack.json
if command -v shasum >/dev/null; then SHA=$(shasum -a 256 "$OUT" | cut -c1-64); else SHA=$(sha256sum "$OUT" | cut -c1-64); fi
echo "✓ $OUT"
echo "  size   $(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT") bytes"
echo "  sha256 $SHA"
echo "接下来: cp 到中央组件目录, 再 python3 delivery/catfish-poc/tools/build_component_manifest.py <组件目录>"
