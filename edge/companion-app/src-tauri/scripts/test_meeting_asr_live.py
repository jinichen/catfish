"""会中实时转写的断句 / 提交 (11/15)。只测纯逻辑 (不加载 FunASR 模型); 真模型的端到端见提交说明
(拿一场真会议的 120 秒录音按 4 倍速写进分片, 边写边认: 42 句、首尾相接无重叠无缺口, 标点比会后整场转写还好)。"""
from meeting_asr_live import LONE_SENTENCE, SILENCE_SKIP, split_commit


def s(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_除了最后一句都提交_光标挪到倒数第二句末尾():
    keep, adv, partial = split_commit([s(0.5, 3, "第一句，"), s(3.2, 7, "第二句。"), s(7.5, 9.8, "第三句被截")], 10.0, False)
    assert [k["text"] for k in keep] == ["第一句，", "第二句。"]
    assert adv == 7 and partial == "第三句被截"


def test_只有一句_不到45秒等下一轮_到了就提交():
    assert split_commit([s(0, 20, "一个人一直说")], 20.0, False) == ([], None, "一个人一直说")
    keep, adv, partial = split_commit([s(0, 46, "一个人一直说")], LONE_SENTENCE + 1, False)
    assert len(keep) == 1 and adv == 46 and partial == ""


def test_静音_30秒以上跳过_收尾全提交():
    assert split_commit([], 10.0, False) == ([], None, "")
    keep, adv, _ = split_commit([], SILENCE_SKIP + 5, False)
    assert keep == [] and adv == SILENCE_SKIP + 3            # 留 2 秒, 别把刚开口的半个字跳过去
    keep, adv, partial = split_commit([s(0, 2, "甲。"), s(2.5, 4, "乙")], 4.2, True)
    assert len(keep) == 2 and adv == 4.2 and partial == ""
