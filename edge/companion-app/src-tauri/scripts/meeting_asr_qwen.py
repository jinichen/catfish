"""高精度重新转写: 在已经转写过的会议上, 用 Qwen3-ASR 重出文字 (11/17, docs/MEETING-MINUTES-PLAN.md §12)。

鸿波 11/17「现在的转写准确性不够」。CCRC 整改会 72:00–82:00 盲测 (按 VAD 切 49 段, 三列每行打乱、不告诉是哪个模型,
员工听原声挑最接近的): Qwen3-ASR-1.7B 23 次、现在的 Paraformer 11 次、Fun-ASR-Nano 9 次。整场 (2:12) 对比: 说得清楚的地方
Qwen3 明显更好 (「我们自己看都不认识」vs「我们自己看不能嗯」), 远场 / 很小声的两个都不行。

但它重: 一场两小时的会 Qwen3 这一步就要几十分钟, 运行时占 4GB 左右内存, 模型 4.4GB —— 所以不塞进默认的会议组件, 做成
**可选的高精度组件** (meeting-asr-hq, 自己一个 Python 环境), 员工对要紧的会点「高精度重新转写」才跑:

  · 输入是这场会已有的 transcript.json (默认组件转出来的): 句子起止、说话人、哪条音轨都从它来 —— 说话人编号不变,
    员工起好的名字照用; Qwen3-ASR 不分说话人, 也用不着再跑一遍 FunASR。
  · 同一个人、停顿 < 0.8 秒的相邻句子连成 ≤ 20 秒的小段 (跟盲测的切法一样), 每段给 Qwen3-ASR, 热词当上下文;
    出来的文字按句末标点切句, 起止按字数在段内摊。
  · 单独一个进程跑 (默认组件那个进程已经退了, 内存不叠加), Companion 关了就跟着退 (exit_with_parent)。

    python meeting_asr_qwen.py --transcript T.json --audio-dir DIR --models HQ_MODELS --out OUT.json [--hotwords ...]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

QWEN_DIR = "asr-qwen3"
ENGINE_NAME = "qwen3-asr-1.7b"
SR = 16000
#: 小段最长 / 相邻两句停顿多短就连起来 (秒) —— 跟盲测时的切法一致
MAX_UNIT_S = 20.0
JOIN_GAP_S = 0.8
#: 句子起止来自逐字时间戳, 首尾字可能被切掉一点 —— 每段前后各多给这么多
PAD_S = 0.15
SENTENCE_END = "。？！?!；;"
BATCH = 4


def units_from_sentences(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """已有转写的句子 → 送给 Qwen3-ASR 的小段: 同一条音轨、同一个人、停顿 < 0.8 秒、合起来 ≤ 20 秒的连成一段;
    单句本身超过 20 秒的平均切成 ≤ 20 秒的几段。

    11/17 实测 CCRC: Paraformer 偶尔把一句的首字和尾字时间标得差几分钟 (「你说h是进行中，」407 秒、「甲方都在给他做。」
    186 秒)。原来「单句不切」直接把 7 分钟音频一次塞给 Qwen3 —— 内存涨到 46GB (16GB 的机器), 两小时的会 53 分钟只走了
    128 / 593 段。切开了这几段 Qwen3 还能捞回 Paraformer 漏掉的话。"""
    out: list[dict[str, Any]] = []
    for s in sorted(segs, key=lambda x: (float(x["start"]), float(x["end"]))):
        a, b = float(s["start"]), float(s["end"])
        if b <= a:
            continue
        track, spk = s.get("track"), int(s.get("spk", 0))
        cur = out[-1] if out else None
        if (cur and cur["track"] == track and cur["spk"] == spk and a - cur["end"] < JOIN_GAP_S
                and b - cur["start"] <= MAX_UNIT_S):
            cur["end"] = max(cur["end"], b)
        else:
            out.append({"start": a, "end": b, "spk": spk, "track": track})
    return [piece for u in out for piece in _split_long(u)]


def _split_long(u: dict[str, Any]) -> list[dict[str, Any]]:
    n = int(-(-(u["end"] - u["start"]) // MAX_UNIT_S))   # 向上取整
    if n <= 1:
        return [u]
    step = (u["end"] - u["start"]) / n
    return [{**u, "start": round(u["start"] + i * step, 3), "end": round(u["start"] + (i + 1) * step, 3)} for i in range(n)]


_TOKEN = re.compile(r"[一-鿿]|[A-Za-z]+(?:'[A-Za-z]+)?|\d+")


def split_sentences(text: str, start: float, end: float) -> list[dict[str, Any]]:
    """一小段的文字按句末标点切句, 起止按字数在 [start, end] 里摊。逗号不切 (Qwen3 的逗号很密, 切了句子太碎)。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p for p in re.split(rf"(?<=[{re.escape(SENTENCE_END)}])", text) if p.strip()]
    weights = [max(1, len(_TOKEN.findall(p))) for p in parts]
    total, acc, out, t = sum(weights), 0, [], start
    for p, w in zip(parts, weights):
        acc += w
        e = start + (end - start) * acc / total
        out.append({"start": round(t, 2), "end": round(e, 2), "text": p.strip()})
        t = e
    return out


