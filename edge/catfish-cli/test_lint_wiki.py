from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parent / "scripts" / "lint_wiki.py"
SPEC = importlib.util.spec_from_file_location("lint_wiki", SCRIPT)
assert SPEC and SPEC.loader
LINT_WIKI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT_WIKI)


def test_parse_related_accepts_structured_and_legacy_entries():
    frontmatter = (
        'related: [{name: "中电福富信息科技有限公司", rel: "隶属"}, '
        '"[[CSMM-4]]", 历史记录]\n'
    )

    assert LINT_WIKI.parse_related(frontmatter) == [
        "中电福富信息科技有限公司",
        "CSMM-4",
        "历史记录",
    ]


def test_parse_related_deduplicates_targets():
    frontmatter = 'related: ["[[公司]]", {name: "公司", rel: "隶属"}]\n'

    assert LINT_WIKI.parse_related(frontmatter) == ["公司"]
