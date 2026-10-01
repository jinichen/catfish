#!/usr/bin/env bash
# 打会议纪要组件包 meeting-asr-<版本>-mac-arm64.tar.gz (10/1, docs/MEETING-MINUTES-PLAN.md §3-4)。
#
#   bash scripts/build-meeting-asr-pack.sh [版本号, 默认 1.0.0] [输出目录, 默认 ~/catfish-components]
#
# 输出默认放仓库外: 10/1 第一版放在 companion-app/dist/components/, 而 dist/ 是前端构建
# 目录 —— 下一次 npm run dev / tauri build 时 vite 清空 dist/, 2.1GB 的包被连带删掉。
#
# 产物放到中央的组件目录 (delivery/catfish-poc/components/), 再跑
# tools/build_component_manifest.py 生成清单, Companion 就能下载。
#
# 包里:
#   wheels/   离线安装用的 wheel (平台钉 macosx_12_0_arm64 + cp311, 跟内嵌 Python 3.11 对齐)
#   models/   vad / asr / punc / spk 四个模型目录 (FunASR 直接按本地路径加载)
#   pack.json 版本 / 依赖清单 / 模型来源 / 最低 macOS
#
# 为什么平台要钉: 在本机直接 pip wheel 会拿到本机系统版本的 wheel (torch 2.14 /
# numpy / scipy 都是 macosx_14_0), 客户的 macOS 12 / 13 装不上。只有源码包的
# (jieba / oss2 / crcmod / antlr4) 在本机编成 wheel —— 它们是纯 Python 或最低 11.0。
#
# 最后一步自检: 用包里的东西在干净 venv 里**离线**装一遍, 跑一次真转写 (模型自带的示例
# 音频), 过了才出包。需要联网 (下 wheel 和模型), 只在打包机上跑。
set -euo pipefail

VERSION="${1:-1.0.0}"
COMPANION="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${2:-$HOME/catfish-components}"
PLATFORM_TAG="macosx_12_0_arm64"
REQS_FILE="$COMPANION/meeting-asr-requirements.txt"
SCRIPT="$COMPANION/src-tauri/scripts/meeting_asr.py"
PACK_NAME="meeting-asr-$VERSION-mac-arm64.tar.gz"
MODEL_CACHE="${MEETING_PACK_MODEL_CACHE:-$HOME/.cache/modelscope}"

