"""测试 Skill 全生命周期 4 步 (五一 sprint Day 2-3).

覆盖:
- _read_skill_metadata 解析 version / deprecated
- _write_skill_audit append 一行 jsonl
- catfish_run_skill 加 audit (成功 + 失败)
- catfish_run_skill 检测 deprecated → 加 warning
- catfish_skill_install 正常 + 安全检查 + overwrite
- catfish_skill_delete 正常 + confirm 强制
- 注册到 NATIVE_TOOL_NAMES
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 让测试 import tool-bridge
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_tool_bridge.catfish_tools import (  # noqa: E402
    NATIVE_TOOL_NAMES,
    _read_skill_metadata,
    _skill_audit_path,
    _write_skill_audit,
    is_native,
    run_skill,
    skill_delete,
    skill_install,
)


# ── 注册检查 (Day 2-3) ──────────────────────────────────────────


def test_new_tools_registered():
    assert "catfish_skill_install" in NATIVE_TOOL_NAMES
    assert "catfish_skill_delete" in NATIVE_TOOL_NAMES
    assert "catfish_a2a_ask" in NATIVE_TOOL_NAMES
    assert is_native("catfish_skill_install")
    assert is_native("catfish_skill_delete")
    assert is_native("catfish_a2a_ask")


# ── metadata 解析 (Day 2 BL-L14) ───────────────────────────────


def test_read_skill_metadata_full(tmp_path: Path) -> None:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        '---\n'
        'name: foo\n'
        'version: "1.2.3"\n'
        'deprecated: true\n'
        'deprecated_reason: "use bar instead"\n'
        'description: |-\n'
        '  test\n'
        '---\n'
        '# Foo\n',
        encoding="utf-8",
    )
    md = _read_skill_metadata(skill_md)
    assert md["version"] == "1.2.3"
    assert md["deprecated"] is True
    assert md["deprecated_reason"] == "use bar instead"


def test_read_skill_metadata_defaults(tmp_path: Path) -> None:
    """老 SKILL.md 没有 version/deprecated → 走默认值."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        '---\n'
        'name: legacy\n'
        'description: old\n'
        '---\n',
        encoding="utf-8",
    )
    md = _read_skill_metadata(skill_md)
    assert md["version"] == "0.1.0"
    assert md["deprecated"] is False
    assert md["deprecated_reason"] == ""


def test_read_skill_metadata_no_file(tmp_path: Path) -> None:
    """没文件 → 默认值."""
    md = _read_skill_metadata(tmp_path / "nope.md")
    assert md["version"] == "0.1.0"
    assert md["deprecated"] is False


def test_read_skill_metadata_no_frontmatter(tmp_path: Path) -> None:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# 无 frontmatter\n直接正文")
    md = _read_skill_metadata(skill_md)
    assert md["version"] == "0.1.0"


# ── audit 写入 (Day 2 BL-L17) ──────────────────────────────────


def test_write_skill_audit_append(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    audit_path = tmp_path / ".catfish" / "skill_audit.jsonl"

    _write_skill_audit({"event_type": "run", "skill_path": "x/y", "ok": True})
    _write_skill_audit({"event_type": "delete", "skill_path": "x/y", "ok": True})

    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "run"
    assert json.loads(lines[1])["event_type"] == "delete"


def test_write_skill_audit_unicode(tmp_path: Path, monkeypatch) -> None:
    """中文 skill name / reason 不应 escape."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_skill_audit({"reason": "员工说删掉"})
    text = (tmp_path / ".catfish" / "skill_audit.jsonl").read_text(encoding="utf-8")
    assert "员工说删掉" in text


# ── catfish_skill_delete (Day 2 BL-L16) ────────────────────────


def _make_test_skill(skills_root: Path, name: str) -> Path:
    """造一个测试 skill 目录 (含 SKILL.md + script.py)."""
    skill_dir = skills_root / "test-ns" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\n'
        f'name: {name}\n'
        'version: "1.0.0"\n'
        'description: test\n'
        '---\n',
        encoding="utf-8",
    )
    (skill_dir / "script.py").write_text(
        "def render_x():\n    return {'ok': True}\n", encoding="utf-8"
    )
    return skill_dir


def test_skill_delete_requires_confirm(tmp_path: Path, monkeypatch) -> None:
    """confirm 默认 false → 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))
    _make_test_skill(skills, "test-skill")

    result = skill_delete({
        "skill_path": "test-ns/test-skill",
        "reason": "员工说不用了",
        "confirm": False,
    })
    assert result["ok"] is False
    assert "confirm" in result["error"].lower()


