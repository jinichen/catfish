"""说话人分得更准 (11/17): 人数当上限、并掉同一个人、零碎簇并走、短句跟前后。只测纯逻辑 (不加载 FunASR 模型)。"""
import numpy as np

from meeting_asr import ClusterRefine, refine_labels, smooth_short


def voices(n_spk, per, dim=192, spread=0.35, seed=0):
    """n_spk 个人, 每人 per 个声纹片段 (同一人围着一个中心抖)。返回 X, 真实说话人。"""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(n_spk, dim))
    X = np.concatenate([c + spread * rng.normal(size=(per, dim)) * np.linalg.norm(c) / np.sqrt(dim) for c in centers])
    return X, np.repeat(np.arange(n_spk), per)


def test_forced_n_splits_are_merged_back():
    """真 3 个人, 被硬分成 9 簇 (每人拆 3 份) → 并回 3 个, 而且每个编号只对应一个真人。"""
    X, truth = voices(3, 300)
    rng = np.random.default_rng(1)
    forced = truth * 3 + rng.integers(0, 3, size=len(truth))
    out = refine_labels(forced, X)
    assert len(set(out.tolist())) == 3
    for k in set(out.tolist()):
        assert len(set(truth[out == k].tolist())) == 1


def test_distinct_people_are_not_merged():
    X, truth = voices(6, 200, seed=3)
    out = refine_labels(truth, X)
    assert len(set(out.tolist())) == 6


def test_tiny_cluster_goes_to_most_similar():
    X, truth = voices(2, 500, seed=5)
    labels = truth.copy()
    labels[:5] = 7          # 第一个人的 5 个片段被单分一簇 (0.5%)
    out = refine_labels(labels, X)
    assert len(set(out.tolist())) == 2
    assert len(set(out[:500].tolist())) == 1


def test_relabels_by_first_appearance_and_trivial_inputs():
    X, truth = voices(2, 50, seed=7)
    out = refine_labels(np.where(truth == 0, 5, 2), X)
    assert out[0] == 0 and set(out.tolist()) == {0, 1}
    assert refine_labels(np.zeros(10, dtype=int), X[:10]).tolist() == [0] * 10


def seg(start, end, text, spk):
    return {"spk": spk, "start": start, "end": end, "text": text}


def test_short_fragment_between_same_speaker_joins_him():
    segs = [seg(0, 3, "我们先看整改清单，", 1), seg(3.2, 3.5, "对。", 4), seg(3.8, 8, "第二项是材料一致性。", 1)]
    assert [s["spk"] for s in smooth_short(segs)] == [1, 1, 1]


def test_short_reply_between_different_speakers_kept():
    segs = [seg(0, 3, "这个能按时交吗？", 1), seg(3.2, 3.5, "可以。", 4), seg(3.8, 8, "那就这么定。", 2)]
    assert [s["spk"] for s in smooth_short(segs)] == [1, 4, 2]


def test_long_sentence_or_long_gap_kept():
    long_one = [seg(0, 3, "我们先看整改清单，", 1), seg(3.2, 6, "我觉得这个不对吧。", 4), seg(6.2, 8, "第二项。", 1)]
    assert smooth_short(long_one)[1]["spk"] == 4
    gap = [seg(0, 3, "我们先看整改清单，", 1), seg(8, 8.3, "对。", 4), seg(8.5, 10, "第二项。", 1)]
    assert smooth_short(gap)[1]["spk"] == 4


class FakeModel:
    def __init__(self, labels):
        self.labels = labels
        self.cb_model = lambda X, oracle_num=None: self.labels


def test_cluster_refine_wraps_and_restores():
    X, truth = voices(2, 100, seed=9)
    m = FakeModel(truth * 2 + (np.arange(len(truth)) % 2))      # 2 人拆 4 簇
    orig = m.cb_model
    with ClusterRefine(m) as r:
        out = m.cb_model(X, oracle_num=4)
    assert len(set(out.tolist())) == 2 and (r.before, r.after) == (4, 2)
    assert m.cb_model is orig


def test_cluster_refine_falls_back_on_error():
    m = FakeModel(np.array([0, 1, 1]))
    with ClusterRefine(m):
        out = m.cb_model("不是数组")       # 细化出错 → 原样返回, 转写不失败
    assert out.tolist() == [0, 1, 1]
    with ClusterRefine(None):              # 不分说话人
        pass


def test_exits_when_parent_goes_away(tmp_path):
    """父进程 (Companion) 没了, 转写进程 5 秒内自己退出, 不留孤儿。"""
    import subprocess
    import sys
    import time
    from pathlib import Path

    here = Path(__file__).parent
    child = tmp_path / "child.py"
    child.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(here)!r})\n"
        "import meeting_asr\n"
        "meeting_asr.exit_with_parent(interval=0.2)\n"
        "open(sys.argv[1], 'w').write('started')\n"
        "time.sleep(30)\n", encoding="utf-8")
    flag = tmp_path / "flag"
    # 中间夹一层 shell 当「Companion」: 它起了转写进程后自己先退
    parent = subprocess.Popen([sys.executable, "-c",
                               "import os, subprocess, sys, time; p = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]]); "
                               "print(p.pid, flush=True)\n"
                               "while not os.path.exists(sys.argv[2]): time.sleep(0.05)", str(child), str(flag)], stdout=subprocess.PIPE, text=True)
    pid = int(parent.stdout.readline())
    parent.wait()
    deadline = time.time() + 10
    import os
    alive = True
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            alive = False
            break
        time.sleep(0.2)
    assert not alive, "父进程退了, 转写进程还在"
