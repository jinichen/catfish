"""9/24「CMMI-5 拿证后又被当成进行中」: 分段蒸馏的旧段不知道后来的结局,
而消费方 (早安画像/上下文) 只读文件开头 —— 开头恰好是最旧的一段。"""
from __future__ import annotations

from catfish_memory_distill_reconcile import (
    assemble, chunk_date_range, fallback_current_status, status_digest,
)

OLD = "## 项目\n- CMMI-5 级评审：准备中\n\n## 任务状态\n- CMMI-5 级评审准备 | paused\n"
NEW = "## 项目\n- CMMI-5：已拿证 (编号83824)\n\n## 任务状态\n- CMMI-5 级评审 | resolved\n- 三期采购 | paused\n"
SEGS = [("2026-06-05 ~ 2026-06-12", OLD), ("2026-08-11", NEW)]


def test_chunk_date_range():
    assert chunk_date_range("## [2026-07-01 09:00] x\n## [2026-07-03] y") == "2026-07-01 ~ 2026-07-03"
    assert chunk_date_range("no dates") == ""


def test_current_status_first_and_newest_segment_before_oldest():
    out = assemble(SEGS, "## 进行中\n(无)\n\n## 已完结（不再进待办）\n- CMMI-5：已拿证（2026-08-11）")
    assert out.startswith("### 蒸馏段 0 · 当前状态")
    # 只读开头 3000 字节的消费方先看到当前状态, 其次是最新段
    assert out.index("已拿证") < out.index("准备中")
    assert out.index("### 蒸馏段 2（2026-08-11）") < out.index("### 蒸馏段 1（2026-06-05 ~ 2026-06-12）")


def test_fallback_newest_wins():
    text = fallback_current_status(SEGS)
    done = text.split("## 暂停/搁置")[0]
    assert "CMMI-5 级评审" in done, text
    assert "CMMI-5 级评审准备" not in text.split("## 暂停/搁置")[1], "旧段的 paused 不能盖过新段的 resolved"
    assert "三期采购" in text.split("## 暂停/搁置")[1]


def test_digest_newest_first_and_only_status_sections():
    d = status_digest([("2026-06-05", "## 人物\n- 张三\n\n" + OLD), ("2026-08-11", NEW)])
    assert d.index("2026-08-11") < d.index("2026-06-05")
    assert "张三" not in d