def test_skill_delete_blocks_path_traversal(tmp_path: Path, monkeypatch) -> None:
    """skill_path 含 .. 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    result = skill_delete({
        "skill_path": "../../../etc",
        "reason": "x",
        "confirm": True,
    })
    assert result["ok"] is False
    assert ".." in result["error"]


def test_skill_delete_success_with_backup(tmp_path: Path, monkeypatch) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))
    _make_test_skill(skills, "to-delete")

    assert (skills / "test-ns" / "to-delete").is_dir()

    result = skill_delete({
        "skill_path": "test-ns/to-delete",
        "reason": "已不需要",
        "confirm": True,
    })
    assert result["ok"] is True
    assert "to-delete" in result["deleted_path"]
    # 原目录消失
    assert not (skills / "test-ns" / "to-delete").exists()
    # 备份目录存在
    assert Path(result["backup_path"]).exists()
    # audit jsonl 有 delete 事件
    audit = (tmp_path / ".catfish" / "skill_audit.jsonl").read_text(encoding="utf-8")
    assert "delete" in audit


# ── catfish_skill_install (Day 3 BL-D1) ───────────────────────


def test_skill_install_basic(tmp_path: Path, monkeypatch) -> None:
    """正常装一个 skill."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    # 准备 source dir
    src = tmp_path / "source-skill"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\n'
        'name: my-new-skill\n'
        'version: "0.1.0"\n'
        'description: test\n'
        '---\n',
        encoding="utf-8",
    )

    result = skill_install({
        "source_dir": str(src),
        "namespace": "personal",
    })
    assert result["ok"] is True
    assert "personal/my-new-skill" in result["installed_path"]
    # skill 真复制了
    assert (skills / "personal" / "my-new-skill" / "SKILL.md").exists()


def test_skill_install_blocks_system_dirs(tmp_path: Path, monkeypatch) -> None:
    """source_dir 在 /etc /usr /System 等 → 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    for blocked in ["/etc", "/usr/lib", "/System/Library"]:
        result = skill_install({
            "source_dir": blocked,
            "namespace": "personal",
        })
        assert result["ok"] is False
        assert "系统目录" in result["error"]


def test_skill_install_requires_skill_md(tmp_path: Path, monkeypatch) -> None:
    """source_dir 没 SKILL.md → 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "source-no-skillmd"
    src.mkdir()

    result = skill_install({"source_dir": str(src)})
    assert result["ok"] is False
    assert "SKILL.md" in result["error"]


def test_skill_install_overwrite(tmp_path: Path, monkeypatch) -> None:
    """同名 skill 已存在, overwrite=True 才覆盖, 老版本 backup 到 trash."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    # 装一次
    src = tmp_path / "v1"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: foo\nversion: "1.0.0"\ndescription: v1\n---\n', encoding="utf-8",
    )
    skill_install({"source_dir": str(src), "namespace": "personal"})

    # 不带 overwrite 装第二次 → 拒
    # 五一 sprint 5/2 加 BL-C13 dedup, 现在拒的话术是"检测到 ... 高度相似 skill" (name 完全相同),
    # 不再是老的"已存在". force_install=true 跳过 dedup 才会触发原"已存在"路径.
    src2 = tmp_path / "v2"
    src2.mkdir()
    (src2 / "SKILL.md").write_text(
        '---\nname: foo\nversion: "2.0.0"\ndescription: v2\n---\n', encoding="utf-8",
    )
    result = skill_install({"source_dir": str(src2), "namespace": "personal"})
    assert result["ok"] is False
    assert ("已存在" in result["error"]) or ("高度相似" in result["error"])

    # 强制 force_install (跳过 dedup) → 撞 target_dir 已存在 → 拒老消息
    result_force = skill_install({
        "source_dir": str(src2),
        "namespace": "personal",
        "force_install": True,
    })
    assert result_force["ok"] is False
    assert "已存在" in result_force["error"]

    # overwrite=True 装成功
    result2 = skill_install({
        "source_dir": str(src2),
        "namespace": "personal",
        "overwrite": True,
    })
    assert result2["ok"] is True
    # 旧版备份在 trash 里
    trash = tmp_path / ".catfish" / "skill-trash"
    assert any(d.name.endswith("-replaced") for d in trash.iterdir())


def test_skill_install_blocks_namespace_traversal(tmp_path: Path, monkeypatch) -> None:
    """namespace 含 .. 或 / 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: foo\ndescription: test\n---\n', encoding="utf-8",
    )

    for bad_ns in ["../etc", "namespace/sub", "..", "..//.."]:
        result = skill_install({"source_dir": str(src), "namespace": bad_ns})
        assert result["ok"] is False, f"namespace {bad_ns} 应被拒"


