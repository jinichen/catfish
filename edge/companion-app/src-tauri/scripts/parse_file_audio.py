"""Audio file parsing — 抽自 parse_file.py (5/21 拆分).

走 ffmpeg + whisper.cpp 转录音频 (mp3/m4a/wav/aac/...). 跟 BL-VOICE3 (5/10) 联动.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

PREVIEW_MAX_CHARS = int(os.environ.get("CATFISH_PREVIEW_MAX_CHARS") or 5000)


def _truncate(s: str, limit: int = PREVIEW_MAX_CHARS) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n\n... [Audio transcript truncated at {limit} chars]"


# ============================================================
#
# 用 ffmpeg 转 16kHz mono wav → whisper-cli + ggml-small.bin → 转写文本.
# 跟 src/commands/speech.rs (5/1 ship 的语音输入) 共用同一套 whisper 链路:
#   - whisper-cli 已在: brew install whisper-cpp
#   - 模型已下载: ~/.catfish/whisper-models/ggml-small.bin
#   - ffmpeg 已装 (avfoundation 录音用)
#
# preview 输出:
#   - duration_sec / sample_rate / language='zh'
#   - 转写文本前 PREVIEW_MAX_CHARS 字 (前 5K)
#   - 全文 ≥ 50KB 自动走 BL-L26 BM25 sidecar
#
# 失败兜底: ffmpeg / whisper-cli 找不到 → 报清楚错让员工知道装啥
# 5/14 demo 不演音频上传, 这是 5/22 PoC 起客户用的


def _find_executable(name: str) -> str | None:
    """跨平台找可执行 (homebrew / apt / 用户 PATH)."""
    import shutil as _shutil
    return _shutil.which(name)


def _whisper_model_path() -> Path | None:
    """跟 speech.rs 一样: ~/.catfish/whisper-models/ggml-small.bin (优先 small),
    回退到 ggml-medium.bin (准确率高但慢) / ggml-large-v3.bin."""
    base = Path.home() / ".catfish" / "whisper-models"
    for model in ("ggml-small.bin", "ggml-medium.bin", "ggml-large-v3.bin"):
        p = base / model
        if p.exists():
            return p
    return None


def _transcribe_audio_to_text(audio_path: Path, lang: str = "zh") -> tuple[str, dict[str, Any]]:
    """跑 ffmpeg → 16kHz mono wav → whisper-cli → 转写文本.

    返回 (transcript, meta) — meta 含 duration_sec / sample_rate / model.
    异常: 缺工具时抛 RuntimeError, parser 兜底返 error JSON.
    """
    import subprocess as _sp

    ffmpeg = _find_executable("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg 未装. macOS: brew install ffmpeg")

    whisper = _find_executable("whisper-cli") or _find_executable("main")
    if whisper is None:
        raise RuntimeError(
            "whisper-cli 未装. macOS: brew install whisper-cpp\n"
            "或编译 native/transcribe-helper.swift 装 catfish-transcribe (准确率最佳)"
        )

    model_path = _whisper_model_path()
    if model_path is None:
        raise RuntimeError(
            "whisper 模型没下载. 跑: \n"
            "  mkdir -p ~/.catfish/whisper-models\n"
            "  curl -L -o ~/.catfish/whisper-models/ggml-small.bin "
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
        )

    # 1. ffmpeg 转 16kHz mono wav (whisper 输入要求)
    wav_path = audio_path.with_suffix(audio_path.suffix + ".transcoded.wav")
    duration_sec = 0.0
    try:
        # -loglevel error: 只输出错误, 别污染 stdout
        # -y: 覆盖
        # -ac 1: mono
        # -ar 16000: 16kHz (whisper 要求)
        ff = _sp.run(
            [ffmpeg, "-loglevel", "error", "-y", "-i", str(audio_path),
             "-vn",  # 不要视频流 (BL-I3.1 复用此函数处理视频时关键)
             "-ac", "1", "-ar", "16000",
             str(wav_path)],
            capture_output=True, timeout=180,
        )
        if ff.returncode != 0:
            raise RuntimeError(f"ffmpeg 失败: {ff.stderr.decode('utf-8', errors='replace')[:500]}")

        # 拿 duration: ffprobe 优先, 没有用 ffmpeg -i 解析
        ffprobe = _find_executable("ffprobe")
        if ffprobe:
            try:
                pp = _sp.run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                    capture_output=True, timeout=30, text=True,
                )
                duration_sec = float(pp.stdout.strip() or "0")
            except (ValueError, _sp.TimeoutExpired):
                duration_sec = 0.0

        # 2. whisper-cli 转写
        # 跟 speech.rs 一样加 prompt 提升公文术语
        prompt = (
            "以下是中文工作对话, 涉及鲶鱼平台、Companion、催办、"
            "公文汇报、月度总结、周报、资质管理、合规、ISO27001、客户、PoC、"
            "立项、采购、招投标、技术方案、KPI、考核 等场景."
        )
        wp = _sp.run(
            [whisper,
             "-m", str(model_path),
             "-l", lang,
             "-f", str(wav_path),
             "-otxt",
             "--no-prints",
             "--prompt", prompt],
            capture_output=True, timeout=600,
        )

        # whisper-cli 输出 <wav>.txt
        txt_path = wav_path.with_suffix(".wav.txt")
        if wp.returncode != 0:
            raise RuntimeError(
                f"whisper-cli 失败 (rc={wp.returncode}): "
                f"{wp.stderr.decode('utf-8', errors='replace')[:500]}"
            )
        if txt_path.exists():
            transcript = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        else:
            transcript = wp.stdout.decode("utf-8", errors="replace").strip()
    finally:
        # 清理 transcoded wav + .txt sidecar (留原文件)
        for p in (wav_path, wav_path.with_suffix(".wav.txt")):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    meta: dict[str, Any] = {
        "duration_sec": round(duration_sec, 2),
        "sample_rate": 16000,
        "language": lang,
        "model": model_path.name,
        "transcript_chars": len(transcript),
    }
    return transcript, meta


def parse_audio_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """音频文件 (mp3/wav/m4a/flac) 转写 preview.

    BL-I4 (5/8): 复用 5/1 BL 语音输入的 whisper.cpp 链路, 把上传的音频文件
    转成中文文本, 让 LLM 能"听懂"会议录音 / 培训音频 / 微信语音条等.

    返回 preview (前 5K 字) + 完整 meta. 全文 (任意大小) 通过 sidecar 给 BM25.
    """
    transcript, raw_meta = _transcribe_audio_to_text(path, lang="zh")

    if not transcript:
        return (
            f"## 音频 · {raw_meta.get('duration_sec', 0)} 秒\n"
            "(没识别出文字, 可能音频空 / 信号弱 / 模型不兼容)",
            raw_meta,
        )

    # 拼 preview
    parts = [
        f"## 音频转写 · {raw_meta.get('duration_sec', 0)} 秒 · "
        f"语言 {raw_meta.get('language', 'zh')} · {raw_meta.get('transcript_chars', 0)} 字",
        "",
        _truncate(transcript),
    ]
    if len(transcript) > PREVIEW_MAX_CHARS:
        parts.append(
            f"\n[... 全文 {len(transcript)} 字, 用 BM25 检索相关段, "
            "或 execute_code 读 keptPath 完整文件]"
        )
    return "\n".join(parts), raw_meta

