"""高精度重新转写 (11/17, meeting_asr_qwen.py): 按已有句子切小段 / 句子时间 / 说话人和音轨照旧 / 进度 / 出错。
只测纯逻辑 (不加载模型)。"""
import json
from pathlib import Path

import numpy as np

import meeting_asr_qwen as mq


def seg(start, end, text, spk=0, track=None):
    s = {"spk": spk, "start": start, "end": end, "text": text}
    if track:
        s["track"] = track
    return s


def test_units_join_same_speaker_short_gaps_up_to_20s():
    segs = [seg(0, 3, "a", 1), seg(3.3, 6, "b", 1), seg(6.5, 9, "c", 2), seg(9.2, 20, "d", 2), seg(20.3, 31, "e", 2)]
    units = mq.units_from_sentences(segs)
    # 0–6 同一人连起来; 6.5 换人断开; 6.5–20 同一人; 再接 31 就超 20 秒 → 另起
    assert [(u["start"], u["end"], u["spk"]) for u in units] == [(0, 6, 1), (6.5, 20, 2), (20.3, 31, 2)]


def test_long_sentence_split_into_20s_pieces():
    """Paraformer 偶尔把一句标成几分钟 (CCRC「你说h是进行中，」407 秒): 平均切成 ≤ 20 秒, 不再一次塞给 Qwen3。"""
    units = mq.units_from_sentences([seg(100, 507, "你说h是进行中，", 1)])
    assert len(units) == 21 and all(u["end"] - u["start"] <= mq.MAX_UNIT_S + 1e-6 for u in units)
    assert units[0]["start"] == 100 and units[-1]["end"] == 507 and {u["spk"] for u in units} == {1}
    assert mq.units_from_sentences([seg(0, 20, "刚好二十秒")])[0]["end"] == 20


def test_units_break_on_track_and_skip_empty():
    segs = [seg(0, 2, "a", 0, "mic"), seg(2.1, 4, "b", 0, "system"), seg(5, 5, "空", 0, "mic")]
    units = mq.units_from_sentences(segs)
    assert [(u["track"], u["start"]) for u in units] == [("mic", 0), ("system", 2.1)]


def test_split_sentences_spreads_time_by_length():
    out = mq.split_sentences("我们两周内做完。好的！", 10.0, 20.0)
    assert [s["text"] for s in out] == ["我们两周内做完。", "好的！"]
    assert out[0]["start"] == 10.0 and out[-1]["end"] == 20.0 and out[0]["end"] > 16.0
    assert mq.split_sentences("  ", 0, 1) == []
    assert len(mq.split_sentences("这个影响会非常大，那这里面每一个具体的问题。", 0, 3)) == 1   # 逗号不切


def test_build_segments_keeps_speaker_and_track():
    units = [{"start": 0.0, "end": 5.0, "spk": 2, "track": "system"}, {"start": 6.0, "end": 9.0, "spk": 0, "track": None}]
    segs = mq.build_segments(units, ["先说整改。", "可以。"])
    assert segs == [{"spk": 2, "start": 0.0, "end": 5.0, "text": "先说整改。", "track": "system"},
                    {"spk": 0, "start": 6.0, "end": 9.0, "text": "可以。"}]


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, context, language):
        self.calls.append((len(audio), [len(a[0]) for a in audio], context, language))
        return [type("R", (), {"text": f"第{len(self.calls)}批{i}。"})() for i in range(len(audio))]


def test_transcribe_batches_pads_and_reports_progress():
    wav = np.zeros(mq.SR * 60, dtype="float32")
    units = [{"start": float(i * 5), "end": float(i * 5 + 2), "spk": 0, "track": None} for i in range(6)]
    m, prog = FakeModel(), []
    texts = mq.transcribe(m, {None: wav}, units, " CCRC 金智达 ", lambda d, t: prog.append((d, t)))
    assert len(texts) == 6 and [c[0] for c in m.calls] == [4, 2]
    assert m.calls[0][1][1] == int((7 + mq.PAD_S) * mq.SR) - int((5 - mq.PAD_S) * mq.SR)   # 5–7 秒那段, 前后各多给 0.15 秒
    assert m.calls[0][2] == "CCRC 金智达" and m.calls[0][3] == "Chinese"
    assert prog == [(4, 6), (6, 6)]


def test_main_errors_become_events(tmp_path: Path, capsys):
    t = tmp_path / "t.json"
    t.write_text(json.dumps({"segments": []}), encoding="utf-8")
    rc = mq.main(["--transcript", str(t), "--audio-dir", str(tmp_path), "--models", str(tmp_path), "--out", str(tmp_path / "o.json")])
    assert rc == 1
    ev = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert ev[-1]["event"] == "error" and "空" in ev[-1]["message"]


def test_clip_units_and_language_passed():
    """11/18 学习外语: 语音输入一小段按 ≤ 20 秒切, 语言名传给模型。"""
    assert mq.clip_units(0) == []
    us = mq.clip_units(45.0)
    assert len(us) == 3 and us[0]["start"] == 0.0 and us[-1]["end"] == 45.0
    wav = np.zeros(mq.SR * 50, dtype="float32")
    m = FakeModel()
    mq.transcribe(m, {None: wav}, us, "", language="Japanese")
    assert m.calls[0][3] == "Japanese"
    assert mq.CLIP_LANGUAGES["ja"] == "Japanese" and "xx" not in mq.CLIP_LANGUAGES


def test_clip_errors_become_events(tmp_path: Path, capsys):
    rc = mq.main(["--clip", "--language", "xx", "--audio-dir", str(tmp_path), "--models", str(tmp_path), "--out", str(tmp_path / "o.json")])
    assert rc == 1
    ev = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert ev[-1]["event"] == "error" and "不支持的语言" in ev[-1]["message"]
    rc = mq.main(["--clip", "--language", "ja", "--audio-dir", str(tmp_path), "--models", str(tmp_path), "--out", str(tmp_path / "o.json")])
    ev = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert rc == 1 and "高精度组件不全" in ev[-1]["message"]
