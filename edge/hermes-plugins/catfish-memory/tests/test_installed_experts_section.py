"""主小鲶的「员工装的专家」段 (10/18, docs/EXPERT-PACK-PLAN.md §3.3): 只列启用的; 专家 profile 里不出; 没有就空。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from catfish_memory_sections import _SectionsMixin  # noqa: E402


def _write(home: Path, eid: str, name: str, enabled: bool = True) -> None:
    d = home / "experts" / eid
    d.mkdir(parents=True)
    (d / "expert.yaml").write_text(f"id: {eid}\nname: {name}\ndescription: 管{name}的事\nenabled: {str(enabled).lower()}\n",
                                   encoding="utf-8")


def test_列出启用的专家和推荐用法(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    _write(tmp_path, "qual", "资质专员")
    _write(tmp_path, "old", "停用的专员", enabled=False)
    text = _SectionsMixin()._render_installed_experts(tmp_path)
    assert "资质专员 (id: qual)" in text and "停用的专员" not in text
    assert "catfish_expert(action=suggest" in text and "只推荐" in text


def test_专家自己的profile里不出(tmp_path, monkeypatch):
    _write(tmp_path, "qual", "资质专员")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes" / "profiles" / "expert-qual"))
    assert _SectionsMixin()._render_installed_experts(tmp_path) == ""


def test_没装专家就是空(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    assert _SectionsMixin()._render_installed_experts(tmp_path) == ""
