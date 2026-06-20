"""P3.5.43 — install_proposal 一键装 hermes-兼容 SKILL.

跑法:
  python3 -m pytest tests/test_install_proposal.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools, catfish_tools_propose
from catfish_tool_bridge.recmode import skill_sync


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """每个测独立 proposals jsonl + skills root + hermes root."""
    proposals = tmp_path / "skill_proposals.jsonl"
    monkeypatch.setattr(catfish_tools_propose, "SKILL_PROPOSALS_PATH", proposals)
    monkeypatch.setattr(catfish_tools, "SKILL_PROPOSALS_PATH", proposals)

    skills_root = tmp_path / "catfish-skills"
    hermes_root = tmp_path / "hermes-skills"
    monkeypatch.setattr(skill_sync, "HERMES_SKILLS_ROOT", hermes_root)
    return {
        "proposals": proposals,
        "skills_root": skills_root,
        "hermes_root": hermes_root,
    }


def _propose(name: str, **extras):
    """Helper 调 propose_skill 拿 proposal_id."""
    args = {
        "name": name,
        "reason": f"员工本周 3 次让我做这件事 {name}",
        "action_steps": "1. step a\n2. step b\n3. step c\n4. step d",
        "evidence_count": 3,
        "triggered_by": "user_request",
    }
    args.update(extras)
    out = catfish_tools.propose_skill(args)
    assert out["type"] == "ok", out
    return out["proposal_id"]


def test_install_proposal_creates_hermes_skill(isolated_env):
    """propose → install → ~/.hermes/skills/<name>/SKILL.md 真出来."""
    pid = _propose(
        "weekly-report",
        triggers=["周报", "本周工作", "本周总结"],
        kind="procedural",
        skill_namespace="department",
    )
    out = catfish_tools.dispatch_native("catfish_install_proposal", {
        "proposal_id": pid,
        "skills_root": str(isolated_env["skills_root"]),
    })
    assert out["ok"] is True
    # 落到 ~/.catfish/skills/department/weekly-report/
    skill_dir = isolated_env["skills_root"] / "department" / "weekly-report"
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "script.py").exists()
    # sync 到 ~/.hermes/skills/weekly-report/
    hermes_dir = isolated_env["hermes_root"] / "weekly-report"
    assert (hermes_dir / "SKILL.md").exists()
    assert (hermes_dir / "script.py").exists()


def test_install_proposal_generates_hermes_frontmatter(isolated_env):
    """生成的 SKILL.md 必须含 hermes-兼容 frontmatter."""
    pid = _propose(
        "test-skill",
        triggers=["t1", "t2", "t3"],
        kind="instructional",
        skill_namespace="personal",
    )
    catfish_tools.dispatch_native("catfish_install_proposal", {
        "proposal_id": pid,
        "skills_root": str(isolated_env["skills_root"]),
    })
    skill_md = (isolated_env["skills_root"] / "personal" / "test-skill" / "SKILL.md").read_text()
    assert skill_md.startswith("---\n")
    assert "name: test-skill" in skill_md
    assert "kind: instructional" in skill_md
    assert "namespace: personal" in skill_md
    assert "triggers:" in skill_md
    assert "  - t1" in skill_md


def test_install_proposal_generates_render_function(isolated_env):
    """script.py 必须含 def render_<slug>(params)."""
    pid = _propose("my-cool-skill", triggers=["a", "b", "c"])
    catfish_tools.dispatch_native("catfish_install_proposal", {
        "proposal_id": pid,
        "skills_root": str(isolated_env["skills_root"]),
    })
    script = (isolated_env["skills_root"] / "personal" / "my-cool-skill" / "script.py").read_text()
    assert "def render_my_cool_skill(params):" in script


def test_install_proposal_unknown_id_returns_error(isolated_env):
    out = catfish_tools.dispatch_native("catfish_install_proposal", {
        "proposal_id": "prop_doesnt_exist",
        "skills_root": str(isolated_env["skills_root"]),
    })
    assert out["ok"] is False
    assert "找不到" in out["error"]


def test_install_proposal_appends_installed_event(isolated_env):
    """audit trail: 装完后 jsonl 多一条 installed event."""
    pid = _propose("audit-test", triggers=["a", "b", "c"])
    catfish_tools.dispatch_native("catfish_install_proposal", {
        "proposal_id": pid,
        "skills_root": str(isolated_env["skills_root"]),
    })
    events = [json.loads(line) for line in isolated_env["proposals"].read_text().splitlines() if line.strip()]
    installed = [e for e in events if e.get("event_type") == "installed"]
    assert len(installed) == 1
    assert installed[0]["proposal_id"] == pid
    assert installed[0]["skill_name"] == "audit-test"


def test_install_proposal_collision_refused_for_handwritten(isolated_env):
    """hermes 已有同 slug 的手写 skill → sync 拒绝 (sync_error 报)."""
    # 模拟手写 skill 已经在 hermes 里
    handwritten = isolated_env["hermes_root"] / "collision-skill"
    handwritten.mkdir(parents=True)
    (handwritten / "SKILL.md").write_text(
        '---\nname: collision-skill\nauthor: 鸿波 (手写)\n---\n# hand\n',
        encoding="utf-8",
    )

    pid = _propose("collision-skill", triggers=["a", "b", "c"])
    out = catfish_tools.dispatch_native("catfish_install_proposal", {
        "proposal_id": pid,
        "skills_root": str(isolated_env["skills_root"]),
    })
    # 落档到 ~/.catfish/skills 仍成功, 但 sync 挂报 sync_error
    assert out["ok"] is True
    assert out["hermes_dir"] is None
    assert "sync_error" in out
    # 手写的 skill 没被覆盖
    assert (handwritten / "SKILL.md").read_text().startswith("---\nname: collision-skill\nauthor: 鸿波")


def test_install_proposal_no_sync_when_disabled(isolated_env):
    """sync_to_hermes=False 时只落 ~/.catfish/skills/, 不动 hermes."""
    pid = _propose("no-sync-test", triggers=["a", "b", "c"])
    out = catfish_tools.dispatch_native("catfish_install_proposal", {
        "proposal_id": pid,
        "skills_root": str(isolated_env["skills_root"]),
        "sync_to_hermes": False,
    })
    assert out["ok"] is True
    assert out["hermes_dir"] is None
    assert (isolated_env["skills_root"] / "personal" / "no-sync-test" / "SKILL.md").exists()
    assert not isolated_env["hermes_root"].exists() or not (
        isolated_env["hermes_root"] / "no-sync-test"
    ).exists()
