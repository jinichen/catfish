"""会中实时转写 (11/15, docs/MEETING-MINUTES-PLAN.md §8 会中小鲶)。

录音进行时由 Companion 起这个常驻进程 (会议组件包的 venv, 跟 meeting_asr.py 同一套模型), 模型只加载一次
(~17 秒), 之后每 --step 秒把各条音轨新录到的部分识别一遍 (SeACo-Paraformer + VAD + 标点, 不分说话人):

    python meeting_asr_live.py --audio-dir <meeting>/audio --models <models> --hotwords "资质集采 达华" \
        --out-dir <meeting>

写两个文件 (都在会议目录, 只在本机):
    live_transcript.jsonl  一行一句 {"start": 秒, "end": 秒, "text": ..., "track": "mic" | "system"}, 只追加
    live_state.json        {"status": loading|running|lagging|done|error, "lag_secs", "partial", "updated", ...}

stdin 关掉 (Companion 结束录音 / Companion 退出) = 收尾: 剩下的全部识别、提交, 写 status=done 后退出。

几个决定 (11/15 实测, 30 秒音频 2 线程 1.5 秒出字):
- 窗口切在句子边界: 每次把「上次提交到哪」到「现在」整段送进去, 除了最后一句都提交 (最后一句可能被录音
  截断, 下次连同后面的一起重认); 只有一句且窗口已经 45 秒以上就也提交, 一直没人说话就跳过静音。
- 一次最多认 90 秒: 机器慢、落后了就分几口追, 落后超过 60 秒在界面上说「跟不上」。
- 只用 2 个线程、nice 10: 开会时电脑还要跑会议软件, 不能被转写占满。
- 不分说话人: 说话人要在整场录音上聚类才准, 会后照常整场重转 (meeting_asr.py), 这份只给会中用。
- 线上会议 (audio/system/ 有对方的声音): 两条分开认, 先认对方那条, 麦克风这条里跟对方同时、字几乎一样的
  句子是外放回声, 去掉 (drop_echo, 跟会后同一个判断)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meeting_asr as base  # noqa: E402 — 同目录, 复用 emit / drop_echo / list_segments

TARGET_SR = base.TARGET_SR
MIN_WINDOW = 6.0       # 新录的不到 6 秒先不认 (太短断不出句子)
MAX_WINDOW = 90.0      # 一次最多认 90 秒, 落后了分几口追
LONE_SENTENCE = 45.0   # 窗口里只有一句、已经 45 秒: 这句也提交 (一个人一直说没停顿)
SILENCE_SKIP = 30.0    # 30 秒都没认出字: 跳过这段静音
LAG_WARN = 60.0
TAIL_GUARD = 0.3
MAX_FAILURES = 5       # 连续这么多段都认不了才放弃 (偶尔一段出错就跳过它)       # 文件最后不到一秒可能还没 flush 完, 留一点不认


class Track:
    """一条音轨 = 目录下按顺序的 seg-*.wav (设备原生采样率, 录音线程每秒回写一次 WAV 头)。"""

    def __init__(self, name: str, directory: Path) -> None:
        self.name, self.dir = name, directory
        self.cursor = 0.0   # 已提交到第几秒 (音轨时间)
        self.partial = ""

    def files(self) -> list[tuple[Path, int, int]]:
        import soundfile as sf

        out = []
        for p in base.list_segments(self.dir) if self.dir.is_dir() else []:
            try:
                info = sf.info(str(p))
            except Exception:  # noqa: BLE001 — 刚建出来、头还没写好: 下一轮再看
                continue
            out.append((p, info.samplerate, info.frames))
        return out

    def length(self) -> float:
        return sum(frames / sr for _, sr, frames in self.files())

    def read(self, start: float, end: float):
        """[start, end) 秒 → 16k 单声道 float32。跨分片拼起来。"""
        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio.functional as AF

        parts, t = [], 0.0
        for p, sr, frames in self.files():
            dur = frames / sr
            lo, hi = max(start, t), min(end, t + dur)
            if hi > lo:
                data, _ = sf.read(str(p), dtype="float32", start=int((lo - t) * sr), stop=int((hi - t) * sr), always_2d=True)
                mono = data.mean(axis=1)
                if sr != TARGET_SR:
                    mono = AF.resample(torch.from_numpy(mono), sr, TARGET_SR).numpy()
                parts.append(mono)
            t += dur
        return np.concatenate(parts) if parts else np.zeros(0, dtype="float32")


def split_commit(sentences: list[dict], window: float, final: bool) -> tuple[list[dict], float | None, str]:
    """一次识别结果 (句子时间是窗口内的秒) → (要提交的句子, 光标往前挪几秒, 还没提交的那截文字)。

    挪 None = 不动 (等下一轮连同新录的一起认)。"""
    if not sentences:
        if final or window >= SILENCE_SKIP:
            return [], max(0.0, window - (0.0 if final else 2.0)), ""
        return [], None, ""
    if final:
        return sentences, window, ""
    keep = sentences[:-1]
    if not keep and window >= LONE_SENTENCE:
        keep = sentences
    if not keep:
        return [], None, sentences[-1]["text"]
    rest = "".join(s["text"] for s in sentences[len(keep):])
    return keep, keep[-1]["end"], rest


def recognize(model, audio, hotwords: str) -> list[dict]:
    kw = {"batch_size_s": 300, "sentence_timestamp": True}
    if hotwords.strip():
        kw["hotword"] = hotwords.strip()
    results = model.generate(input=audio, **kw)
    if not results:   # 11/16: 一整段都没人说话, VAD 一句都没切出来时 FunASR 返回空列表 (原来 [0] 直接 IndexError, 整场没了实时文字)
        return []
    # 句子时间按逐字时间戳重切 (meeting_asr.segments_of; FunASR 自己拼的句子时间会往前偏)
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in base.segments_of(results[0]) if s["text"].strip()]


class Live:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out = args.out_dir / "live_transcript.jsonl"
        self.state_path = args.out_dir / "live_state.json"
        self.tracks = [Track("system", args.audio_dir / base.SYSTEM_TRACK_DIR), Track("mic", args.audio_dir)]
        self.recent_system: list[dict] = []   # 最近认出的对方的话, 给麦克风那条去回声
        self.lines = 0
        self.stopping = threading.Event()
        self.load_secs = 0.0
        self.failures = 0   # 连着认失败几次 (偶尔一次跳过那段; 一直失败说明真坏了, 报错退出)

    def state(self, status: str, **kw) -> None:
        lag = max((t.length() - t.cursor for t in self.tracks if t.files()), default=0.0)
        doc = {"status": status, "lag_secs": round(max(0.0, lag), 1), "lines": self.lines, "load_secs": self.load_secs,
               "partial": "".join(t.partial for t in self.tracks), "updated": time.time(), **kw}
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def append(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.out.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.lines += len(rows)

    def step(self, model, final: bool) -> None:
        """每条音轨: 从光标认到现在。落后了 (超过 MAX_WINDOW) 就一口一口追; final = 收尾, 剩下的全提交。"""
        for t in self.tracks:
            if not t.files():
                continue
            while True:
                avail = t.length() - (0.0 if final else TAIL_GUARD) - t.cursor
                if avail <= 0.2 or (avail < MIN_WINDOW and not final):
                    break
                window = min(avail, MAX_WINDOW)
                catching_up = avail > MAX_WINDOW
                try:
                    sents = recognize(model, t.read(t.cursor, t.cursor + window), self.args.hotwords)
                    self.failures = 0
                except Exception as e:  # noqa: BLE001 — 一段认不了不能让整场实时文字停掉: 跳过这段, 接着认后面的
                    self.failures += 1
                    base.emit("warn", message=f"{type(e).__name__}: {e}", skipped_secs=round(window, 1))
                    if self.failures >= MAX_FAILURES:
                        raise
                    t.cursor += window
                    t.partial = ""
                    continue
                keep, advance, partial = split_commit(sents, window, final and not catching_up)
                if advance is None and catching_up:   # 90 秒里一句都断不出来: 整口提交, 不然永远追不上
                    keep, advance, partial = split_commit(sents, window, True)
                rows = [{"start": round(t.cursor + s["start"], 2), "end": round(t.cursor + s["end"], 2),
                         "text": s["text"], "track": t.name} for s in keep]
                if t.name == "system":
                    self.recent_system = [r for r in self.recent_system + rows if r["end"] > t.cursor - 120]
                elif self.recent_system and rows:
                    rows, _ = base.drop_echo(rows, self.recent_system)
                self.append(rows)
                t.partial = partial
                if advance is None:
                    break
                t.cursor += advance
                if not catching_up and not final:
                    break

    def run(self) -> int:
        import torch

        torch.set_num_threads(max(1, self.args.threads))
        self.state("loading")
        base.emit("phase", phase="loading")
        t0 = time.time()
        from funasr import AutoModel

        model = AutoModel(model=str(self.args.models / "asr"), vad_model=str(self.args.models / "vad"),
                          punc_model=str(self.args.models / "punc"), device="cpu", disable_update=True,
                          disable_pbar=True, log_level="ERROR")
        self.load_secs = round(time.time() - t0, 1)
        base.emit("phase", phase="running", load_secs=self.load_secs)
        while not self.stopping.is_set():
            t1 = time.time()
            self.step(model, final=False)
            lag = max((t.length() - t.cursor for t in self.tracks if t.files()), default=0.0)
            self.state("lagging" if lag > LAG_WARN else "running", step_secs=round(time.time() - t1, 1))
            self.stopping.wait(max(1.0, self.args.step - (time.time() - t1)))
        self.state("finishing")
        self.step(model, final=True)
        for t in self.tracks:
            t.partial = ""
        self.state("done")
        base.emit("done", out=str(self.out), segments=self.lines, speakers=0, duration_secs=0)
        return 0


def watch_stdin(stop: threading.Event) -> None:
    """Companion 关掉 stdin (结束录音 / 自己退出了) → 收尾。"""
    try:
        while sys.stdin.read(1024):
            pass
    except Exception:  # noqa: BLE001
        pass
    stop.set()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    base._EVENTS = sys.stdout
    sys.stdout = sys.stderr   # FunASR import 时往 stdout 打字, 不能混进事件流
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", required=True, type=Path)
    ap.add_argument("--models", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--hotwords", default="")
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    live = Live(args)
    threading.Thread(target=watch_stdin, args=(live.stopping,), daemon=True).start()
    try:
        base.lower_priority()
        for sub in ("vad", "asr", "punc"):
            if not (args.models / sub / "model.pt").is_file() and not any((args.models / sub).glob("*.bin")):
                raise RuntimeError(f"模型不全: {args.models / sub} (会议组件包损坏? 重新安装)")
        return live.run()
    except Exception as e:  # noqa: BLE001 — 失败只影响会中文字, 录音照常
        try:
            live.state("error", message=f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        base.emit("error", message=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
