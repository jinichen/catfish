from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "migrate_wiki_integrity.py"
SPEC = importlib.util.spec_from_file_location("migrate_wiki_integrity", SCRIPT)
assert SPEC and SPEC.loader
MIGRATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATOR)


def _wiki(tmp_path: Path, name: str, text: str, kind: str = "entities") -> Path:
    path = tmp_path / "wiki" / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_marks_expired_without_touching_raw(tmp_path):
    path = _wiki(
        tmp_path,
        "cert.md",
        "---\ntype: entity\nupdated: 2026-07-01\n---\n有效期至：2026-07-02\n",
    )
    (tmp_path / "wiki" / "raw" / "sources").mkdir(parents=True)
    raw = tmp_path / "wiki" / "raw" / "sources" / "source.md"
    raw.write_text("原始材料", encoding="utf-8")

    assert MIGRATOR.migrate(tmp_path, apply=True, today=date(2026, 8, 28)) == 0
    assert "status: expired" in path.read_text(encoding="utf-8")
    assert raw.read_text(encoding="utf-8") == "原始材料"
    assert list((tmp_path / ".wiki-integrity-backups").rglob("cert.md"))


def test_rewrites_only_confirmed_link_and_state(tmp_path):
    path = _wiki(
        tmp_path,
        "flow.md",
        "---\ntype: concept\nupdated: 2026-07-11\n---\n"
        "[[CMMI-5级评审项目]]；“CMMI-5”的正式评估已定于2026年8月3日至10日进行。\n",
    )

    MIGRATOR.migrate(tmp_path, apply=True, today=date(2026, 8, 28))
    text = path.read_text(encoding="utf-8")
    assert "[[CMMI-5]]" in text
    assert "已于2026年8月3日至10日完成" in text


def test_rewrites_confirmed_formal_title_and_adds_sourced_department(tmp_path):
    path = _wiki(
        tmp_path,
        "flow.md",
        "---\ntype: concept\nupdated: 2026-07-11\n---\n"
        "[[通信工程施工总承包二级]]；[[项目负责人资质拆分规则]]；"
        "[[工作优先级排序原则]]；[[邮件安全分类规则]]；"
        "[[工作汇报确定性口吻原则]]；[[企业发展与风控部]]\n",
    )

    MIGRATOR.migrate(tmp_path, apply=True, today=date(2026, 8, 28))

    text = path.read_text(encoding="utf-8")
    assert "[[通信工程施工总承包(二级)]]" in text
    assert "[[项目负责人资质拆分原则]]" in text
    assert "[[工作优先级判断原则]]" in text
    assert "[[邮件安全分类原则]]" in text
    assert "工作汇报确定性口吻原则" not in text
    department = tmp_path / "wiki" / "entities" / "企业发展与风控部.md"
    assert department.exists()
    assert "1783325598-福富组织架构20260423" in department.read_text(encoding="utf-8")


def test_merges_duplicate_company_and_deletes_requested_orphans(tmp_path):
    canonical = _wiki(
        tmp_path,
        "zhong-dian-fu-fu-xin-xi-ke-ji-you-xian-gong-si.md",
        '---\ntype: entity\ntitle: 中电福富信息科技有限公司\nupdated: 2026-07-27\n'
        'aliases: ["中电福富", "福富"]\n'
        'sources: ["employee_journal"]\n---\n# 中电福富信息科技有限公司\n',
    )
    duplicate = _wiki(
        tmp_path,
        "福富.md",
        "---\ntype: entity\ntitle: 福富\n---\n# 福富\n统计记录\n",
    )
    orphan = _wiki(
        tmp_path,
        "gongzuo-youxianji-paixu-yuanze.md",
        "# 工作优先级排序原则\n",
        kind="concepts",
    )

    MIGRATOR.migrate(tmp_path, apply=True, today=date(2026, 8, 28))

    assert not duplicate.exists()
    assert not orphan.exists()
    merged = canonical.read_text(encoding="utf-8")
    assert "资质统计口径（2026-08-15）" in merged
    assert "journal:2026-08-15" in merged
    assert list((tmp_path / ".wiki-integrity-backups").rglob("福富.md"))
    assert list((tmp_path / ".wiki-integrity-backups").rglob("gongzuo-youxianji-paixu-yuanze.md"))


def test_narrows_scope_without_breaking_yaml_shape(tmp_path):
    scope = tmp_path / "search-scope.yaml"
    scope.write_text(
        "include:\n- ~/.catfish/outputs\n- ~/.catfish\n"
        "exclude:\n- '**/.git'\nmax_file_size_mb: 20\n",
        encoding="utf-8",
    )

    updated, reasons = MIGRATOR._narrow_search_scope(scope)

    assert "- ~/.catfish\n" not in updated
    assert "**/.wiki-integrity-backups" in updated
    assert "max_file_size_mb: 20" in updated
    assert reasons
