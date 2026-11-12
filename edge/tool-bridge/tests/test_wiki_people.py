"""知识库里的「人 → 邮箱」(wiki_people.py, 11/12)。人名 / 邮箱都是虚构的。"""
from __future__ import annotations

from pathlib import Path

from catfish_tool_bridge import wiki_people as wp


def _page(home: Path, name: str, text: str) -> None:
    d = home / "wiki" / "entities"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text, encoding="utf-8")


def test_directory_reads_person_fields_and_text_pairs(tmp_path):
    _page(tmp_path, "zhangsan", '---\ntype: entity\ntitle: 张三\nentity_type: person\naliases: [张总]\nemail: "Zhang@Ex.cn"\n---\n# 张三\n')
    _page(tmp_path, "wangwu", "---\ntype: entity\ntitle: 王五\nentity_type: person\n---\n# 王五\n同事 (邮箱账号 ww@ex.cn)\n")
    _page(tmp_path, "对照", "---\ntype: entity\ntitle: 人员与邮箱对照\nentity_type: doc\n---\n"
          "- 接口人: **迟小璐 cxl@ex.cn**, 电话 189\n"
          "- 谢小华(集成能力中心负责人;答辩分工=运维)= xxh@ex.cn;孟小辉(主管副总)= mxh@ex.cn\n"
          "- 黄小颖本机邮箱** hxy@ex.cn**;管小阳邮箱 gxy@ex.cn\n"
          "- 招标公司接口人=郭小祥(gxx@ex.cn)\n"
          "- 单位联系人李小倩 lxq@ex.cn\n"
          "| 陈小平 | cxp@ex.cn |\n"
          "- 发件地址 noreply@ex.cn; 通讯录 book@ex.cn; 申请书里的电子邮箱填的是 `x@ex.cn`\n"
          "- 只有前缀不收: ffchenrd=陈仁德\n")
    d = {e["name"]: e for e in wp.directory(tmp_path)}
    assert d["张三"]["email"] == "zhang@ex.cn" and d["张三"]["aliases"] == ["张总"]
    assert d["王五"]["email"] == "ww@ex.cn"
    for n, e in [("迟小璐", "cxl"), ("谢小华", "xxh"), ("孟小辉", "mxh"), ("黄小颖", "hxy"), ("管小阳", "gxy"),
                 ("郭小祥", "gxx"), ("李小倩", "lxq"), ("陈小平", "cxp")]:
        assert d[n]["email"] == f"{e}@ex.cn", n
        assert d[n]["sources"] == ["人员与邮箱对照"]
    assert not {"发件地址", "通讯录", "陈仁德", "电子邮箱"} & set(d)


def test_lookup_by_name_or_alias_and_two_emails(tmp_path):
    _page(tmp_path, "a", "---\ntitle: A\n---\n迟小璐 a@ex.cn\n")
    _page(tmp_path, "b", "---\ntitle: B\n---\n迟小璐 b@ex.cn\n")
    _page(tmp_path, "z", '---\ntitle: 张三\nentity_type: person\naliases: [张总]\nemail: z@ex.cn\n---\n')
    m = wp.lookup(["迟小璐", "张总", "没这人", " "], wp.directory(tmp_path))
    assert m == {"迟小璐": ["a@ex.cn", "b@ex.cn"], "张总": ["z@ex.cn"], "没这人": []}


def test_skips_history_and_shared_pages_are_read(tmp_path):
    _page(tmp_path, "x", "---\ntitle: X\n---\n")
    h = tmp_path / "wiki" / "entities" / ".history"
    h.mkdir()
    (h / "old.md").write_text("赵旧人 old@ex.cn\n", encoding="utf-8")
    s = tmp_path / "wiki-shared" / "dept" / "研发"
    s.mkdir(parents=True)
    (s / "p.md").write_text("---\ntitle: 研发通讯\n---\n钱同事 q@ex.cn\n", encoding="utf-8")
    names = {e["name"] for e in wp.directory(tmp_path)}
    assert "钱同事" in names and "赵旧人" not in names
