"""9/24: 「福富」事件 —— 新建条目的标题是已有条目的别名, 别名又跟已有条目撞了,
库里 24 处指向「中电福富」的关系同时变成"指向不明"。这里钉住两道闸。"""
from __future__ import annotations

from catfish_memory_wiki import _write_wiki_files

COMPANY = (
    "---\ntype: entity\ntitle: 中电福富信息科技有限公司\nentity_type: org\n"
    'aliases: ["中电福富", "福富"]\nontology_status: active\n---\n\n公司。\n'
)


def _home(tmp_path):
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "company.md").write_text(COMPANY, encoding="utf-8")
    return ents


def test_title_that_is_an_existing_alias_updates_that_entry(tmp_path):
    ents = _home(tmp_path)
    new = (
        "---\ntype: entity\ntitle: 福富\nentity_type: org\n"
        'aliases: ["中电福富"]\n---\n\n海南项目的中标单位。\n'
    )
    _write_wiki_files(tmp_path, {"wiki/entities/福富.md": new})
    assert not (ents / "福富.md").exists(), "不该新建第二份"
    assert "海南项目的中标单位" in (ents / "company.md").read_text(encoding="utf-8")


def test_alias_claimed_by_another_entry_is_dropped(tmp_path, caplog):
    ents = _home(tmp_path)
    new = (
        "---\ntype: entity\ntitle: 福富集团\nentity_type: org\n"
        'aliases: ["中电福富", "福富集团公司"]\n---\n\n另一个主体。\n'
    )
    _write_wiki_files(tmp_path, {"wiki/entities/fufu-group.md": new})
    text = (ents / "fufu-group.md").read_text(encoding="utf-8")
    assert 'aliases: ["福富集团公司"]' in text, text
    assert "已被" in caplog.text


def test_own_aliases_survive_rewrite_of_same_entry(tmp_path):
    ents = _home(tmp_path)
    _write_wiki_files(tmp_path, {"wiki/entities/company.md": COMPANY.replace("公司。", "公司, 补一句。")})
    text = (ents / "company.md").read_text(encoding="utf-8")
    assert "中电福富" in text and "\"福富\"" in text, text