# name=ModelScope 模型 id。改模型要同步改 meeting_asr.py 里的目录名。
MODELS=(
  "vad=iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
  "asr=iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
  "punc=iic/punc_ct-transformer_cn-en-common-vocab471067-large"
  "spk=iic/speech_campplus_sv_zh-cn_16k-common"
)

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "❌ 版本号要 x.y.z: $VERSION" >&2; exit 1; }
[[ "$(uname -s)-$(uname -m)" == "Darwin-arm64" ]] || { echo "❌ 只能在 Apple Silicon Mac 上打 (要跑自检)" >&2; exit 1; }
PY311="$(command -v python3.11 || true)"
[[ -n "$PY311" ]] || { echo "❌ 需要 python3.11 (uv python install 3.11)" >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/meeting-asr-pack.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/stage"
mkdir -p "$STAGE/wheels" "$STAGE/models" "$WORK/local" "$OUT_DIR"

REQS=()
while IFS= read -r line; do
  line="${line%%#*}"; line="$(echo "$line" | xargs)"
  [[ -n "$line" ]] && REQS+=("$line")
done <"$REQS_FILE"
echo "=== meeting-asr $VERSION · 依赖: ${REQS[*]}"

echo "=== [1/5] 打包环境"
"$PY311" -m venv "$WORK/build-venv"
BPY="$WORK/build-venv/bin/python"
"$BPY" -m pip install -q --upgrade pip wheel modelscope

echo "=== [2/5] wheel (平台 $PLATFORM_TAG · cp311)"
# 只有源码包的依赖在本机编成 wheel, 放 local/ 让下面的平台下载能找到
for round in $(seq 1 20); do
  # 不加 -q: 版本冲突的明细 ("X depends on Y==...") 只在非 quiet 输出里, 下面要靠它找源码包
  if out="$("$BPY" -m pip download -d "$STAGE/wheels" --find-links "$WORK/local" \
        --only-binary=:all: --platform "$PLATFORM_TAG" --python-version 3.11 \
        --implementation cp "${REQS[@]}" 2>&1)"; then
    break
  fi
  # `|| true`: 没匹配时 grep 退出码 1, 在 pipefail + set -e 下会让整个脚本当场静默退出
  # (10/1 第一次跑就这么停在了 oss2 之后)
  pkg="$(printf '%s' "$out" | grep -oE "No matching distribution found for [^ ]+" | awk '{print $NF}' | head -1 || true)"
  if [[ -z "$pkg" ]]; then
    # hydra-core 要的 antlr4 4.9.* 只有源码包, pip 把它报成版本冲突而不是"找不到"
    pkg="$(printf '%s' "$out" | grep -oE "depends on [A-Za-z0-9_.-]+==[0-9.*]+" | awk '{print $3}' | head -1 | sed 's/\*$/3/' || true)"
  fi
  [[ -n "$pkg" ]] || { echo "$out" >&2; echo "❌ 下载失败, 不是缺源码包的问题" >&2; exit 1; }
  echo "  · 只有源码包, 本机编: $pkg"
  "$BPY" -m pip wheel -q --no-deps -w "$WORK/local" "$pkg"
  [[ "$round" -lt 20 ]] || { echo "❌ 源码包太多, 停" >&2; exit 1; }
done
cp "$WORK"/local/*.whl "$STAGE/wheels/" 2>/dev/null || true
# 兜底核对: 不许混进比 macOS 12 新的 wheel
if ls "$STAGE/wheels" | grep -E "macosx_(1[3-9]|[2-9][0-9])_" ; then
  echo "❌ 上面这些 wheel 要求的 macOS 比 12 新" >&2; exit 1
fi
echo "  ✓ $(ls "$STAGE/wheels" | wc -l | xargs) 个 wheel · $(du -sh "$STAGE/wheels" | cut -f1)"

echo "=== [3/5] 模型"
MODEL_JSON=""
for pair in "${MODELS[@]}"; do
  name="${pair%%=*}"; id="${pair#*=}"
  # 模型放持久缓存 (默认 ModelScope 自己的 ~/.cache/modelscope): 2.1GB 每次重下要几十分钟,
  # 已有的 snapshot_download 只做校验。
  dir="$("$BPY" -c "from modelscope import snapshot_download; print(snapshot_download('$id', cache_dir='$MODEL_CACHE'))" 2>/dev/null | tail -1)"
  [[ -d "$dir" ]] || { echo "❌ 模型下载失败: $id" >&2; exit 1; }
  cp -RL "$dir" "$STAGE/models/$name"
  rm -rf "$STAGE/models/$name/.git" "$STAGE/models/$name/.msc" "$STAGE/models/$name/.mv"
  echo "  ✓ $name ← $id · $(du -sh "$STAGE/models/$name" | cut -f1)"
  MODEL_JSON+="\"$name\": \"$id\","
done

echo "=== [4/5] 自检: 干净 venv 离线安装 + 真转写"
"$PY311" -m venv "$WORK/check-venv"
"$WORK/check-venv/bin/python" -m pip install -q --no-index --find-links "$STAGE/wheels" "${REQS[@]}"
mkdir -p "$WORK/check/audio"
cp "$STAGE/models/asr/example/asr_example.wav" "$WORK/check/audio/seg-0001.wav" 2>/dev/null \
  || cp "$(find "$STAGE/models/asr" -name '*.wav' | head -1)" "$WORK/check/audio/seg-0001.wav"
mkdir -p "$WORK/empty-cache"
HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 MODELSCOPE_CACHE="$WORK/empty-cache" HF_HUB_OFFLINE=1 \
  "$WORK/check-venv/bin/python" "$SCRIPT" --audio-dir "$WORK/check/audio" --models "$STAGE/models" \
  --speakers 1 --out "$WORK/check/transcript.json" 2>"$WORK/check/stderr.txt" | tail -1
n="$("$WORK/check-venv/bin/python" -c "import json;print(len(json.load(open('$WORK/check/transcript.json'))['segments']))")"
[[ "$n" -gt 0 ]] || { cat "$WORK/check/stderr.txt" >&2; echo "❌ 自检转写结果为空" >&2; exit 1; }
[[ -z "$(find "$WORK/empty-cache" -type f | head -1)" ]] || { echo "❌ 自检时偷偷联网下载了东西" >&2; exit 1; }
echo "  ✓ 离线转写出 $n 段"

echo "=== [5/5] 出包"
REQS_JSON="$(printf '"%s",' "${REQS[@]}")"
cat >"$STAGE/pack.json" <<JSON
{
  "name": "meeting-asr",
  "version": "$VERSION",
  "platform": "mac-arm64",
  "python": "3.11",
  "min_macos": "12.0",
  "requirements": [${REQS_JSON%,}],
  "models": {${MODEL_JSON%,}},
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
tar czf "$OUT_DIR/$PACK_NAME" -C "$STAGE" pack.json wheels models
echo "✓ $OUT_DIR/$PACK_NAME · $(du -h "$OUT_DIR/$PACK_NAME" | cut -f1)"
echo "  下一步: 拷到中央组件目录, 再跑 python3 tools/build_component_manifest.py ./components"
