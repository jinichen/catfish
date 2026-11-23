"""11/16 对话卡片里的数据图表 (VegaChart, 上游官方扩展 catalogs/vegachart/v1): 目录跟上游一致、规则、出卡 / 改卡都查。"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge import a2ui_chart as ch
from catfish_tool_bridge.a2ui_catalog import CATALOG_PATH, DATA

BAR = {"mark": "bar", "data": {"values": [{"day": "周一", "n": 12}, {"day": "周二", "n": 30}]},
       "encoding": {"x": {"field": "day", "type": "nominal", "title": "日期"}, "y": {"field": "n", "type": "quantitative"}}}


def test_catalog_vegachart_matches_upstream():
    up = json.loads((DATA / "vegachart_catalog_v1.json").read_text(encoding="utf-8"))["components"]["VegaChart"]
    ours = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["components"]["VegaChart"]
    for prop in ("spec", "height"):
        assert ours["properties"][prop]["$ref"] == up["properties"][prop]["$ref"]
    assert set(up["properties"]) <= set(ours["properties"]) and ours["required"] == up["required"]


@pytest.mark.parametrize("spec,why", [
    ("bar", "Vega-Lite JSON 对象"),
    ({"$schema": "https://vega.github.io/schema/vega/v5.json", "marks": []}, "只支持 Vega-Lite"),
    ({"signals": [], "mark": "bar"}, "只支持 Vega-Lite"),
    ({**BAR, "data": {"url": "https://evil.example.com/x.json"}}, "网址"),
    ({**BAR, "encoding": {**BAR["encoding"], "href": {"field": "link"}}}, "网址"),
    ({**BAR, "mark": {"type": "image"}}, "image"),
    ({**BAR, "data": {"values": [{"n": i} for i in range(ch.MAX_ROWS + 1)]}}, "最多"),
    ({**BAR, "data": {"values": [{"n": "x" * 2000} for _ in range(40)]}}, "太大"),
])
def test_check_spec_rejects(spec, why):
    with pytest.raises(ValueError, match=why):
        ch.check_spec(spec)


def test_check_spec_allows_lite_with_schema_layers_and_params():
    ch.check_spec({**BAR, "$schema": "https://vega.github.io/schema/vega-lite/v6.json"})
    ch.check_spec({"layer": [BAR, {**BAR, "mark": "line"}], "params": [{"name": "p", "select": "point"}]})


def _card(spec, model=None):
    comps = [{"id": "root", "component": "Card", "child": "c"}, {"id": "c", "component": "VegaChart", "spec": spec}]
    msgs = [{"version": "1.0", "createSurface": {"surfaceId": "main", "catalogId": "x", "components": comps}}]
    for path, value in (model or {}).items():
        msgs.append({"version": "1.0", "updateDataModel": {"surfaceId": "main", "path": path, "value": value}})
    return msgs


def test_check_messages_literal_and_bound():
    assert ch.check_messages(_card(BAR)) == 1
    assert ch.check_messages(_card({"@path": "/chart"})) == 1                      # 还没推数据: 先画空的
    assert ch.check_messages(_card({"path": "/chart"}, {"/chart": BAR})) == 1
    with pytest.raises(ValueError, match="网址"):
        ch.check_messages(_card({"path": "/chart"}, {"/chart": BAR, "/chart/data/url": "https://x.example.com"}))
    assert ch.check_messages([{"createSurface": {"components": [{"id": "root", "component": "Text", "text": "x"}]}}]) == 0


# ── 走官方 SDK 的部分 (tool-bridge venv 没装 SDK 时跳过) ──

def test_compile_reply_rejects_url_chart_and_accepts_inline():
    pytest.importorskip("a2ui")
    from catfish_tool_bridge import a2ui_card as ac

    ok = ('<a2ui>\nroot = Card(Column([VegaChart(' + json.dumps(BAR, ensure_ascii=False) + '), Text("周二最多")]))\n</a2ui>')
    msgs = ac.compile_reply(ok)
    assert any(c.get("component") == "VegaChart" for c in msgs[0]["createSurface"]["components"])
    bad = ok.replace('"values": [{"day": "周一", "n": 12}, {"day": "周二", "n": 30}]', '"url": "https://evil.example.com/d.json"')
    with pytest.raises(ValueError, match="网址"):
        ac.compile_reply(bad)


def test_card_update_pushes_new_chart_data_and_rejects_url(tmp_path, monkeypatch):
    pytest.importorskip("a2ui")
    from catfish_tool_bridge import a2ui_card as ac

    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    card = '<a2ui>\nroot = Card(Column([VegaChart($/chart), Text("进度")]))\n$/chart = ' + json.dumps(BAR, ensure_ascii=False) + '\n</a2ui>'
    cid = ac.save_card(ac.compile_reply(card), "画一张本周邮件量")
    r = ac.update_card({"card_id": cid, "data": {"/chart/data/values": [{"day": "周一", "n": 12}, {"day": "周二", "n": 30},
                                                                          {"day": "周三", "n": 7}]}})
    assert r["ok"] and r["revision"] == 1
    r = ac.update_card({"card_id": cid, "data": {"/chart/data": {"url": "https://evil.example.com/d.json"}}})
    assert not r["ok"] and "网址" in r["error"]
    assert ac.load_card(cid)["revision"] == 1


def test_lift_chart_updates_turns_nested_path_into_whole_chart():
    base = _card({"@path": "/chart"}, {"/chart": BAR})
    upd = [{"version": "1.0", "updateDataModel": {"surfaceId": "main", "path": "/chart/data/values", "value": [{"day": "周三", "n": 7}]}},
           {"version": "1.0", "updateDataModel": {"surfaceId": "main", "path": "/chart/encoding/x/title", "value": "星期"}},
           {"version": "1.0", "updateDataModel": {"surfaceId": "main", "path": "/note", "value": "别的数据不动"}}]
    out = ch.lift_chart_updates(base, upd)
    assert [u["updateDataModel"]["path"] for u in out] == ["/chart", "/note"]
    whole = out[0]["updateDataModel"]["value"]
    assert whole["data"]["values"] == [{"day": "周三", "n": 7}] and whole["encoding"]["x"]["title"] == "星期" and whole["mark"] == "bar"
    assert ch.lift_chart_updates(_card(BAR), upd) == upd          # 没有绑定的图: 原样


def test_card_update_nested_chart_path_is_sent_as_whole_chart(tmp_path, monkeypatch):
    pytest.importorskip("a2ui")
    from catfish_tool_bridge import a2ui_card as ac

    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    card = '<a2ui>\nroot = Card(Column([VegaChart($/chart), Text("进度")]))\n$/chart = ' + json.dumps(BAR, ensure_ascii=False) + '\n</a2ui>'
    cid = ac.save_card(ac.compile_reply(card), "画一张本周邮件量")
    assert ac.update_card({"card_id": cid, "data": {"/chart/data/values": [{"day": "周三", "n": 7}]}})["ok"]
    last = ac.load_card(cid)["messages"][-1]["updateDataModel"]
    assert last["path"] == "/chart" and last["value"]["data"]["values"] == [{"day": "周三", "n": 7}]


def test_card_returns_data_paths_and_update_rejects_unknown_path(tmp_path, monkeypatch):
    """11/16 真模型评估: 主小鲶猜了个 /bars/2/value 去改图表, 原来照单全收、说「已更新」, 图其实没变。"""
    pytest.importorskip("a2ui")
    from catfish_tool_bridge import a2ui_card as ac

    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    card = ('<a2ui>\nroot = Card(Column([VegaChart($/chart), Text($/note)]))\n$/chart = ' + json.dumps(BAR, ensure_ascii=False)
            + '\n$/note = "周二最多"\n</a2ui>')
    msgs = ac.compile_reply(card)
    paths = ac.data_paths(msgs)
    assert "/chart/data/values = 图表数据 2 行 (字段 day, n)" in paths and '/note = "周二最多"' in paths
    cid = ac.save_card(msgs, "画一张本周邮件量")
    r = ac.update_card({"card_id": cid, "data": {"/bars/2/value": 60}})
    assert not r["ok"] and "没有数据路径 /bars/2/value" in r["error"] and "/chart/data/values" in r["error"]
    assert ac.load_card(cid).get("revision", 0) == 0
    assert ac.update_card({"card_id": cid, "data": {"/note": "周三最多", "/chart/data/values/2": {"day": "周三", "n": 40}}})["ok"]
    assert not ac.update_card({"card_id": cid, "data": {"/chart/data/values/9": {"day": "x", "n": 1}}})["ok"]   # 列表只能接在末尾


def test_card_update_reminds_to_update_summary(tmp_path, monkeypatch):
    """11/16 第二轮评估: 图更新了, 图下「周三至周五待补」那句没跟着改 —— 结论绑到 $/summary, 改图时提醒一起改。"""
    pytest.importorskip("a2ui")
    from catfish_tool_bridge import a2ui_card as ac

    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    card = ('<a2ui>\nroot = Card(Column([VegaChart($/chart), Text($/summary)]))\n$/chart = ' + json.dumps(BAR, ensure_ascii=False)
            + '\n$/summary = "周二最多"\n</a2ui>')
    msgs = ac.compile_reply(card)
    assert '/summary = "周二最多"' in ac.data_paths(msgs)
    cid = ac.save_card(msgs, "画一张本周邮件量")
    r = ac.update_card({"card_id": cid, "data": {"/chart/data/values": [{"day": "周三", "n": 40}]}})
    assert r["ok"] and "结论也要一起改" in r["note"]
    r = ac.update_card({"card_id": cid, "data": {"/chart/data/values": [{"day": "周三", "n": 41}], "/summary": "周三最多"}})
    assert r["ok"] and "结论" not in r["note"]


def test_fix_domains_drops_text_domain_on_numeric_field_in_bound_chart():
    """11/24 上周邮件分类饼图: theta = count (数值) 却给了类别名当 domain, 饼画不出来 —— 出卡时就地去掉; 类别字段的照留。"""
    from catfish_tool_bridge import a2ui_chart
    pie = {"data": {"values": [{"category": "本人自留", "count": 7}, {"category": "订阅资讯", "count": 6}]},
           "mark": {"type": "arc", "innerRadius": 60},
           "encoding": {"theta": {"field": "count", "type": "quantitative", "scale": {"domain": ["本人自留", "订阅资讯"]}},
                        "color": {"field": "category", "type": "nominal", "scale": {"domain": ["本人自留", "订阅资讯"]}},
                        "y": {"field": "count", "type": "quantitative", "scale": {"domain": [0, 10]}}}}
    msgs = [{"version": "1.0", "createSurface": {"surfaceId": "s", "catalogId": "x", "dataModel": {"chart1": pie},
             "components": [{"id": "root", "component": "VegaChart", "spec": {"@path": "/chart1"}}]}}]
    assert a2ui_chart.check_messages(msgs) == 1
    enc = msgs[0]["createSurface"]["dataModel"]["chart1"]["encoding"]
    assert "domain" not in enc["theta"]["scale"]
    assert enc["color"]["scale"]["domain"] == ["本人自留", "订阅资讯"] and enc["y"]["scale"]["domain"] == [0, 10]