def build_segments(units: list[dict[str, Any]], texts: list[str]) -> list[dict[str, Any]]:
    segs = []
    for u, txt in zip(units, texts):
        for s in split_sentences(txt, u["start"], u["end"]):
            seg = {"spk": u["spk"], **s}
            if u.get("track"):
                seg["track"] = u["track"]
            segs.append(seg)
    return segs


def device() -> str:
    """Apple 芯片用 GPU (MPS); 有 CUDA 用 CUDA; 否则 CPU。"""
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda:0"
    return "cpu"


def load_model(models: Path):
    import torch
    from qwen_asr import Qwen3ASRModel

    dev = device()
    dtype = torch.float16 if dev != "cpu" else torch.float32
    return dev, Qwen3ASRModel.from_pretrained(str(models / QWEN_DIR), dtype=dtype, device_map=dev,
                                              max_inference_batch_size=BATCH)


def _free_cache() -> None:
    """每批完了还回 GPU 缓存: 每段长短不一, MPS 按每种大小各留一份缓存, 不还会越攒越多。"""
    try:
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def transcribe(model, wavs: dict, units: list[dict[str, Any]], hotwords: str, on_progress=None, language: str = "Chinese") -> list[str]:
    """按 BATCH 一批批送, 每批报一次进度 (界面显示「约 x%」)。language: Qwen3-ASR 的语言名 (学习外语时是 English / Japanese …)。"""
    texts: list[str] = []
    for i in range(0, len(units), BATCH):
        batch = units[i: i + BATCH]
        audio = []
        for u in batch:
            wav = wavs[u.get("track")]
            a = max(0, int((u["start"] - PAD_S) * SR))
            b = min(len(wav), int((u["end"] + PAD_S) * SR))
            audio.append((wav[a:b], SR))
        res = model.transcribe(audio=audio, context=hotwords.strip(), language=language)
        texts += [(r.text or "").strip() for r in res]
        _free_cache()
        if on_progress:
            on_progress(min(len(units), i + BATCH), len(units))
    return texts


def main(argv: list[str] | None = None) -> int:
    import meeting_asr as ma   # 同目录: 事件格式、拼录音、跟着 Companion 退

    saved = (ma._EVENTS, sys.stdout)
    ma._EVENTS, sys.stdout = sys.stdout, sys.stderr   # 事件走 stdout, 库自己 print 的都赶到 stderr
    try:
        return _run(ma, argv)
    finally:
        ma._EVENTS, sys.stdout = saved


#: 学习外语 (11/18 第 5 步) 的语言代码 → Qwen3-ASR 的语言名
CLIP_LANGUAGES = {"en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish",
                  "ru": "Russian", "zh": "Chinese"}


def clip_units(duration: float) -> list[dict[str, Any]]:
    """一整段录音 (语音输入) → 送给模型的小段: 不分说话人, 按 ≤ 20 秒平均切。"""
    if duration <= 0:
        return []
    return _split_long({"start": 0.0, "end": float(duration), "spk": 0, "track": None})


