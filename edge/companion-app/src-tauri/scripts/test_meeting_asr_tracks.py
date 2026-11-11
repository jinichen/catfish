"""线上会议两条音轨合并 (11/11)。只测纯逻辑 (不加载 FunASR 模型); 真模型的端到端见提交说明。

真录音里的样子 (11/11 合成的一场 3 人线上会议, 外放, 回声 0.25 倍延迟 80ms):
对方那条把「另外」断到上一句末尾, 麦克风那条 (回声) 把它放在下一句开头; 对方那条把
「好的」识别成「的那」。只跟对方某一句比会漏掉这两句回声。
"""
from meeting_asr import drop_echo, merge_tracks


def seg(start, end, text, spk=0):
    return {"spk": spk, "start": start, "end": end, "text": text}


SYSTEM = [
    seg(25.6, 26.2, "的那，", 0),
    seg(26.26, 27.9, "就按这个意见下，", 0),
    seg(28.04, 35.6, "周三之前把冻结清单发给我另外。", 0),
    seg(36.73, 37.1, "服务，", 1),
    seg(37.11, 40.0, "器扩容的采购合同也要在月底前签要。", 1),
]


def test_回声去掉_断句不同也认得():
    mic = [
        seg(25.55, 26.0, "好的，"),                  # 对方「好的」的回声, 两边识别得不一样
        seg(26.3, 27.9, "就按这个意见下，"),
        seg(35.27, 36.7, "另外服务器扩容了，"),        # 跨了对方两句
        seg(30.91, 31.5, "没问题。"),                # 我自己说的 (对方那会儿也在说别的)
    ]
    kept, dropped = drop_echo(mic, SYSTEM)
    assert [m["text"] for m in kept] == ["没问题。"]
    assert dropped == 3


def test_我复述对方的话_不在同一时间_不算回声():
    mic = [seg(50.0, 53.0, "就按这个意见，周三之前把冻结清单发给我")]
    kept, dropped = drop_echo(mic, SYSTEM)
    assert dropped == 0 and kept == mic


def test_戴耳机_对方那边没说话时的短句照留():
    kept, dropped = drop_echo([seg(45.0, 45.4, "好的")], SYSTEM)
    assert dropped == 0 and len(kept) == 1


def test_合并_对方编号接在本地后面_按时间排():
    mic = [seg(3.0, 4.0, "我说", 0), seg(1.0, 2.0, "我先说", 0)]
    system = [seg(2.0, 3.0, "对方甲", 0), seg(5.0, 6.0, "对方乙", 1)]
    out = merge_tracks(mic, system)
    assert [(s["text"], s["spk"], s["track"]) for s in out] == [
        ("我先说", 0, "mic"), ("对方甲", 1, "system"), ("我说", 0, "mic"), ("对方乙", 2, "system"),
    ]


def test_合并_麦克风一句没有_对方从零编():
    out = merge_tracks([], [seg(1.0, 2.0, "对方", 0)])
    assert out[0]["spk"] == 0 and out[0]["track"] == "system"
