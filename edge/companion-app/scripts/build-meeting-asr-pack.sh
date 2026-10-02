#!/usr/bin/env bash
# 打会议纪要组件包 meeting-asr-<版本>-<平台>.tar.gz (10/1, docs/MEETING-MINUTES-PLAN.md §3-4)。
#
#   bash scripts/build-meeting-asr-pack.sh [版本号, 默认 1.0.0] [输出目录, 默认 ~/catfish-components]
#
# 平台 = 在哪台机器上跑就打哪个 (要在目标平台上跑自检):
#   Apple Silicon Mac → mac-arm64
#   Windows x64 (Git Bash) → windows-x64 —— 10/2 加; 平时由 .github/workflows/
#     build-meeting-asr-pack.yml 在 windows-latest 上跑, 不用找 Windows 机器
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
# Windows 上 Python 默认按 cp1252 往控制台打印, 自检里一 print 中文转写就崩
# (10/2 第一次在 windows-latest 上跑: 转写全对, 死在打印比对结果这一步)
export PYTHONUTF8=1

VERSION="${1:-1.0.0}"
COMPANION="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${2:-$HOME/catfish-components}"
REQS_FILE="$COMPANION/meeting-asr-requirements.txt"
SCRIPT="$COMPANION/src-tauri/scripts/meeting_asr.py"
MODEL_CACHE="${MEETING_PACK_MODEL_CACHE:-$HOME/.cache/modelscope}"

# name=ModelScope 模型 id。改模型要同步改 meeting_asr.py 里的目录名。
MODELS=(
  "vad=iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
  "asr=iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
  "punc=iic/punc_ct-transformer_cn-en-common-vocab471067-large"
  "spk=iic/speech_campplus_sv_zh-cn_16k-common"
)

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "❌ 版本号要 x.y.z: $VERSION" >&2; exit 1; }
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    PLATFORM="mac-arm64"; PLATFORM_TAG="macosx_12_0_arm64"; VBIN="bin"; PYEXE="python"
    PY311="${PY311:-$(command -v python3.11 || true)}" ;;
  MINGW64*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64)
    # 内嵌 Python 也是 3.11 (build-windows-msi.yml 打的 cpython 3.11.15), 所以 cp311
    PLATFORM="windows-x64"; PLATFORM_TAG="win_amd64"; VBIN="Scripts"; PYEXE="python.exe"
    PY311="${PY311:-$(command -v python3.11 || command -v python || true)}" ;;
  *) echo "❌ 只能在 Apple Silicon Mac 或 Windows x64 上打 (要在目标平台跑自检)" >&2; exit 1 ;;
esac
[[ -n "$PY311" ]] && "$PY311" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))' \
  || { echo "❌ 需要 python 3.11 (uv python install 3.11, 或设 PY311=...)" >&2; exit 1; }