def _run_clip(ma, args) -> int:
    """--clip: 语音输入那一小段 (学习外语时日语等默认组件认不了的语言), 出 {"text"}。"""
    lang = CLIP_LANGUAGES.get(args.language, "")
    if not lang:
        ma.emit("error", message=f"不支持的语言: {args.language}")
        return 1
    try:
        if not (args.models / QWEN_DIR / "config.json").is_file():
            raise RuntimeError(f"高精度组件不全: {args.models / QWEN_DIR} (重新安装高精度组件)")
        ma.emit("phase", phase="loading")
        _, model = load_model(args.models)
        import soundfile as sf

        with tempfile.TemporaryDirectory(prefix="catfish-clip-") as td:
            wav_path = Path(td) / "clip-16k.wav"
            duration = ma.concat_to_16k(ma.list_segments(args.audio_dir), wav_path)
            wav = sf.read(str(wav_path), dtype="float32")[0]
        ma.emit("phase", phase="recognizing", duration_secs=round(duration, 1))
        texts = transcribe(model, {None: wav}, clip_units(duration), "", language=lang)
        text = " ".join(t for t in texts if t).strip() if lang not in ("Chinese", "Japanese", "Korean") else "".join(texts).strip()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"text": text, "language": lang, "duration_secs": round(duration, 1)}, ensure_ascii=False),
                            encoding="utf-8")
        ma.emit("done", out=str(args.out), segments=1, speakers=0, duration_secs=round(duration, 1))
        return 0
    except Exception as e:  # noqa: BLE001
        ma.emit("error", message=f"{type(e).__name__}: {e}")
        return 1


def _run(ma, argv: list[str] | None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", action="store_true", help="语音输入的一小段 (不要 --transcript)")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--transcript", type=Path)
    ap.add_argument("--audio-dir", required=True, type=Path)
    ap.add_argument("--models", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--hotwords", default="")
    args = ap.parse_args(argv)
    ma.lower_priority()
    ma.exit_with_parent()
    if args.clip:
        return _run_clip(ma, args)
    if args.transcript is None:
        ma.emit("error", message="要 --transcript (或 --clip)")
        return 1
    try:
        base = json.loads(args.transcript.read_text(encoding="utf-8"))
        units = units_from_sentences(base.get("segments") or [])
        if not units:
            raise RuntimeError("这场会议的转写是空的, 没法高精度重转")
        if not (args.models / QWEN_DIR / "config.json").is_file():
            raise RuntimeError(f"高精度组件不全: {args.models / QWEN_DIR} (重新安装高精度组件)")
        t0 = time.time()
        ma.emit("phase", phase="loading")
        dev, model = load_model(args.models)
        ma.emit("phase", phase="preparing")
        import soundfile as sf

        wavs: dict = {}
        with tempfile.TemporaryDirectory(prefix="catfish-meeting-hq-") as td:
            mic = Path(td) / "mic-16k.wav"
            duration = ma.concat_to_16k(ma.list_segments(args.audio_dir), mic)
            wavs[None] = wavs["mic"] = sf.read(str(mic), dtype="float32")[0]
            sys_dir = args.audio_dir / ma.SYSTEM_TRACK_DIR
            if any(u.get("track") == "system" for u in units) and ma.list_segments(sys_dir):
                sw = Path(td) / "system-16k.wav"
                ma.concat_to_16k(ma.list_segments(sys_dir), sw)
                wavs["system"] = sf.read(str(sw), dtype="float32")[0]
        ma.emit("phase", phase="refining", duration_secs=round(duration, 1), units=len(units))
        texts = transcribe(model, wavs, units, args.hotwords,
                           lambda done, total: ma.emit("phase", phase="refining", done=done, total=total))
        segs = build_segments(units, texts)
        if base.get("diarization") is not None or any(s.get("spk") for s in segs):
            segs = ma.smooth_short(segs)
        ma.emit("phase", phase="writing")
        doc = {**base, "segments": segs, "text": "".join(s["text"] for s in segs),
               "engine": {**(base.get("engine") or {}), "asr": ENGINE_NAME, "hq": True, "device": dev},
               "hq_elapsed_secs": round(time.time() - t0, 1), "hq_units": len(units)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, args.out)
        ma.emit("done", out=str(args.out), segments=len(segs), speakers=doc.get("speakers", 0),
                duration_secs=doc.get("duration_secs", round(duration, 1)))
        return 0
    except Exception as e:  # noqa: BLE001 —— 任何失败都要变成一条 error 事件给界面
        ma.emit("error", message=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