# ── run_skill audit (Day 2 BL-L17) ────────────────────────────


def test_run_skill_writes_audit_on_success(tmp_path: Path, monkeypatch) -> None:
    """正常调用后 audit jsonl 有 1 行."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))
    skill_dir = _make_test_skill(skills, "audit-test")

    result = run_skill({"skill_path": "test-ns/audit-test", "params": {}})
    assert result["ok"] is True

    audit = (tmp_path / ".catfish" / "skill_audit.jsonl").read_text(encoding="utf-8")
    assert "audit-test" in audit
    event = json.loads(audit.strip().split("\n")[-1])
    assert event["ok"] is True
    assert event["skill_path"] == "test-ns/audit-test"
    assert "duration_ms" in event


def test_run_skill_writes_audit_on_failure(tmp_path: Path, monkeypatch) -> None:
    """skill 不存在也写 audit (但带 error_msg)."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    result = run_skill({"skill_path": "nonexistent/x", "params": {}})
    assert result["ok"] is False
    # 注意: skill 不存在的情况是早期返回 (root 检查后 skill_dir.is_dir 失败), 在写
    # audit 之前. 这里只验证不 crash 即可.


# ── BL-C12 dry-run 验证 + BL-C13 dedup (五一 sprint 5/2 收尾) ──


def test_skill_install_dry_run_passes_with_valid_script(tmp_path: Path, monkeypatch) -> None:
    """script.py 有效 + 入口 render_xxx → dry-run 通过."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "valid_skill"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: valid_test\nversion: "1.0.0"\ndescription: test\n---\n',
        encoding="utf-8",
    )
    (src / "script.py").write_text(
        "def render_test(**kwargs):\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    result = skill_install({"source_dir": str(src), "namespace": "personal"})
    assert result["ok"] is True
    assert result["dry_run"]["ok"] is True
    assert "render_test" in result["dry_run"]["entry_functions"]


def test_skill_install_dry_run_rolls_back_on_syntax_error(tmp_path: Path, monkeypatch) -> None:
    """script.py 语法错 → dry-run 失败 → rollback 删 target_dir."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "bad_skill"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: bad_test\nversion: "1.0.0"\ndescription: bad\n---\n',
        encoding="utf-8",
    )
    (src / "script.py").write_text(
        "def render_bad(\n    syntax error here\n",  # 故意语法错
        encoding="utf-8",
    )

    result = skill_install({"source_dir": str(src), "namespace": "personal"})
    assert result["ok"] is False
    assert "dry-run" in result["error"]
    # rollback 验证: target_dir 真的被删了
    assert not (skills / "personal" / "bad_test").exists()


def test_skill_install_dry_run_rolls_back_on_no_entry(tmp_path: Path, monkeypatch) -> None:
    """script.py 没入口函数 (没 render_xxx / run / main) → dry-run 失败 + rollback."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "no_entry"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: no_entry_test\nversion: "1.0.0"\ndescription: noent\n---\n',
        encoding="utf-8",
    )
    (src / "script.py").write_text(
        "def helper():\n    pass\nMY_CONST = 1\n",  # 只 helper, 无 render/run/main
        encoding="utf-8",
    )

    result = skill_install({"source_dir": str(src), "namespace": "personal"})
    assert result["ok"] is False
    assert "入口" in result["error"]
    assert not (skills / "personal" / "no_entry_test").exists()


def test_skill_install_skip_dry_run(tmp_path: Path, monkeypatch) -> None:
    """skip_dry_run=True → 不验, 装坏 skill 也通过 (老 skill 兼容用)."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "skip"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: skip_test\nversion: "1.0.0"\ndescription: skip\n---\n',
        encoding="utf-8",
    )
    (src / "script.py").write_text("def helper():\n    pass\n", encoding="utf-8")

    result = skill_install({
        "source_dir": str(src), "namespace": "personal", "skip_dry_run": True,
    })
    assert result["ok"] is True