PACK_NAME="meeting-asr-$VERSION-$PLATFORM.tar.gz"
# Git Bash 只自动转换"单独一个参数且以 / 开头"的路径; 嵌在 python -c 字符串里的不转,
# Windows 的 Python 读不懂 /c/Users/...。这两个函数在 mac 上原样返回。
winpath() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
unixpath() { if command -v cygpath >/dev/null; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
# Git Bash 的 GNU tar 把 "D:\..." 当成"远程主机 D 上的路径", 去连一台叫 D 的机器
# (10/2 windows-latest: 自检全过, 死在出包 "Cannot connect to D: resolve failed")。
OUT_DIR="$(unixpath "$OUT_DIR")"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/meeting-asr-pack.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/stage"
mkdir -p "$STAGE/wheels" "$STAGE/models" "$WORK/local" "$OUT_DIR"

REQS=()
while IFS= read -r line; do
  # 去注释 + 去首尾空白。不用 xargs: 它会吃掉引号, `av; sys_platform == "win32"` 就坏了
  line="${line%%#*}"; line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
  [[ -n "$line" ]] && REQS+=("$line")
done <"$REQS_FILE"
echo "=== meeting-asr $VERSION · $PLATFORM · 依赖: ${REQS[*]}"

echo "=== [1/5] 打包环境"
"$PY311" -m venv "$WORK/build-venv"
BPY="$WORK/build-venv/$VBIN/$PYEXE"
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
if [[ "$PLATFORM" == mac-* ]] && ls "$STAGE/wheels" | grep -E "macosx_(1[3-9]|[2-9][0-9])_" ; then
  echo "❌ 上面这些 wheel 要求的 macOS 比 12 新" >&2; exit 1
fi
# 反过来也核对: 别的平台的 wheel 混进来 = 下载那步平台没钉住
if ls "$STAGE/wheels" | grep -vE "(none-any|$PLATFORM_TAG|macosx_1[0-2]_[0-9]+_(arm64|universal2))\.whl$" | grep -E "\.whl$" ; then
  echo "❌ 上面这些 wheel 不是 $PLATFORM 能装的" >&2; exit 1
fi
echo "  ✓ $(ls "$STAGE/wheels" | wc -l | xargs) 个 wheel · $(du -sh "$STAGE/wheels" | cut -f1)"

echo "=== [3/5] 模型"
MODEL_JSON=""
for pair in "${MODELS[@]}"; do
  name="${pair%%=*}"; id="${pair#*=}"
  # 模型放持久缓存 (默认 ModelScope 自己的 ~/.cache/modelscope): 2.1GB 每次重下要几十分钟,
  # 已有的 snapshot_download 只做校验。
  dir="$("$BPY" -c "import sys; from modelscope import snapshot_download; print(snapshot_download('$id', cache_dir=sys.argv[1]))" \
        "$(winpath "$MODEL_CACHE")" 2>/dev/null | tail -1 | tr -d '\r')"
  dir="$(unixpath "$dir")"
  [[ -d "$dir" ]] || { echo "❌ 模型下载失败: $id" >&2; exit 1; }
  cp -RL "$dir" "$STAGE/models/$name"
  rm -rf "$STAGE/models/$name/.git" "$STAGE/models/$name/.msc" "$STAGE/models/$name/.mv"
  echo "  ✓ $name ← $id · $(du -sh "$STAGE/models/$name" | cut -f1)"
  MODEL_JSON+="\"$name\": \"$id\","
done

echo "=== [4/5] 自检: 干净 venv 离线安装 + 真转写"
"$PY311" -m venv "$WORK/check-venv"
CPY="$WORK/check-venv/$VBIN/$PYEXE"
"$CPY" -m pip install -q --no-index --find-links "$STAGE/wheels" "${REQS[@]}"
mkdir -p "$WORK/check/audio"
cp "$STAGE/models/asr/example/asr_example.wav" "$WORK/check/audio/seg-0001.wav" 2>/dev/null \
  || cp "$(find "$STAGE/models/asr" -name '*.wav' | head -1)" "$WORK/check/audio/seg-0001.wav"
mkdir -p "$WORK/empty-cache"
offline() {
  HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 MODELSCOPE_CACHE="$(winpath "$WORK/empty-cache")" \
    HF_HUB_OFFLINE=1 PYTHONUTF8=1 "$@"
}
offline "$CPY" "$SCRIPT" --audio-dir "$WORK/check/audio" --models "$STAGE/models" \
  --speakers 1 --out "$WORK/check/transcript.json" 2>"$WORK/check/stderr.txt" | tail -1
n="$("$CPY" -c "import json, sys; print(len(json.load(open(sys.argv[1], encoding='utf-8'))['segments']))" \
     "$WORK/check/transcript.json" | tr -d '\r')"
[[ "$n" -gt 0 ]] || { cat "$WORK/check/stderr.txt" >&2; echo "❌ 自检转写结果为空" >&2; exit 1; }
echo "  ✓ 离线转写出 $n 段 (分说话人)"

if [[ "$PLATFORM" == windows-* ]]; then
  # Windows 的语音输入 / 上传音频靠 PyAV 解码: 示例音频编成 m4a (AAC), 再走一遍
  # --decode → 不分说话人转写, 文字要跟直接转 wav 一样
  "$CPY" - "$WORK/check/audio/seg-0001.wav" "$WORK/check/upload.m4a" <<'PY'
import sys, av, numpy as np, soundfile as sf
data, sr = sf.read(sys.argv[1], dtype="int16")
with av.open(sys.argv[2], "w", format="mp4") as c:
    st = c.add_stream("aac", rate=sr)
    st.layout = "mono"
    fr = av.AudioFrame.from_ndarray(data.reshape(1, -1), format="s16", layout="mono")
    fr.sample_rate = sr
    for f in av.AudioResampler(format=st.format.name, layout="mono", rate=sr).resample(fr):
        for p in st.encode(f):
            c.mux(p)
    for p in st.encode(None):
        c.mux(p)
PY
  offline "$CPY" "$SCRIPT" --decode "$WORK/check/upload.m4a" --out "$WORK/check/upload.wav" | tail -1
  offline "$CPY" "$SCRIPT" --audio-file "$WORK/check/upload.wav" --models "$STAGE/models" \
    --speakers 0 --out "$WORK/check/upload.json" 2>>"$WORK/check/stderr.txt" | tail -1
  "$CPY" - "$WORK/check/transcript.json" "$WORK/check/upload.json" <<'PY' || { cat "$WORK/check/stderr.txt" >&2; exit 1; }
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8"))["text"]
b = json.load(open(sys.argv[2], encoding="utf-8"))["text"]
print(f"  wav: {a}\n  m4a: {b}")
sys.exit(0 if b and b == a else "❌ m4a 解码后转写结果跟原 wav 不一样")
PY
  echo "  ✓ PyAV 解码 m4a → 转写一致"
fi
[[ -z "$(find "$WORK/empty-cache" -type f | head -1)" ]] || { echo "❌ 自检时偷偷联网下载了东西" >&2; exit 1; }

echo "=== [5/5] 出包"
REQS_JSON=""
for r in "${REQS[@]}"; do REQS_JSON+="\"${r//\"/\\\"}\","; done  # 依赖里有引号 (环境标记), 要转义
cat >"$STAGE/pack.json" <<JSON
{
  "name": "meeting-asr",
  "version": "$VERSION",
  "platform": "$PLATFORM",
  "python": "3.11",
  "min_os": "$([[ "$PLATFORM" == mac-* ]] && echo "macOS 12.0" || echo "Windows 10 x64")",
  "requirements": [${REQS_JSON%,}],
  "models": {${MODEL_JSON%,}},
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
tar czf "$OUT_DIR/$PACK_NAME" -C "$STAGE" pack.json wheels models
echo "✓ $OUT_DIR/$PACK_NAME · $(du -h "$OUT_DIR/$PACK_NAME" | cut -f1)"
echo "  下一步: 拷到中央组件目录, 再跑 python3 tools/build_component_manifest.py ./components"
