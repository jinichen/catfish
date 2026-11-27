"""11/27 按属性查并进 catfish_wiki_search (mode=query / browse) + 中文说法对到属性 key。"""
import pytest

from catfish_tool_bridge import wiki_query_terms as wq
from catfish_tool_bridge import wiki_search, wiki_types
from catfish_tool_bridge.catfish_tool_schemas import CATFISH_NATIVE_TOOLS


def _cert(title, **props):
    head = "\n".join(f'{k}: "{v}"' for k, v in props.items())
    return f"---\ntype: entity\ntitle: {title}\nentity_type: cert\nrelated: []\n{head}\n---\n\n# {title}\n"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    d = tmp_path / "wiki" / "entities"
    d.mkdir(parents=True)
    rows = [
        ("A证", dict(maintainer="陈鸿波", issuer="中华人民共和国工业和信息化部", valid_until="2027-03-01", level="甲级")),
        ("B证", dict(maintainer="陈鸿波", issuer="中华人民共和国工业和信息化部", valid_until="2029-01-01", level="一级")),
        ("C证", dict(maintainer="谢竞翔", issuer="盛唐认证南京有限责任公司", valid_until="2026-12-01", level="一级")),
    ]
    for title, props in rows:
        (d / f"{title}.md").write_text(_cert(title, **props), encoding="utf-8")
    return tmp_path


def test_同一类型里一个说法只对到一个属性_也不跟别的属性名撞():
    for key, tdef in wiki_types.load_schema().items():
        names: dict[str, str] = {}
        for p in tdef.get("properties") or []:
            for n in [p["key"], p.get("label")] + list(p.get("terms") or []):
                if not n:
                    continue
                assert names.get(n, p["key"]) == p["key"], f"{key}: 「{n}」同时对到 {names[n]} 和 {p['key']}"
                names[n] = p["key"]


def test_中文类型和属性对到key():
    schema = wiki_types.load_schema()
    real, resolved, by_term = wq.translate(
        {"type": "资质", "where": [{"field": "维护人", "eq": "x"}, {"field": "到期", "before": "2027-01-01"}], "sort": "到期"}, schema)
    assert real["type"] == "cert"
    assert [c["field"] for c in real["where"]] == ["maintainer", "valid_until"]
    assert real["sort"] == "valid_until"
    assert resolved == {"资质": "cert", "维护人": "maintainer", "到期": "valid_until"}
    assert by_term == ["valid_until"]
    assert wq.resolve_type("软著", schema) == "software"
    assert wq.resolve_type("资质证书", schema) == "cert"


def test_search_mode_query_按中文条件数(home):
    r = wiki_search.tool_wiki_search({"mode": "query", "type": "证书", "where": [{"field": "维护人", "eq": "陈鸿波"}]})
    assert r["ok"] and r["total"] == 2
    assert r["resolved"]["维护人"] == "maintainer"
    r = wiki_search.tool_wiki_search({"mode": "query", "type": "资质", "where": [{"field": "到期", "before": "2027-12-31"}]})
    assert sorted(i["title"] for i in r["items"]) == ["A证", "C证"]


def test_等于查不到时给相近的真实取值(home):
    r = wiki_search.tool_wiki_search({"mode": "query", "type": "资质", "where": [{"field": "发证单位", "eq": "工业和信息化部"}]})
    assert r["total"] == 0
    assert r["near_values"] == {"issuer": ["中华人民共和国工业和信息化部"]}


def test_按说法对到的属性一条都没填时提示(home):
    r = wiki_search.tool_wiki_search({"mode": "query", "type": "资质", "where": [{"field": "年审", "before": "2030-01-01"}]})
    assert r["total"] == 0 and r["unfilled"] == {"review_on": {"label": "复审日期", "count": 3}}
    assert "3 条没填复审日期" in r["note"] and "告诉员工" in r["note"]


def test_查到了也写明有几条没填_没算进去(home):
    d = home / "wiki" / "entities"
    (d / "D证.md").write_text(_cert("D证", maintainer="陈鸿波"), encoding="utf-8")
    r = wiki_search.tool_wiki_search({"mode": "query", "type": "资质", "where": [{"field": "到期", "before": "2027-12-31"}],
                                      "sort": "到期"})
    assert r["total"] == 2
    assert r["unfilled"] == {"valid_until": {"label": "有效期至", "count": 1}}
    assert "共 4 条资质证书, 其中 1 条没填有效期至" in r["note"]
    # 条件里的属性都填全了: 不多话
    r = wiki_search.tool_wiki_search({"mode": "query", "type": "资质", "where": [{"field": "维护人", "eq": "陈鸿波"}]})
    assert r["total"] == 3 and "unfilled" not in r
    # 找没填的 (empty) 本身不算缺口
    r = wiki_search.tool_wiki_search({"mode": "query", "type": "资质", "where": [{"field": "到期", "empty": True}]})
    assert r["total"] == 1 and "unfilled" not in r


def test_browse_列类型和常见取值(home):
    r = wiki_search.tool_wiki_search({"mode": "browse"})
    assert r["types"]["cert"]["count"] == 3
    r = wiki_search.tool_wiki_search({"mode": "browse", "type": "资质"})
    assert r["type"] == "cert"
    assert r["properties"]["maintainer"]["filled"] == 3
    assert r["properties"]["maintainer"]["top"][0] == "陈鸿波"
    assert r["properties"]["valid_until"]["type"] == "date" and "top" not in r["properties"]["valid_until"]
    assert "到期" in r["properties"]["valid_until"]["terms"]


def test_原来的search和wiki_query照旧(home):
    assert "matches" in wiki_search.tool_wiki_search({"query": "A证"})
    r = wiki_types.tool_dispatch("catfish_wiki_query", {"type": "cert", "where": [{"field": "维护人", "eq": "谢竞翔"}]})
    assert r["total"] == 1


def test_核心里的wiki_search带上了query和browse():
    t = next(t for t in CATFISH_NATIVE_TOOLS if t["name"] == "catfish_wiki_search")
    assert t["input_schema"]["properties"]["mode"]["enum"] == ["search", "query", "browse"]
    assert {"type", "where", "sort", "fields", "limit"} <= set(t["input_schema"]["properties"])
