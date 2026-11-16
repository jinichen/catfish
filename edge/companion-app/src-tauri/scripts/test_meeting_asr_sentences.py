"""会后 / 实时转写的句子时间 (11/16): FunASR 自己拼的 sentence_info 越往后越早 (18 分钟的会第 400 秒的话标在 361 秒),
改成按逐字时间戳自己切句子、按说话人分段的重叠分说话人。只测纯逻辑; 真录音前后对比见提交说明。"""
from meeting_asr import SpeakerTap, segments_of, sentences_from_tokens


def ts(*starts):
    return [[int(s * 1000), int(s * 1000) + 300] for s in starts]


def test_按标点切_起止取首尾字_英文词和数字各算一个():
    text = "你们都写的CVT百分之80啊，这个材料。好"
    #        你 们 都 写 的 CVT 百 分 之 80 啊 | 这 个 材 料 | 好
    stamps = ts(400, 400.2, 400.4, 400.6, 400.8, 401, 401.4, 401.6, 401.8, 402, 402.2, 405, 405.2, 405.4, 405.6, 410)
    out = sentences_from_tokens(text, stamps, None)
    assert [(s["start"], s["end"], s["text"]) for s in out] == [
        (400.0, 402.5, "你们都写的CVT百分之80啊，"), (405.0, 405.9, "这个材料。"), (410.0, 410.3, "好")]


def test_字数跟时间戳对不上就返回None():
    assert sentences_from_tokens("你好世界", ts(0, 1, 2), None) is None
    assert sentences_from_tokens("", [], None) is None


def test_说话人按时间重叠分():
    out = sentences_from_tokens("甲说话。乙回答。", ts(1, 1.2, 1.4, 5, 5.2, 5.4), [(0.5, 3.0, 2), (4.5, 6.0, 7)])
    assert [s["spk"] for s in out] == [2, 7]


def test_segments_of_对不上时退回FunASR的句子():
    res = {"text": "你好", "timestamp": ts(0), "sentence_info": [{"start": 0, "end": 900, "text": "你好", "spk": 3}]}
    assert segments_of(res) == [{"spk": 3, "start": 0.0, "end": 0.9, "text": "你好"}]
    res2 = {"text": "你好。", "timestamp": ts(0, 0.5), "sentence_info": [{"start": 0, "end": 99999, "text": "你好。"}]}
    assert segments_of(res2) == [{"start": 0.0, "end": 0.8, "text": "你好。", "spk": 0}]


def test_SpeakerTap_截下分段且照常调用原函数(monkeypatch):
    import types
    import sys
    calls = []
    fake = types.SimpleNamespace(distribute_spk=lambda sl, sd: calls.append(sd) or sl)
    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(auto=types.SimpleNamespace(auto_model=fake)))
    monkeypatch.setitem(sys.modules, "funasr.auto", types.SimpleNamespace(auto_model=fake))
    monkeypatch.setitem(sys.modules, "funasr.auto.auto_model", fake)
    with SpeakerTap() as tap:
        fake.distribute_spk([], [(1, 2.5, 3)])
    assert tap.segments == [(1.0, 2.5, 3)] and calls == [[(1, 2.5, 3)]]
    assert fake.distribute_spk is not None and calls
