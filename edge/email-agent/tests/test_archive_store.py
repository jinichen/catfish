"""档案落地 —— 这一层是真源, 坏了没有第二份。

这些测试防的不是"功能对不对", 是**信会不会丢**。档案馆的承诺是"服务器
清理了本地还在", 兑现这个承诺的全部东西就是这几个函数。而它们的下游是
服务器端删除: verify 说 OK, 那封信在服务器上就可能被删, 之后本地这份是
世上唯一一份。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catfish_email import archive_store as A


# ─────────────────────────────────────────────────────────────
# 文件名 —— 输入是远端可控的邮件头
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("evil", [
    "<../../../../etc/passwd>",
    "../../secret",
    "a/b/c",
    "..",
    ".",
    "....//....//x",
    "\\windows\\system32",
])
def test_no_path_traversal_from_a_hostile_message_id(evil, tmp_path):
    """Message-ID 是发件人完全可控的字符串, 邮件头里塞什么都不犯法。

    消毒之后的名字必须落在档案目录**里面**, 不能跑出去。
    """
    name = A.safe_name(evil)
    assert "/" not in name and "\\" not in name, f"{evil!r} → {name!r} 还带路径分隔符"
    assert not name.startswith("."), f"{evil!r} → {name!r} 是隐藏文件或 .."

    root = tmp_path / "archive"
    rel = A.relpath_for(account="u@x.com", ident=evil, date_iso="2026-09-20")
    A.write_message(root, rel, b"x")
    written = (root / rel).resolve()
    assert root.resolve() in written.parents, f"写到档案目录外面去了: {written}"


def test_two_ids_that_sanitize_the_same_do_not_collide():
    """消毒是多对一的映射, 冲突必须靠哈希后缀挡住。

    `a/b` 和 `a-b` 白名单过滤之后都是 `a-b`。两封不同的信落到同一个文件名 =
    后到的**覆盖**先到的, 而且悄无声息 —— 索引里两行都显示"已归档", 磁盘上
    只有一封。等服务器那两封都被清理掉, 才发现少了一封。
    """
    assert A.safe_name("a/b") != A.safe_name("a-b")


def test_two_long_ids_sharing_a_prefix_do_not_collide():
    """超长 ID 截断之后前缀相同 —— 同样是覆盖。"""
    a = "x" * 400 + "@aaa.com"
    b = "x" * 400 + "@bbb.com"
    assert A.safe_name(a) != A.safe_name(b)


def test_name_stays_within_filesystem_limits():
    """文件系统的 255 是**字节**不是字符。中文一个字 3 字节。

    超了会 OSError, 而且是在归档跑了两小时之后才炸。
    """
    for raw in ["x" * 500, "测" * 500, "<" + "邮件编号" * 200 + ">"]:
        name = A.safe_name(raw)
        assert len(name.encode("utf-8")) <= 200, f"{name[:30]}… 太长"


def test_empty_or_all_junk_id_still_produces_a_usable_name():
    """全是非法字符时不能返回空串 —— 空文件名写不出文件, 那就是不归档。"""
    for raw in ["", "<>", "///", "...", "   "]:
        assert A.safe_name(raw), f"{raw!r} 消毒后成了空串"


# ─────────────────────────────────────────────────────────────
# 目录分布
# ─────────────────────────────────────────────────────────────


def test_grouped_by_month():
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso="2026-09-20T13:05:57+00:00")
    assert "/2026-09/" in rel


@pytest.mark.parametrize("bad_date", ["", "not-a-date", "20260920", "2026", None])
def test_a_broken_date_never_blocks_archiving(bad_date):
    """日期解析不出来就落 unknown/ —— **绝不能因此不归档**。

    日期错顶多是放错抽屉, 不归档是真丢信。这两个后果差着数量级。
    """
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso=bad_date or "")
    assert rel.endswith(".eml")
    assert "/unknown/" in rel


# ─────────────────────────────────────────────────────────────
# 原子写
# ─────────────────────────────────────────────────────────────


def test_write_then_verify_roundtrip(tmp_path):
    raw = b"From: a@b\r\nSubject: hi\r\n\r\nbody\r\n"
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso="2026-09-20")
    n, digest = A.write_message(tmp_path, rel, raw)
    assert n == len(raw)
    assert digest == hashlib.sha256(raw).hexdigest()
    assert A.verify_message(tmp_path, rel, expect_bytes=n, expect_sha256=digest) == ""


def test_failed_write_leaves_no_partial_file(tmp_path, monkeypatch):
    """写到一半炸了, 不能在档案目录里留下半封 .eml 或 .tmp 垃圾。

    半封 .eml 的后果不是"少一封": 调用方拿不到返回值就不会写 archived_at,
    所以索引上是"没归档"没错。但残留文件会被下一次 rglob 扫到当成邮件,
    解析失败, 每轮都报一次 —— 噪声掩盖真错误。
    """
    rel = A.relpath_for(account="u@x.com", ident="<boom@x>", date_iso="2026-09-20")

    real_replace = A.os.replace

    def blow_up(src, dst):
        raise OSError("磁盘满了")

    monkeypatch.setattr(A.os, "replace", blow_up)
    with pytest.raises(OSError):
        A.write_message(tmp_path, rel, b"half")
    monkeypatch.setattr(A.os, "replace", real_replace)

    leftovers = [p.name for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == [], f"留下了垃圾: {leftovers}"


def test_rewriting_the_same_id_replaces_atomically(tmp_path):
    """重新归档同一封 (比如上次校验没过) 要能覆盖, 不能因为文件已存在就炸。"""
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso="2026-09-20")
    A.write_message(tmp_path, rel, b"old")
    n, digest = A.write_message(tmp_path, rel, b"new-content")
    assert (tmp_path / rel).read_bytes() == b"new-content"
    assert A.verify_message(tmp_path, rel, expect_bytes=n, expect_sha256=digest) == ""


# ─────────────────────────────────────────────────────────────
# 校验 —— 服务器端删除的唯一依据
# ─────────────────────────────────────────────────────────────


def test_verify_actually_reads_the_disk_not_the_memory(tmp_path):
    """这是整个档案馆最要紧的一条。

    写完顺手拿内存里那份算哈希跟自己比 = 什么都没验。必须重读磁盘, 否则
    截断 / 坏块 / 被别的进程改了, 全部漏过去 —— 然后服务器上那封被删掉,
    本地这份是坏的, 而且没有任何地方报过错。

    这里直接在背后把文件改了, verify 必须发现。
    """
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso="2026-09-20")
    n, digest = A.write_message(tmp_path, rel, b"original content")

    (tmp_path / rel).write_bytes(b"tampered content")   # 同样长度!
    problem = A.verify_message(tmp_path, rel, expect_bytes=n, expect_sha256=digest)
    assert problem, "内容被换掉了却说校验通过 —— verify 没有真的读磁盘"
    assert "哈希" in problem


def test_verify_catches_truncation(tmp_path):
    """写一半断电的典型形态: 长度短了。"""
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso="2026-09-20")
    n, digest = A.write_message(tmp_path, rel, b"0123456789")
    (tmp_path / rel).write_bytes(b"01234")
    problem = A.verify_message(tmp_path, rel, expect_bytes=n, expect_sha256=digest)
    assert problem and "大小" in problem


def test_verify_catches_a_missing_file(tmp_path):
    """索引说已归档, 盘上没有 —— 备份恢复错了/被人清了/磁盘换了。"""
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso="2026-09-20")
    n, digest = A.write_message(tmp_path, rel, b"x")
    (tmp_path / rel).unlink()
    problem = A.verify_message(tmp_path, rel, expect_bytes=n, expect_sha256=digest)
    assert problem and "不在" in problem


def test_verify_problem_is_a_sentence_a_human_can_act_on(tmp_path):
    """返回字符串而不是 False, 是因为这句话要进界面和日志。

    调用方不知道是大小不对还是哈希不对, 让它现编那句话, 编出来的一定是
    "校验失败" 四个字 —— 那跟没说一样。降级必须说话, 说话就得有内容。
    """
    rel = A.relpath_for(account="u@x.com", ident="<1@x>", date_iso="2026-09-20")
    n, digest = A.write_message(tmp_path, rel, b"0123456789")
    (tmp_path / rel).write_bytes(b"01234")
    problem = A.verify_message(tmp_path, rel, expect_bytes=n, expect_sha256=digest)
    assert len(problem) > 15
    assert rel in problem, "得说清楚是哪一封"
    assert "5" in problem and "10" in problem, "得说清楚差在哪"


# ─────────────────────────────────────────────────────────────
# 起点 —— "从使用的第一天开始, 不回填历史"
# ─────────────────────────────────────────────────────────────


def test_cutoff_survives_a_wiped_index(tmp_path):
    """起点必须跟 .eml 放在一起, 不能只活在索引里。

    index_store 遇到 schema 不匹配会 DROP 重建, 那条设计的前提是"索引每一列
    都能从 .eml 重算"。起点不能 —— "我们什么时候开始管这个邮箱"这个事实,
    磁盘上任何一封邮件里都没有。

    只存索引里的话, 一次 schema bump 之后两条路都难看:
      当成"从今天开始" → 中间那段永远没人归档, 没有任何迹象
      当成"没设过"     → 整个邮箱重灌一遍, 用户莫名其妙等一夜
    """
    marks = {"INBOX": {"uidvalidity": "1", "uid": 8418}}
    A.write_cutoff(tmp_path, "u@x.com", marks)

    # 索引整个删掉都不影响 —— 起点在档案目录里
    assert A.read_cutoff(tmp_path, "u@x.com") == marks
    assert (tmp_path / A.safe_name("u@x.com") / ".archive-since.json").exists()


def test_no_cutoff_means_do_not_archive_not_archive_everything(tmp_path):
    """**方向性的一条。** 拿不准的时候宁可不归档。

    漏归档是"少存了", 用户看得出来也能补。误判成要归档是几小时下载 +
    几个 GB 磁盘, 发生在用户完全没预期的时候 —— 而且多半是在他刚装上
    软件、最没耐心的那一刻。
    """
    assert A.above_cutoff({}, "INBOX", "1", 99999) is False


def test_corrupt_cutoff_file_is_loud_and_safe(tmp_path, caplog):
    """起点文件坏了要出声, 并且退化成"还没设过"而不是"从 0 开始"。

    静默返回 {} 也是安全的 (不归档), 但那样用户永远不知道归档停了。
    降级必须说话。
    """
    path = tmp_path / A.safe_name("u@x.com") / ".archive-since.json"
    path.parent.mkdir(parents=True)
    path.write_text("{这不是 json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert A.read_cutoff(tmp_path, "u@x.com") == {}
    assert any("起点" in r.message for r in caplog.records), "坏了却一声不吭"


def test_uidvalidity_change_invalidates_the_watermark(tmp_path):
    """UID 从头发放了, 旧水位线跟新周期的 UID 没有可比性。

    返回 False = 不归档, 调用方该去重设起点。若在这里拿两个不相干的数
    比大小, 邮箱重建那天要么全灌要么全漏, 而那天往往正是容量满了在清理。
    """
    marks = {"INBOX": {"uidvalidity": "1", "uid": 8418}}
    assert A.above_cutoff(marks, "INBOX", "1", 8419) is True
    assert A.above_cutoff(marks, "INBOX", "2", 8419) is False
    assert A.above_cutoff(marks, "INBOX", "2", 1) is False


def test_watermark_is_exclusive(tmp_path):
    """水位线那一封**不归档** —— 它是启用之前就在的。"""
    marks = {"INBOX": {"uidvalidity": "1", "uid": 100}}
    assert A.above_cutoff(marks, "INBOX", "1", 100) is False
    assert A.above_cutoff(marks, "INBOX", "1", 101) is True


def test_unknown_folder_is_not_archived(tmp_path):
    """起点是按文件夹记的。没记过的文件夹同样按"不归档"处理。"""
    marks = {"INBOX": {"uidvalidity": "1", "uid": 100}}
    assert A.above_cutoff(marks, "Sent", "1", 999) is False


def test_cutoff_write_is_atomic(tmp_path, monkeypatch):
    """写一半的起点文件比没有更坏 —— 它会被 json 解析失败当成"没设过"。"""
    real = A.os.replace
    monkeypatch.setattr(A.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("满了")))
    with pytest.raises(OSError):
        A.write_cutoff(tmp_path, "u@x.com", {"INBOX": {"uidvalidity": "1", "uid": 1}})
    monkeypatch.setattr(A.os, "replace", real)
    leftovers = [p.name for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == [], f"留下了垃圾: {leftovers}"


def test_a_garbage_entry_does_not_poison_the_whole_cutoff(tmp_path):
    """一个文件夹的记录坏了, 别的文件夹照常归档。

    整份丢掉 = 所有文件夹都回到"没设过", 归档整体停摆。
    """
    path = tmp_path / A.safe_name("u@x.com") / ".archive-since.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"INBOX": {"uidvalidity": "1", "uid": 5}, "Sent": "坏了"}', encoding="utf-8"
    )
    marks = A.read_cutoff(tmp_path, "u@x.com")
    assert marks == {"INBOX": {"uidvalidity": "1", "uid": 5}}
