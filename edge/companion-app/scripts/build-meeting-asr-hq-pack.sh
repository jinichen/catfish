#!/usr/bin/env bash
# 打高精度转写组件 meeting-asr-hq-<版本>-<平台>.tar.gz (11/17, docs/MEETING-MINUTES-PLAN.md §12)。
#
#   bash scripts/build-meeting-asr-hq-pack.sh [版本号, 默认 1.0.0] [输出目录, 默认 ~/catfish-components]
#
# 平台 = 在哪台机器上跑就打哪个 (Apple Silicon Mac → mac-arm64; Windows x64 Git Bash → windows-x64,
# 平时由 .github/workflows/build-meeting-asr-pack.yml 选 hq 在 windows-latest 上跑)。
#
# 包里 (不带 Python —— 员工机上借默认会议组件的 3.11 建 venv, 见 services/meeting_asr_hq.rs):
#   wheels/   离线安装用的 wheel (平台钉 macosx_12_0_arm64 / win_amd64 + cp311)
#   models/asr-qwen3/   Qwen/Qwen3-ASR-1.7B
#   pack.json 版本 / 依赖 / 只装自己的依赖 (requirements_nodeps) / 模型来源
#
# 自检: 干净 venv 离线装一遍, 拿一段示例人声做一份假的「已转写」, 跑 meeting_asr_qwen.py 真识别一次 (有字才出包);
# 顺手量 60 秒音频要多久 (Windows 没有 Apple GPU, 看 CPU 跑不跑得动)。需要联网 (下 wheel 和模型), 只在打包机上跑。
set -euo pipefail
export PYTHONUTF8=1

VERSION="${1:-1.0.0}"
COMPANION="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${2:-$HOME/catfish-components}"
REQS_FILE="$COMPANION/meeting-asr-hq-requirements.txt"
SCRIPT="$COMPANION/src-tauri/scripts/meeting_asr_qwen.py"
MODEL_CACHE="${MEETING_PACK_MODEL_CACHE:-$HOME/.cache/modelscope}"
MODEL_ID="Qwen/Qwen3-ASR-1.7B"
# 自检用的一段人声: 默认组件 Paraformer 模型自带的示例 (只下这一个文件)
SAMPLE_MODEL="iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "❌ 版本号要 x.y.z: $VERSION" >&2; exit 1; }
PY_VERSION="3.11.15"
PY_BUILD_TAG="20260807"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    PLATFORM="mac-arm64"; PLATFORM_TAG="macosx_12_0_arm64"; VBIN="bin"; PYEXE="python"
    PBS_TRIPLE="aarch64-apple-darwin"; BUNDLED_REL="python/bin/python3.11" ;;
  MINGW64*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64)
    PLATFORM="windows-x64"; PLATFORM_TAG="win_amd64"; VBIN="Scripts"; PYEXE="python.exe"
    PBS_TRIPLE="x86_64-pc-windows-msvc"; BUNDLED_REL="python/python.exe" ;;
  *) echo "❌ 只能在 Apple Silicon Mac 或 Windows x64 上打 (要在目标平台跑自检)" >&2; exit 1 ;;
