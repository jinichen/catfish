"""11/06 常驻记忆写入硬上限: 单条字数 / 快满了不许再新加 (memory_budget.py)。"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("mb", Path(__file__).parents[1] / "memory_budget.py")
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)


def test_单条超长拦下_改短的放行():
    long = "口" * 300
    msg = mb.check("memory", "add", long, entries=[], limit=4000)
    assert msg and "300 字" in msg and "200 字" in msg and "知识库" in msg
    assert mb.check("memory", "add", "口" * 150, entries=[], limit=4000) is None
    assert mb.check("user", "add", "口" * 160, entries=[], limit=2000), "USER.md 单条 150"
    # 把 577 字那条改成 300 字: 还是超, 但在变短 —— 放行
    old = "细节已移入 skill/wiki" + "口" * 560
    assert mb.check("memory", "replace", "口" * 300, old_text="细节已移入", entries=[old], limit=4000) is None
    # 改得更长 → 拦
    assert mb.check("memory", "replace", "口" * 600, old_text="细节已移入", entries=[old], limit=4000)


def test_快满了不许新加_但可以合并():
    entries = ["口" * 180] * 19      # ≈ 3473 字, 87%
    msg = mb.check("memory", "add", "新规矩一条", entries=entries, limit=4000)
    assert msg and "快满了" in msg and "replace" in msg
    assert mb.check("memory", "add", "新规矩一条", entries=["口" * 100] * 10, limit=4000) is None
    # 快满时把新规矩并进已有一条 (变长一点): 不超总上限就放行
    assert mb.check("memory", "replace", "口" * 195, old_text="口口", entries=entries, limit=4000) is None


def test_别的动作和目标不管():
    assert mb.check("memory", "remove", "口" * 999, entries=[], limit=4000) is None
    assert mb.check("skills", "add", "口" * 999, entries=[], limit=4000) is None
