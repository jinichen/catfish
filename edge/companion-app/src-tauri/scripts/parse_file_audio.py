"""Audio file parsing — 抽自 parse_file.py (5/21 拆分).

音频 / 视频转文字 —— 10/1 起走 afconvert 解码 + 会议组件包 (FunASR), 不再用 ffmpeg + whisper.cpp。
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
# 10/1: afconvert (macOS 自带) 解码成 16k 单声道 wav → 会议组件包的 meeting_asr.py 转写
# (FunASR, 不分说话人)。跟 src/commands/speech.rs (聊天 🎤 / 上传音频) 同一套。
# 组件包没装 → RuntimeError 提示去「会议」页装; 不再需要 brew 的 ffmpeg / whisper-cpp。
#
# preview 输出:
#   - duration_sec / sample_rate / language='zh'
#   - 转写文本前 PREVIEW_MAX_CHARS 字 (前 5K)
#   - 全文 ≥ 50KB 自动走 BL-L26 BM25 sidecar
#
# 失败兜底: ffmpeg / whisper-cli 找不到 → 报清楚错让员工知道装啥
# 5/14 demo 不演音频上传, 这是 5/22 PoC 起客户用的


def _meeting_asr_install() -> dict | None:
    """会议组件包装在哪 (~/.catfish/meeting-asr/current.json, Companion 装的)。没装返回 None。"""
    cur = Path.home() / ".catfish" / "meeting-asr" / "current.json"
    try:
        inst = json.loads(cur.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not Path(inst.get("python", "")).is_file() or not (Path(inst.get("models", "")) / "asr").is_dir():
        return None
    return inst


def _transcribe_audio_to_text(audio_path: Path, lang: str = "zh") -> tuple[str, dict[str, Any]]:
    """音频 / 视频 → 文字 (10/1 改版, 原来是 ffmpeg + whisper-cli)。

    解码用 macOS 自带的 afconvert (mp3 / m4a / aac / flac / opus / wav / aiff 和
    mp4 / mov 的音轨都实测能解), 转写用会议组件包 (FunASR) 的 meeting_asr.py,
    不分说话人。脚本跑在组件包自己的 venv 里 (本模块所在的 hermes venv 没装 FunASR)。
    ffmpeg / whisper-cli / whisper 模型从来不在安装包里, 客户机上原来这条路是断的。

    返回 (transcript, meta)。缺组件包 / 解不了的格式抛 RuntimeError, parser 兜底返 error JSON。
    """
    import os as _os
    import subprocess as _sp
    import tempfile as _tf
    import wave as _wave

    inst = _meeting_asr_install()
    if inst is None:
        raise RuntimeError("音频转文字要先在鲶鱼「会议」页下载并安装会议组件包 (一次就好)")
    if not Path("/usr/bin/afconvert").exists():
        raise RuntimeError("音频转文字目前只支持 macOS")
    script = Path(__file__).with_name("meeting_asr.py")

    with _tf.TemporaryDirectory(prefix="catfish-audio-") as td:
        wav = Path(td) / "in.wav"
        out = Path(td) / "t.json"
        dec = _sp.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(audio_path), str(wav)],
            capture_output=True, timeout=600,
        )
        if dec.returncode != 0:
            raise RuntimeError(
                f"这个格式解不了 (换成 mp3 / m4a / wav 再试): {dec.stderr.decode('utf-8', 'replace')[:200]}"
            )
        with _wave.open(str(wav)) as w:
            duration_sec = w.getnframes() / float(w.getframerate() or 16000)

        empty_cache = Path.home() / ".catfish" / "meeting-asr" / ".empty-cache"
        empty_cache.mkdir(parents=True, exist_ok=True)
        env = dict(_os.environ, HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9",
                   NO_PROXY="", MODELSCOPE_CACHE=str(empty_cache), HF_HUB_OFFLINE="1",
                   PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        r = _sp.run(
            [inst["python"], str(script), "--audio-file", str(wav), "--models", inst["models"],
             "--speakers", "0", "--out", str(out)],
            capture_output=True, env=env, timeout=max(600, int(duration_sec * 2)),
        )
        if r.returncode != 0 or not out.exists():
            last = r.stdout.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"转写失败: {last[0][:300]}")
        transcript = (json.loads(out.read_text(encoding="utf-8")).get("text") or "").strip()

    meta: dict[str, Any] = {
        "duration_sec": round(duration_sec, 2),
        "sample_rate": 16000,
        "language": lang,
        "model": f"funasr seaco-paraformer (会议组件包 {inst.get('version', '?')})",
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

