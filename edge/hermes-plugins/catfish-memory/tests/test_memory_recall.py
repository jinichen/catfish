"""P1 (10/22): 长期记忆按问题检索 (catfish_memory_recall)。"""
from __future__ import annotations

import os
from pathlib import Path

import catfish_memory_recall as r
from catfish_memory_distill_reconcile import assemble

SEG_OLD = (
    "## 人物\n- 林启铵：副总经理，采购方案终审\n\n"
    "## 偏好\n- 周报沿用上期格式\n\n"
    "## 任务状态\n- CMMI-5级评审 | paused\n"
)
SEG_NEW = (
    "## 项目\n- ISO50001认证：需增补斗门2025逐月水电数据\n\n"
    "## 偏好\n- 周报沿用上期格式\n\n"
    "## 任务状态\n- CMMI-5证书已出 | resolved\n"
)
STATUS = "## 进行中\n- ISO50001认证：增补数据（2026-10-21）\n\n## 已完结（不再进待办）\n- CMMI-5：拿证（2026-08-11）"


def _write(tmp_path: Path) -> tuple[Path, str]:
    text = assemble([("2026-06-29 ~ 2026-06-30", SEG_OLD), ("2026-10-19 ~ 2026-10-21", SEG_NEW)], STATUS)
    p = tmp_path / "distilled_facts.md"
    p.write_text(text, encoding="utf-8")
    return p, text


def test_当前状态段整段取出_不含其它段(tmp_path):
    _, text = _write(tmp_path)
    block = r.current_status_block(text)
    assert block.startswith("### 蒸馏段 0") and "ISO50001认证：增补数据" in block
    assert "林启铵" not in block, "只要段 0, 别把后面的段带进来"
    assert r.current_status_block("老格式, 没有段 0") == ""


def test_按问题捞出两个月前的人物(tmp_path):
    p, text = _write(tmp_path)
    out = r.recall_block(p, text, "林启铵是谁", 1800)
    assert "林启铵：副总经理" in out and "[2026-06-29 ~ 2026-06-30 · 人物]" in out
    assert "ISO50001" not in out, "不相关的不该进来"


def test_同一件事新段排前(tmp_path):
    p, text = _write(tmp_path)
    out = r.recall_block(p, text, "CMMI-5", 1800)
    assert out.index("CMMI-5证书已出") < out.index("CMMI-5级评审"), out


def test_重复的一句只留最新那段(tmp_path):
    p, text = _write(tmp_path)
    out = r.recall_block(p, text, "周报格式", 1800)
    assert out.count("周报沿用上期格式") == 1
    assert "[2026-10-19 ~ 2026-10-21 · 偏好] 周报沿用上期格式" in out


def test_只有问句功能词时不硬凑(tmp_path):
    """★ 10/22 实测: 「今天先做什么」靠 什么/先/做 撞出一串无关条目 —— 现在应该什么都不给。"""
    p, text = _write(tmp_path)
    assert r.recall_block(p, text, "今天先做什么", 1800) == ""
    assert r.recall_block(p, text, "", 1800) == ""


def test_预算卡住条数(tmp_path):
    p, text = _write(tmp_path)
    out = r.recall_block(p, text, "CMMI-5", 60)
    assert out.count("\n- ") <= 1


def test_文件变了索引跟着重建(tmp_path):
    p, text = _write(tmp_path)
    assert "张三" not in r.recall_block(p, text, "张三", 1800)
    new = text + "\n\n### 蒸馏段 3（2026-10-22）\n\n## 人物\n- 张三：新来的采购经理\n"
    p.write_text(new, encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert "张三：新来的采购经理" in r.recall_block(p, new, "张三", 1800)


def test_没有jieba时退回双字(monkeypatch):
    monkeypatch.setattr(r, "_jieba", None)
    monkeypatch.setattr(r, "_jieba_tried", True)
    toks = r.tokenize("ISO50001 能源数据")
    assert "iso50001" in toks and "能源" in toks and "数据" in toks