esac
. "$COMPANION/scripts/lib-download.sh"
PACK_NAME="meeting-asr-hq-$VERSION-$PLATFORM.tar.gz"
winpath() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
unixpath() { if command -v cygpath >/dev/null; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
OUT_DIR="$(unixpath "$OUT_DIR")"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/meeting-asr-hq-pack.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/stage"
mkdir -p "$STAGE/wheels" "$STAGE/models" "$OUT_DIR"

REQS=(); NODEPS=()
while IFS= read -r line; do
  line="${line%%#*}"; line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  if [[ "$line" == --no-deps\ * ]]; then NODEPS+=("${line#--no-deps }"); else REQS+=("$line"); fi
done <"$REQS_FILE"
echo "=== meeting-asr-hq $VERSION · $PLATFORM · 依赖: ${REQS[*]} · 只装自己: ${NODEPS[*]}"

echo "=== [1/5] 打包用的 Python $PY_VERSION (不进包)"
download_with_retry \
  "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_BUILD_TAG}/cpython-${PY_VERSION}+${PY_BUILD_TAG}-${PBS_TRIPLE}-install_only.tar.gz" \
  "$WORK/python.tar.gz"
mkdir -p "$WORK/py"; tar xzf "$WORK/python.tar.gz" -C "$WORK/py"
PY311="$WORK/py/$BUNDLED_REL"
"$PY311" -m venv "$WORK/build-venv"
BPY="$WORK/build-venv/$VBIN/$PYEXE"
"$BPY" -m pip install -q --upgrade pip wheel modelscope

echo "=== [2/5] wheel (平台 $PLATFORM_TAG · cp311)"
"$BPY" -m pip download -q -d "$STAGE/wheels" --only-binary=:all: --platform "$PLATFORM_TAG" \
  --python-version 3.11 --implementation cp "${REQS[@]}"
"$BPY" -m pip download -q -d "$STAGE/wheels" --no-deps --only-binary=:all: --platform "$PLATFORM_TAG" \
  --python-version 3.11 --implementation cp "${NODEPS[@]}"
if [[ "$PLATFORM" == mac-* ]] && ls "$STAGE/wheels" | grep -E "macosx_(1[3-9]|[2-9][0-9])_" ; then
  echo "❌ 上面这些 wheel 要求的 macOS 比 12 新" >&2; exit 1
fi
if ls "$STAGE/wheels" | grep -vE "(none-any|$PLATFORM_TAG|macosx_1[0-2]_[0-9]+_(arm64|universal2))\.whl$" | grep -E "\.whl$" ; then
  echo "❌ 上面这些 wheel 不是 $PLATFORM 能装的" >&2; exit 1
fi
echo "  ✓ $(ls "$STAGE/wheels" | wc -l | xargs) 个 wheel · $(du -sh "$STAGE/wheels" | cut -f1)"

echo "=== [3/5] 模型"
dir="$("$BPY" -c "import sys; from modelscope import snapshot_download; print(snapshot_download('$MODEL_ID', cache_dir=sys.argv[1]))" \
      "$(winpath "$MODEL_CACHE")" 2>/dev/null | tail -1 | tr -d '\r')"
dir="$(unixpath "$dir")"
[[ -f "$dir/config.json" ]] || { echo "❌ 模型下载失败: $MODEL_ID" >&2; exit 1; }
cp -RL "$dir" "$STAGE/models/asr-qwen3"
rm -rf "$STAGE/models/asr-qwen3/.git" "$STAGE/models/asr-qwen3/.msc" "$STAGE/models/asr-qwen3/.mv"
echo "  ✓ asr-qwen3 ← $MODEL_ID · $(du -sh "$STAGE/models/asr-qwen3" | cut -f1)"
sample_dir="$("$BPY" -c "import sys; from modelscope import snapshot_download; print(snapshot_download('$SAMPLE_MODEL', cache_dir=sys.argv[1], allow_file_pattern='example/*'))" \
      "$(winpath "$MODEL_CACHE")" 2>/dev/null | tail -1 | tr -d '\r')"
SAMPLE="$(find "$(unixpath "$sample_dir")" -name '*.wav' | head -1)"
[[ -f "$SAMPLE" ]] || { echo "❌ 自检用的示例人声没下到" >&2; exit 1; }

echo "=== [4/5] 自检: 干净 venv 离线安装 + 真识别一次 (跟员工机同一条路)"
"$PY311" -m venv "$WORK/check-venv"
CPY="$WORK/check-venv/$VBIN/$PYEXE"
"$CPY" -m pip install -q --no-index --find-links "$STAGE/wheels" "${REQS[@]}"
"$CPY" -m pip install -q --no-index --no-deps --find-links "$STAGE/wheels" "${NODEPS[@]}"
mkdir -p "$WORK/check/audio" "$WORK/empty-cache"
cp "$SAMPLE" "$WORK/check/audio/seg-0001.wav"
offline() {
  HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 MODELSCOPE_CACHE="$(winpath "$WORK/empty-cache")" \
    HF_HUB_OFFLINE=1 PYTHONUTF8=1 "$@"
}
# 假的「已转写」: 整段示例一句话, 说话人 0
"$CPY" - "$WORK/check/audio/seg-0001.wav" "$WORK/check/base.json" <<'PY'
import json, sys, soundfile as sf
info = sf.info(sys.argv[1])
json.dump({"segments": [{"spk": 0, "start": 0.0, "end": round(info.duration, 2), "text": "(普通转写)"}],
           "duration_secs": round(info.duration, 1), "speakers": 1}, open(sys.argv[2], "w", encoding="utf-8"))
PY
offline "$CPY" "$(winpath "$SCRIPT")" --transcript "$(winpath "$WORK/check/base.json")" \
  --audio-dir "$(winpath "$WORK/check/audio")" --models "$(winpath "$STAGE/models")" \
  --out "$(winpath "$WORK/check/out.json")" 2>"$WORK/check/stderr.txt" | tail -1
"$CPY" - "$WORK/check/out.json" <<'PY' || { cat "$WORK/check/stderr.txt" >&2; exit 1; }
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
text = "".join(s["text"] for s in d["segments"])
print(f"  识别: {text[:80]} · engine={d['engine']['asr']} · {d['engine'].get('device')}")
sys.exit(0 if text.strip() and d["engine"]["asr"].startswith("qwen3") and "普通转写" not in text else "❌ 自检识别结果为空")
PY
echo "  ✓ 离线高精度识别通过"
offline "$CPY" - "$WORK/check/audio/seg-0001.wav" "$STAGE/models" "$(winpath "$SCRIPT")" <<'PY' || true
import sys, time, numpy as np, soundfile as sf
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[3]).parent))
import meeting_asr_qwen as mq
x, sr = sf.read(sys.argv[1], dtype="float32")
wav = np.tile(x, int(np.ceil(60 * sr / len(x))))[: 60 * sr]
dev, model = mq.load_model(Path(sys.argv[2]))
units = [{"start": i * 15.0, "end": i * 15.0 + 15.0, "spk": 0, "track": None} for i in range(4)]
t = time.time()
mq.transcribe(model, {None: wav}, units, "")
dt = time.time() - t
print(f"  速度 ({dev}): 60 秒音频 {dt:.1f} 秒 · 实时率 {dt / 60:.2f} · 两小时的会 (约 80 分钟人声) 估 {dt / 60 * 80:.0f} 分钟")
PY
[[ -z "$(find "$WORK/empty-cache" -type f | head -1)" ]] || { echo "❌ 自检时偷偷联网下载了东西" >&2; exit 1; }

echo "=== [5/5] 出包"
REQS_JSON=""; for r in "${REQS[@]}"; do REQS_JSON+="\"${r//\"/\\\"}\","; done
NODEPS_JSON=""; for r in "${NODEPS[@]}"; do NODEPS_JSON+="\"$r\","; done
cat >"$STAGE/pack.json" <<JSON
{
  "name": "meeting-asr-hq",
  "version": "$VERSION",
  "platform": "$PLATFORM",
  "python": "$PY_VERSION",
  "min_os": "$([[ "$PLATFORM" == mac-* ]] && echo "macOS 12.0" || echo "Windows 10 x64")",
  "requirements": [${REQS_JSON%,}],
  "requirements_nodeps": [${NODEPS_JSON%,}],
  "models": {"asr-qwen3": "$MODEL_ID"},
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
tar czf "$OUT_DIR/$PACK_NAME" -C "$STAGE" pack.json wheels models
echo "✓ $OUT_DIR/$PACK_NAME · $(du -h "$OUT_DIR/$PACK_NAME" | cut -f1)"
echo "  下一步: 中央门户「组件」页上传"
