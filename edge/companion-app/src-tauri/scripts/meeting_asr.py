"""会议录音转写 (10/1, docs/MEETING-MINUTES-PLAN.md §3).

由 Companion 用会议组件包的 venv 跑 (~/.catfish/meeting-asr/venv-<ver>/), 不是 hermes venv。
FunASR: FSMN-VAD + SeACo-Paraformer (支持热词) + CT-Transformer 标点 + CAM++ 说话人。

    python meeting_asr.py --audio-dir <meeting>/audio --models <models 目录> \
        --speakers 4 --hotwords "资质集采 达华" --out <meeting>/transcript.json

    # 语音输入 / 上传的音频文件: 单个 16k wav, 不分说话人
    python meeting_asr.py --audio-file x.wav --models <models 目录> --speakers 0 --out t.json

    # 线上会议 (11/11): audio/ 下是麦克风, audio/system/ 下是会议软件放出来的声音 (对方)。
    # --local-speakers = 这台电脑前说话的人数 (含自己), 其余 = 对方
    python meeting_asr.py --audio-dir <meeting>/audio --models <models> --speakers 5 --local-speakers 1 ...

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
- 线上会议两条音轨分开识别 (11/11): 麦克风那条只有本地的人, 对方那条只有对方 —— 说话人
  不会跨两边认错, 本地只有 1 人时那一条就是「我」。外放开会时麦克风也会收进对方的声音 (回声),
  跟对方那条同一时间、内容几乎一样的句子从麦克风那边去掉 (drop_echo), 纪要里不会重复。
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


SYSTEM_TRACK_DIR = "system"


def _norm(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def drop_echo(mic: list[dict], system: list[dict], *, min_ratio: float = 0.6, window: float = 1.0) -> tuple[list[dict], int]:
    """麦克风那条里, 跟对方那条同时说的、字几乎一样的句子是回声 (外放时喇叭里的对方被麦克风收进来)。

    拿麦克风这句, 跟对方在这句前后 window 秒内说的**所有**话连起来比 —— 两边断句不一样
    (对方那条把「另外」断到上一句末尾, 麦克风这条把它放在下一句开头), 只跟对方某一句比会漏。
    本句的字有 min_ratio 以上按顺序出现在那段话里 → 回声。只看文字不看时间会误删「我复述对方的话」,
    所以只比同一时间段的。很短的句子 (≤3 个字, 「好的」) 字太少比不准: 对方同时在说、且有字对得上就算回声。
    返回 (留下的, 去掉了几句)。
    """
    from difflib import SequenceMatcher

    kept, dropped = [], 0
    for m in mic:
        mt = _norm(m["text"])
        lo, hi = m["start"] - window, m["end"] + window
        near = [s for s in system if s["end"] > lo and s["start"] < hi]
        st = "".join(_norm(s["text"]) for s in near)
        echo = False
        if mt and st:
            sm = SequenceMatcher(None, mt, st, autojunk=False)
            matched = sum(b.size for b in sm.get_matching_blocks())
            if len(mt) <= 3:
                dur = max(m["end"] - m["start"], 0.01)
                talking = sum(_overlap(m, s) for s in near)
                echo = matched > 0 and talking >= 0.5 * dur
            else:
                echo = matched / len(mt) >= min_ratio
        if echo:
            dropped += 1
        else:
            kept.append(m)
    return kept, dropped


def merge_tracks(mic: list[dict], system: list[dict]) -> list[dict]:
    """两条按时间排成一份; 对方那条的说话人编号接在本地后面 (本地 0..k-1, 对方 k..)。"""
    offset = (max((s["spk"] for s in mic), default=-1) + 1) if mic else 0
    out = [{**s, "track": "mic"} for s in mic]
    out += [{**s, "spk": s["spk"] + offset, "track": "system"} for s in system]
    out.sort(key=lambda s: (s["start"], s["end"]))
    return out


def pad_to(path: Path, seconds: float) -> None:
    """16k 单声道 wav 补静音到 seconds 长 (两条对齐; 录音那边已按时间补过, 这里兜最后一点差)。"""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32")
    need = int(round(seconds * sr)) - len(data)
    if need > 0:
        sf.write(str(path), np.concatenate([data, np.zeros(need, dtype="float32")]), sr, subtype="PCM_16")


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


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x00000102   # WAIT_TIMEOUT = 还在跑
        finally:
            k32.CloseHandle(h)
    return os.getppid() == pid   # macOS / Linux: 父进程没了会被过继给 1 (或 launchd), ppid 就变了


def exit_with_parent(interval: float = 5.0) -> None:
    """Companion 关了 / 重启了 (开发版改代码自动重启) 就自己退出 —— 原来转写进程会被留下接着跑 (ppid=1),
    界面不知道它, 员工再点「重新转写」又起一个: 11/17 实测同一场会四个转写一起跑, 互相抢 CPU、最后还会互相覆盖结果。"""
    import threading

    parent = os.getppid()
    if parent <= 1:
        return

    def watch() -> None:
        while True:
            time.sleep(interval)
            if not _parent_alive(parent):
                sys.stderr.write("[meeting_asr] Companion 已退出, 转写跟着停\n")
                os._exit(3)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


# ── 句子时间戳 (11/16) ──────────────────────────────────────────────
#
# FunASR 1.4.16 的 sentence_info 会越往后越早: 一场 18 分钟的会, 第 400 秒说的「你们都写成 CBT…」被标在 361 秒,
# 两个多小时的会后面差好几分钟 —— 会议页点 ▶ 听那一句, 放的是一两分钟前的话 (鸿波 11/16「文字滞后声音」)。
# 查下来逐字时间戳 (result["timestamp"]) 是准的 (第 1616 个字 400.9 秒), 字数也跟它一一对得上; 错在它拼句子那一步
# (timestamp_sentence 的回退路径)。说话人也是按那个错的句子时间分的。
# 所以: 截下它内部的说话人分段 (distribute_spk 的入参), 自己按标点切句子、句子起止取首尾字的时间, 再按重叠分说话人。
# 字和时间戳对不上 (少见的字符) 就退回它原来的 sentence_info。

_TOKEN_RE = None
SENTENCE_PUNCT = set("，。？！、；：,.?!;:…")


def _tokens(text: str) -> list[tuple[int, int]]:
    """跟 Paraformer 逐字时间戳一一对应的单位: 一个汉字 / 一个英文词 / 一串数字。返回 (起, 止) 下标。"""
    global _TOKEN_RE
    import re

    if _TOKEN_RE is None:
        _TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)?|\d+")
    return [m.span() for m in _TOKEN_RE.finditer(text)]


#: 字数跟时间戳个数差多少以内还按位置对齐 (差太多说明结果本身不对, 退回 FunASR 的句子)
MAX_MISMATCH = 0.1


def align_timestamps(n_tokens: int, timestamps: list) -> list | None:
    """字数跟时间戳个数对不上 (少见的字符: 全角数字、假名、拆开的英文…) 时按位置一一对上:
    第 i 个字取第 round(i × (时间戳数-1) / (字数-1)) 个时间戳。差几个字只在局部错开不到一秒, 不会越往后越偏
    (FunASR 自己拼句子就是越往后越偏)。差超过一成 → None。"""
    m = len(timestamps)
    if not n_tokens or not m:
        return None
    if n_tokens == m:
        return timestamps
    if abs(n_tokens - m) > max(2, MAX_MISMATCH * max(n_tokens, m)):
        return None
    if n_tokens == 1:
        return [[timestamps[0][0], timestamps[-1][1]]]
    return [timestamps[round(i * (m - 1) / (n_tokens - 1))] for i in range(n_tokens)]


def sentences_from_tokens(text: str, timestamps: list, sd: list | None) -> list[dict] | None:
    """按标点切句子, 起止取句中首尾字的时间 (毫秒 → 秒); sd = 说话人分段 [(起秒, 止秒, spk)], 按重叠最多分。
    字数跟时间戳个数对不上就按位置对齐 (align_timestamps); 差太多 → None (调用方退回 FunASR 的 sentence_info)。"""
    spans = _tokens(text)
    if not spans:
        return None
    if len(spans) != len(timestamps):
        aligned = align_timestamps(len(spans), timestamps)
        if aligned is None:
            return None
        sys.stderr.write(f"[meeting_asr] 字数 {len(spans)} / 时间戳 {len(timestamps)} 对不上, 按位置对齐\n")
        timestamps = aligned
    out, first, last_end = [], 0, 0
    for i, (a, b) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        tail = text[b:nxt]
        if any(ch in SENTENCE_PUNCT for ch in tail) or i + 1 == len(spans):
            body = text[last_end:b] + "".join(ch for ch in tail if ch in SENTENCE_PUNCT)[:1]
            seg = {"start": round(timestamps[first][0] / 1000, 2), "end": round(timestamps[i][1] / 1000, 2),
                   "text": body.strip(), "spk": 0}
            if seg["text"]:
                out.append(seg)
            first, last_end = i + 1, nxt
    if sd:
        for seg in out:
            best, best_ov = 0, 0.0
            for st, ed, spk in sd:
                ov = max(0.0, min(seg["end"], ed) - max(seg["start"], st))
                if ov > best_ov:
                    best, best_ov = int(spk), ov
            seg["spk"] = best
    return out


class SpeakerTap:
    """截 FunASR 内部的说话人分段 (auto_model.distribute_spk 的第二个参数), 不改它别的行为。"""

    def __init__(self) -> None:
        self.segments: list | None = None

    def __enter__(self):
        from funasr.auto import auto_model as am

        self._am, self._orig = am, am.distribute_spk

        def tap(sentence_list, sd_time_list):
            self.segments = [(float(a), float(b), int(c)) for a, b, c in sd_time_list]
            return self._orig(sentence_list, sd_time_list)

        am.distribute_spk = tap
        return self

    def __exit__(self, *exc):
        self._am.distribute_spk = self._orig
        return False


# ── 11/17 说话人分得更准 (鸿波「模型是不是对多人的混音没有处理能力」) ─────────────────────────
# FunASR 给了人数 (preset_spk_num) 时: 声纹片段 < 2048 个走谱聚类、≥ 2048 个 (长会议, 半小时以上) 走 **KMeans 硬分成 N 簇**,
# 而且给了人数就跳过它自己的「声音很像的并掉」那步 —— 实测 CCRC 两小时会议填 10 人, 10 个说话人各 112–324 句, 平均得不像真会议
# (真会议常是两三个人说大半), 同一个人被拆成好几个编号凑满 10 个。
# 现在把员工填的人数当**上限**: 先照原样分成 N 簇, 再 ① 声纹中心很像的两簇并成一个人 ② 占比极小的零碎簇 (咳嗽、搭一两句)
# 并给声音最像的那簇。不加载新模型, 不多花时间。

#: 两簇声纹中心余弦相似度 ≥ 这个 → 同一个人 (FunASR 自己不给人数时用 0.78; 按 CCRC 实测调, 见 docs/MEETING-MINUTES-PLAN.md §10)
MERGE_COS = 0.78
#: 一簇的片段数占全部不到这个比例 → 零碎簇, 并给最像的
MIN_CLUSTER_SHARE = 0.02


def _centers(labels, X):
    import numpy as np

    ids = sorted(set(int(v) for v in labels))
    c = np.stack([X[labels == i].mean(0) for i in ids])
    return ids, c / np.linalg.norm(c, axis=1, keepdims=True)


def refine_labels(labels, X, merge_cos: float = MERGE_COS, min_share: float = MIN_CLUSTER_SHARE):
    """N 当上限: 并掉声音几乎一样的簇、零碎簇并给最像的; 编号按首次出现重排 (0, 1, 2…)。labels / X 是 numpy。"""
    import numpy as np

    labels = np.asarray(labels).astype(int).copy()
    X = np.asarray(X, dtype=float)
    if len(labels) < 2 or len(set(labels.tolist())) < 2:
        return np.zeros(len(labels), dtype=int)
    while True:   # ① 最像的两簇够像就并, 直到没有够像的
        ids, c = _centers(labels, X)
        if len(ids) < 2:
            break
        sim = c @ c.T
        np.fill_diagonal(sim, -1.0)
        a, b = np.unravel_index(int(np.argmax(sim)), sim.shape)
        if sim[a, b] < merge_cos:
            break
        labels[labels == ids[b]] = ids[a]
    while True:   # ② 最小的那簇太碎就并给最像的 (按片段逐个归到最近的大簇中心)
        ids, c = _centers(labels, X)
        if len(ids) < 2:
            break
        counts = np.array([(labels == i).sum() for i in ids])
        small = int(np.argmin(counts))
        if counts[small] >= max(1, min_share * len(labels)):
            break
        keep = [j for j in range(len(ids)) if j != small]
        mask = labels == ids[small]
        xs = X[mask] / np.linalg.norm(X[mask], axis=1, keepdims=True)
        labels[mask] = np.array(ids)[keep][np.argmax(xs @ c[keep].T, axis=1)]
    order: dict[int, int] = {}
    for v in labels.tolist():
        order.setdefault(v, len(order))
    return np.array([order[v] for v in labels.tolist()], dtype=int)


class ClusterRefine:
    """包住 FunASR 的 cb_model (说话人聚类), 在它的结果上做 refine_labels。不改 FunASR 别的行为。"""

    def __init__(self, model) -> None:
        self.model = model
        self.before: int | None = None
        self.after: int | None = None

    def __enter__(self):
        orig = getattr(self.model, "cb_model", None)
        self._orig = orig
        if orig is None:
            return self

        def cb(X, oracle_num=None, **kw):
            labels = orig(X, oracle_num=oracle_num, **kw)
            try:
                x = X.detach().cpu().numpy() if hasattr(X, "detach") else X
                self.before = len(set(int(v) for v in labels))
                out = refine_labels(labels, x)
                self.after = len(set(out.tolist()))
                return out
            except Exception as e:  # noqa: BLE001 — 细化失败就用原来的分法, 不能让转写失败
                sys.stderr.write(f"[meeting_asr] 说话人细化失败, 用原来的分法: {type(e).__name__}: {e}\n")
                return labels

        self.model.cb_model = cb
        return self

    def __exit__(self, *exc):
        if self._orig is not None:
            self.model.cb_model = self._orig
        return False


#: 这么短的句子 (字数) 声纹靠不住: 前后两句是同一个人时归给他
SHORT_TOKENS = 4


def smooth_short(segs: list[dict]) -> list[dict]:
    """「对」「嗯」「是的」这种几个字的句子, 声纹片段太短分不准; 夹在同一个人的两句中间 (间隔都不到 2 秒) 就归给他。
    前后不是同一个人的不动 (多半真是别人搭话)。"""
    for i in range(1, len(segs) - 1):
        s, p, n = segs[i], segs[i - 1], segs[i + 1]
        if s.get("track") != p.get("track") or s.get("track") != n.get("track"):
            continue
        if len(_tokens(s["text"])) > SHORT_TOKENS or p["spk"] != n["spk"] or s["spk"] == p["spk"]:
            continue
        if s["start"] - p["end"] < 2.0 and n["start"] - s["end"] < 2.0:
            s["spk"] = p["spk"]
    return segs


def segments_of(result: dict, sd: list | None = None) -> list[dict]:
    """一次识别的结果 → 句子 [{spk, start, end, text}]: 优先自己按逐字时间戳切, 对不上再用 FunASR 的 sentence_info。"""
    ts = result.get("timestamp")
    text = result.get("text") or ""
    if isinstance(ts, list) and ts and text:
        rebuilt = sentences_from_tokens(text, ts, sd)
        if rebuilt is not None:
            return rebuilt
        sys.stderr.write(f"[meeting_asr] 逐字时间戳差太多 ({len(_tokens(text))} 字 / {len(ts)} 个时间戳), 用 FunASR 的句子时间\n")
    return to_segments(result)


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
    exit_with_parent()
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--audio-dir", type=Path)
    src.add_argument("--audio-file", type=Path)
    ap.add_argument("--models", required=True, type=Path)
    # 0 = 不分说话人 (语音输入 / 上传音频): 不加载 CAM++, 也不传 preset_spk_num
    ap.add_argument("--speakers", required=True, type=int)
    # 线上会议: 这台电脑前说话的人数 (含自己)。只在有 audio/system/ 时用; 0 = 没说 (按 1)
    ap.add_argument("--local-speakers", type=int, default=0)
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

        system_segments = list_segments(args.audio_dir / SYSTEM_TRACK_DIR) if args.audio_dir else []
        emit("phase", phase="preparing")
        tracks_info = None
        diar_info = None
        with tempfile.TemporaryDirectory(prefix="catfish-meeting-") as td:
            merged = Path(td) / "merged-16k.wav"
            duration = concat_to_16k(segments, merged)
            base_kw = {"batch_size_s": 300}
            if args.hotwords.strip():
                base_kw["hotword"] = args.hotwords.strip()
            if system_segments and with_spk:
                sys_wav = Path(td) / "system-16k.wav"
                sys_duration = concat_to_16k(system_segments, sys_wav)
                duration = max(duration, sys_duration)
                pad_to(merged, duration)
                pad_to(sys_wav, duration)
                local = max(1, min(args.local_speakers or 1, args.speakers))
                remote = max(1, args.speakers - local)
                emit("phase", phase="recognizing", duration_secs=round(duration, 1))
                with SpeakerTap() as tap_mic, ClusterRefine(model):
                    mic_res = model.generate(input=str(merged), preset_spk_num=local, **base_kw)[0]
                with SpeakerTap() as tap_sys, ClusterRefine(model):
                    sys_res = model.generate(input=str(sys_wav), preset_spk_num=remote, **base_kw)[0]
                sys_segs = smooth_short(segments_of(sys_res, tap_sys.segments))
                mic_segs, dropped = drop_echo(smooth_short(segments_of(mic_res, tap_mic.segments)), sys_segs)
                segs = merge_tracks(mic_segs, sys_segs)
                text = "".join(s["text"] for s in segs)
                tracks_info = {"local_speakers": local, "remote_speakers": remote, "echo_dropped": dropped}
            else:
                emit("phase", phase="recognizing", duration_secs=round(duration, 1))
                kw = dict(base_kw)
                if with_spk:
                    kw["preset_spk_num"] = args.speakers
                refine = ClusterRefine(model if with_spk else None)
                with SpeakerTap() as tap, refine:
                    result = model.generate(input=str(merged), **kw)[0]
                segs = segments_of(result, tap.segments if with_spk else None)
                if with_spk:
                    segs = smooth_short(segs)
                    diar_info = {"clusters_before": refine.before, "clusters_after": refine.after}
                text = (result.get("text") or "").strip() or "".join(s["text"] for s in segs)

        emit("phase", phase="writing")
        doc = {
            "text": text,
            "version": 1,
            "sentence_times": "tokens",   # 11/16 起: 句子时间按逐字时间戳重切 (之前的转写越往后越早)
            "engine": {"name": "funasr", "asr": "seaco-paraformer-large", "spk": "cam++", "spk_refine": 1},
            "duration_secs": round(duration, 1),
            "speakers": len({s["spk"] for s in segs}) if with_spk else 0,
            "speakers_requested": args.speakers,
            "hotwords": args.hotwords.split() if args.hotwords.strip() else [],
            "elapsed_secs": round(time.time() - t0, 1),
            "segments": segs,
        }
        if diar_info:
            doc["diarization"] = diar_info   # 11/17: 按上限 N 先分、再并掉同一个人 —— 并前 / 并后几簇
        if tracks_info:
            doc["tracks"] = tracks_info
            # 本地只有一个人 → 麦克风那条的说话人就是员工自己, 先替他起好名字 (可以改)
            if tracks_info["local_speakers"] == 1 and any(s["track"] == "mic" for s in segs):
                doc["speaker_hints"] = {"0": "我"}
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
