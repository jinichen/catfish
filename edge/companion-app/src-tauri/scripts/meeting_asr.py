"""会议录音转写 (10/1, docs/MEETING-MINUTES-PLAN.md §3).

由 Companion 用会议组件包的 venv 跑 (~/.catfish/meeting-asr/venv-<ver>/), 不是 hermes venv。
FunASR: FSMN-VAD + SeACo-Paraformer (支持热词) + CT-Transformer 标点 + CAM++ 说话人。

    python meeting_asr.py --audio-dir <meeting>/audio --models <models 目录> \
        --speakers 4 --hotwords "资质集采 达华" --out <meeting>/transcript.json

    # 语音输入 / 上传的音频文件: 单个 16k wav, 不分说话人
    python meeting_asr.py --audio-file x.wav --models <models 目录> --speakers 0 --out t.json

    # Windows 解码 (10/2): 任意音频 / 视频 → 16k 单声道 wav。macOS 用系统 afconvert,
    # Windows 没有对应的系统工具, 用组件包里的 PyAV (只在 Windows 包里装)
    python meeting_asr.py --decode in.m4a --out in.wav

stdout 每行一个 JSON 事件, Companion 逐行读:
    {"event":"phase","phase":"loading"|"preparing"|"recognizing"|"writing"}
    {"event":"done","out":..., "segments":N, "speakers":N, "duration_secs":X}
    {"event":"error","message":...}
FunASR 自己的日志在 stderr, 不混进事件流。

几个决定 (都是实测出来的, 见计划文档 §2):
- 多个 5 分钟分片**拼成一条**再识别: 说话人聚类必须在整场录音上做, 分片各自聚类的话
  同一个人在不同分片里编号不同。
- `--speakers` (参会人数) 必传: 不给的话 30 分钟录音会把 4 个人分成 42 个。
- 分片是设备原生采样率 (常见 48k), 这里统一转 16k 单声道 —— FunASR 也会转, 但拼接要
  同一采样率, 而且 16k 拼出来的临时文件小三倍。
- 全程离线: 模型只从 --models 读; 调用方还会设 MODELSCOPE_CACHE 到空目录兜底。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TARGET_SR = 16000


# 事件只走这个流。main() 一开始把 sys.stdout 换成 stderr —— FunASR import 时会往
# stdout print "funasr version: x.y.z", 混进事件流的话 Companion 那边得猜哪行是事件。
_EVENTS = sys.stdout


def emit(event: str, **kw) -> None:
    _EVENTS.write(json.dumps({"event": event, **kw}, ensure_ascii=False) + "\n")
    _EVENTS.flush()


def list_segments(audio_dir: Path) -> list[Path]:
    return sorted(p for p in audio_dir.glob("seg-*.wav") if p.is_file())


def concat_to_16k(segments: list[Path], out: Path) -> float:
    """所有分片 → 一个 16k 单声道 WAV。返回总时长 (秒)。"""
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio.functional as AF

    parts = []
    for seg in segments:
        data, sr = sf.read(str(seg), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if sr != TARGET_SR:
            mono = AF.resample(torch.from_numpy(mono), sr, TARGET_SR).numpy()
        parts.append(mono)
    audio = np.concatenate(parts) if parts else np.zeros(0, dtype="float32")
    sf.write(str(out), audio, TARGET_SR, subtype="PCM_16")
    return len(audio) / TARGET_SR


def decode_to_16k(src: Path, dst: Path) -> float:
    """任意音频 / 视频 (mp3 / m4a / aac / flac / ogg / opus / wma / mp4 / mov ...) → 16k 单声道 wav。

    返回时长 (秒)。PyAV 自带 ffmpeg 解码库, 不用员工另装 ffmpeg。
    """
    import av
    import numpy as np
    import soundfile as sf

    chunks = []
    with av.open(str(src)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise RuntimeError("这个文件里没有音轨")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SR)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # 冲掉重采样器里剩的
            chunks.append(out.to_ndarray().reshape(-1))
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    sf.write(str(dst), pcm.astype(np.int16), TARGET_SR, subtype="PCM_16")
    return len(pcm) / TARGET_SR


def lower_priority() -> None:
    """会后转写占满 CPU 好几分钟, 不该让员工手上的活卡顿。

    macOS 由 Companion 用 nice 起; Windows 没有 nice, 进程自己降到"低于正常"。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        below_normal = 0x00004000
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k32.SetPriorityClass(k32.GetCurrentProcess(), below_normal)
    except Exception:  # noqa: BLE001 —— 降不了也照样转
        pass