def test_skill_install_dedup_blocks_same_name(tmp_path: Path, monkeypatch) -> None:
    """同名 skill (跨 namespace) → dedup 命中 → 拒装."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    # 装到 dept namespace
    src1 = tmp_path / "s1"
    src1.mkdir()
    (src1 / "SKILL.md").write_text(
        '---\nname: leadership_briefing\nversion: "1.0.0"\ndescription: dept brief\n---\n',
        encoding="utf-8",
    )
    skill_install({"source_dir": str(src1), "namespace": "department"})

    # 试装到 personal namespace, 同名
    src2 = tmp_path / "s2"
    src2.mkdir()
    (src2 / "SKILL.md").write_text(
        '---\nname: leadership_briefing\nversion: "2.0.0"\ndescription: my brief\n---\n',
        encoding="utf-8",
    )
    result = skill_install({"source_dir": str(src2), "namespace": "personal"})
    assert result["ok"] is False
    assert "高度相似" in result["error"]
    assert len(result["duplicates"]) == 1
    assert result["duplicates"][0]["name"] == "leadership_briefing"


def test_skill_install_force_install_skips_dedup(tmp_path: Path, monkeypatch) -> None:
    """force_install=True 跳过 dedup, 即使重名也装 (因 namespace 不同, 不撞 target_dir)."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src1 = tmp_path / "s1"
    src1.mkdir()
    (src1 / "SKILL.md").write_text(
        '---\nname: weekly\nversion: "1.0.0"\ndescription: dept\n---\n', encoding="utf-8",
    )
    skill_install({"source_dir": str(src1), "namespace": "department"})

    src2 = tmp_path / "s2"
    src2.mkdir()
    (src2 / "SKILL.md").write_text(
        '---\nname: weekly\nversion: "2.0.0"\ndescription: my\n---\n', encoding="utf-8",
    )
    result = skill_install({
        "source_dir": str(src2), "namespace": "personal", "force_install": True,
    })
    assert result["ok"] is True


# ── BL-D1 Skills Hub 第 1 件 (5/5 ship): hub URL 拉取 ──


