"""10/15: 员工本机 skill (~/.catfish/skills/) 也要能被 run / 删 / 查重 / 装。

原来这几条只认仓库 skills/ (或 CATFISH_SKILLS_DIR): 员工教会 / catfish_install_proposal 装的
skill 落在 ~/.catfish/skills/<ns>/, catfish_run_skill 永远报「skill 不存在」;
客户机装包里没有仓库 skills/, 连装都装不了。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge.catfish_tools import dispatch_native
from catfish_tool_bridge.catfish_tools_install import _list_existing_skills

SCRIPT = '''
def render_hello(name: str = "世界") -> dict:
    return {"ok": True, "summary": f"你好 {name} from {__file__}", "files": []}
'''


def _skill(root: Path, path: str, tag: str) -> Path:
    d = root / path
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {tag}\nversion: \"1.0.0\"\n---\n", encoding="utf-8")
    (d / "script.py").write_text(SCRIPT, encoding="utf-8")
    return d


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo-skills"
    (repo / "department").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(repo))
    return repo, home / ".catfish" / "skills"


def test_run_skill_finds_personal_skill_in_local_root(roots):
    repo, local = roots
    _skill(local, "personal/video-audio-summary", "本机")
    r = dispatch_native("catfish_run_skill", {"skill_path": "personal/video-audio-summary", "params": {"name": "鲶鱼"}})
    assert r["ok"], r
    assert "你好 鲶鱼" in r["summary"] and str(local) in r["summary"]


def test_repo_skill_wins_over_local_copy_with_same_path(roots):
    repo, local = roots
    _skill(repo, "department/weekly-report", "仓库")
    _skill(local, "department/weekly-report", "本机")
    r = dispatch_native("catfish_run_skill", {"skill_path": "department/weekly-report", "params": {}})
    assert r["ok"] and str(repo) in r["summary"]  # 工程审定的优先
    assert [x["path"] for x in _list_existing_skills()].count("department/weekly-report") == 1


def test_missing_skill_error_lists_where_it_looked(roots):
    repo, local = roots
    local.mkdir(parents=True)
    r = dispatch_native("catfish_run_skill", {"skill_path": "personal/nope", "params": {}})
    assert not r["ok"] and str(repo) in r["error"] and str(local) in r["error"]


def test_dedup_and_delete_see_local_skills(roots):
    repo, local = roots
    _skill(local, "personal/video-audio-summary", "本机")
    assert "personal/video-audio-summary" in [x["path"] for x in _list_existing_skills()]
    r = dispatch_native("catfish_skill_delete", {"skill_path": "personal/video-audio-summary", "confirm": True, "reason": "测试"})
    assert r["ok"], r
    assert not (local / "personal" / "video-audio-summary").exists()


def test_install_root_falls_back_to_local_without_repo(monkeypatch, tmp_path):
    """客户机: 装包里没有仓库 skills/, 也没设 CATFISH_SKILLS_DIR → 装到 ~/.catfish/skills/。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CATFISH_SKILLS_DIR", raising=False)
    monkeypatch.setattr(ops, "_catfish_skills_root", lambda: None)
    assert ops._skill_install_root() == tmp_path / ".catfish" / "skills"
    _skill(tmp_path / ".catfish" / "skills", "department/eis-login", "hub 装的")
    assert ops._find_skill_dir("department/eis-login") is not None