def to_segments(result: dict) -> list[dict]:
    out = []
    for s in result.get("sentence_info") or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "spk": int(s.get("spk", 0)),
            "start": round(s.get("start", 0) / 1000, 2),
            "end": round(s.get("end", 0) / 1000, 2),
            "text": text,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    global _EVENTS
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    _EVENTS = sys.stdout
    sys.stdout = sys.stderr
    argv = sys.argv[1:] if argv is None else argv
    if "--decode" in argv:
        dp = argparse.ArgumentParser()
        dp.add_argument("--decode", required=True, type=Path)
        dp.add_argument("--out", required=True, type=Path)
        d = dp.parse_args(argv)
        try:
            secs = decode_to_16k(d.decode, d.out)
            emit("done", out=str(d.out), segments=0, speakers=0, duration_secs=round(secs, 2))
            return 0
        except Exception as e:  # noqa: BLE001
            emit("error", message=f"音频解码失败 (这个格式可能不支持, 换成 mp3 / m4a / wav 再试): {type(e).__name__}: {e}")
            return 1

    lower_priority()
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--audio-dir", type=Path)
    src.add_argument("--audio-file", type=Path)
    ap.add_argument("--models", required=True, type=Path)
    # 0 = 不分说话人 (语音输入 / 上传音频): 不加载 CAM++, 也不传 preset_spk_num
    ap.add_argument("--speakers", required=True, type=int)
    ap.add_argument("--hotwords", default="")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    try:
        with_spk = args.speakers > 0
        segments = list_segments(args.audio_dir) if args.audio_dir else [args.audio_file]
        if not segments or not all(p.is_file() for p in segments):
            raise RuntimeError(f"{args.audio_dir or args.audio_file} 里没有录音")
        for sub in ("vad", "asr", "punc") + (("spk",) if with_spk else ()):
            if not (args.models / sub / "model.pt").is_file() and not any((args.models / sub).glob("*.bin")):
                raise RuntimeError(f"模型不全: {args.models / sub} (会议组件包损坏? 重新安装)")

        emit("phase", phase="loading")
        t0 = time.time()
        from funasr import AutoModel

        extra = {"spk_model": str(args.models / "spk")} if with_spk else {}
        model = AutoModel(
            model=str(args.models / "asr"),
            vad_model=str(args.models / "vad"),
            punc_model=str(args.models / "punc"),
            device="cpu",
            disable_update=True,
            disable_pbar=True,
            log_level="ERROR",
            **extra,
        )

        emit("phase", phase="preparing")
        with tempfile.TemporaryDirectory(prefix="catfish-meeting-") as td:
            merged = Path(td) / "merged-16k.wav"
            duration = concat_to_16k(segments, merged)
            emit("phase", phase="recognizing", duration_secs=round(duration, 1))
            kw = {"batch_size_s": 300}
            if with_spk:
                kw["preset_spk_num"] = args.speakers
            if args.hotwords.strip():
                kw["hotword"] = args.hotwords.strip()
            result = model.generate(input=str(merged), **kw)[0]

        emit("phase", phase="writing")
        segs = to_segments(result)
        text = (result.get("text") or "").strip() or "".join(s["text"] for s in segs)
        doc = {
            "text": text,
            "version": 1,
            "engine": {"name": "funasr", "asr": "seaco-paraformer-large", "spk": "cam++"},
            "duration_secs": round(duration, 1),
            "speakers": len({s["spk"] for s in segs}) if with_spk else 0,
            "speakers_requested": args.speakers,
            "hotwords": args.hotwords.split() if args.hotwords.strip() else [],
            "elapsed_secs": round(time.time() - t0, 1),
            "segments": segs,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, args.out)
        emit("done", out=str(args.out), segments=len(segs), speakers=doc["speakers"], duration_secs=doc["duration_secs"])
        return 0
    except Exception as e:  # noqa: BLE001 — 任何失败都要变成一条 error 事件给界面
        emit("error", message=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