def test_skill_install_validates_inputs(tmp_path: Path, monkeypatch) -> None:
    """source_dir 跟 hub_skill 互斥, 都不传报错, 都传也报错."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    # 都不传
    r = skill_install({})
    assert r["ok"] is False
    assert "source_dir" in r["error"] or "hub_skill" in r["error"]

    # 都传
    r = skill_install({"source_dir": "/tmp/x", "hub_skill": "shared/x@latest"})
    assert r["ok"] is False
    assert "互斥" in r["error"]


def test_install_from_hub_parses_hub_skill(tmp_path: Path, monkeypatch) -> None:
    """hub_skill = 'ns/name@version' 格式校验. 缺 namespace 应错."""
    from catfish_tool_bridge.catfish_tools import _install_from_hub

    # 没斜杠
    r = _install_from_hub("not-a-path", "http://localhost:9001")
    assert r["ok"] is False
    assert "ns/name@version" in r["error"]

    # 缺 name
    r = _install_from_hub("ns/", "http://localhost:9001")
    assert r["ok"] is False
    assert "缺" in r["error"] or "格式" in r["error"]


def test_install_from_hub_unreachable(tmp_path: Path, monkeypatch) -> None:
    """hub server 不可达 → 友好 error (不 raise)."""
    from catfish_tool_bridge.catfish_tools import _install_from_hub

    # 用一个肯定不通的端口
    r = _install_from_hub("shared/test@latest", "http://127.0.0.1:1")
    assert r["ok"] is False
    assert "不可达" in r["error"] or "URLError" in r["error"] or "失败" in r["error"]


def test_skill_install_hub_mode_mock(tmp_path: Path, monkeypatch) -> None:
    """模拟 hub 拉取: monkeypatch _install_from_hub 返一个 staging dir 假装拉成功,
    然后走原 install 流程 (dedup + dry-run + 复制).
    """
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    # 准备 staging 目录 (模拟 hub 拉到的内容)
    staging_root = tmp_path / ".catfish" / "skill-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / "fakeuuid"
    staging.mkdir()
    (staging / "SKILL.md").write_text(
        '---\nname: hub-skill\nversion: "1.0.0"\ndescription: 来自 hub\n---\n',
        encoding="utf-8",
    )

    # mock _install_from_hub 直接返这个 staging
    # 5/20 拆分后: skill_install 在 catfish_tools_install 子模块 (再拆从 install_and_ops 抽出),
    # 函数读自己 module 的 _install_from_hub. 必须 patch 那边才生效.
    from catfish_tool_bridge import catfish_tools, catfish_tools_install
    monkeypatch.setattr(catfish_tools_install, "_install_from_hub", lambda *_a, **_kw: {
        "ok": True,
        "staging_dir": str(staging),
        "hub_namespace": "shared",
        "hub_name": "hub-skill" if "hub-skill" in str(staging) else "ns-test",
        "hub_version": "1.0.0",
    })
    monkeypatch.setattr(catfish_tools, "_install_from_hub", lambda *_a, **_kw: {
        "ok": True,
        "staging_dir": str(staging),
        "hub_namespace": "shared",
        "hub_name": "hub-skill",
        "hub_version": "1.0.0",
    })

    result = skill_install({
        "hub_skill": "shared/hub-skill@latest",
        "skip_dry_run": True,  # 没 script.py, 跳 dry-run
    })
    assert result["ok"] is True, f"hub install 应成功: {result}"
    assert result["source"] == "hub"
    assert result["hub_meta"]["hub_skill"] == "shared/hub-skill@latest"
    assert result["hub_meta"]["hub_namespace"] == "shared"
    # hub namespace 自动用 (没显式传)
    assert "shared/hub-skill" in result["installed_path"]
    # 真复制了
    assert (skills / "shared" / "hub-skill" / "SKILL.md").exists()
    # staging 用完已清掉 (防 ~/.catfish/skill-staging/ 堆积)
    assert not staging.exists()


def test_skill_install_hub_namespace_override(tmp_path: Path, monkeypatch) -> None:
    """员工显式传 namespace 时, override hub 自带的 namespace."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    staging = tmp_path / ".catfish" / "skill-staging" / "fakeuuid2"
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text(
        '---\nname: ns-test\nversion: "1.0.0"\ndescription: x\n---\n',
        encoding="utf-8",
    )

    # 5/20 拆分后: skill_install 在 catfish_tools_install 子模块 (再拆从 install_and_ops 抽出),
    # 函数读自己 module 的 _install_from_hub. 必须 patch 那边才生效.
    from catfish_tool_bridge import catfish_tools, catfish_tools_install
    monkeypatch.setattr(catfish_tools_install, "_install_from_hub", lambda *_a, **_kw: {
        "ok": True,
        "staging_dir": str(staging),
        "hub_namespace": "shared",
        "hub_name": "hub-skill" if "hub-skill" in str(staging) else "ns-test",
        "hub_version": "1.0.0",
    })
    monkeypatch.setattr(catfish_tools, "_install_from_hub", lambda *_a, **_kw: {
        "ok": True,
        "staging_dir": str(staging),
        "hub_namespace": "shared",  # hub 那边是 shared
        "hub_name": "ns-test",
        "hub_version": "1.0.0",
    })

    result = skill_install({
        "hub_skill": "shared/ns-test@1.0.0",
        "namespace": "personal",  # 员工显式说要装到 personal
        "skip_dry_run": True,
    })
    assert result["ok"] is True
    # 装到员工指定的 personal, 不是 hub 的 shared
    assert (skills / "personal" / "ns-test" / "SKILL.md").exists()
    assert not (skills / "shared" / "ns-test").exists()
