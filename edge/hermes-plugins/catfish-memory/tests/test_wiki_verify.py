"""10/23: 知识卡写盘前逐条对原文 (catfish_memory_wiki_verify)。"""
from __future__ import annotations

from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body
import catfish_memory_wiki_verify as v

SOURCE = (
    "闽政〔2026〕12号, 2026-07-30 印发。到2030年，工业战略性新兴产业占规上工业比重达到35%左右，"
    "研发经费投入年均增长10%。新材料产业集群规模突破9000亿元。"
)


def _card(body: str) -> str:
    return f"---\ntype: concept\ntitle: 规划\nconcept_type: 政策\n---\n\n# 规划\n\n{body}\n"


def _lists(content: str):
    fm, body = _split_frontmatter_body(content)
    return _parse_frontmatter_lists(fm), body


def test_摘录对得上_留下_摘录进evidence():
    out, st = v.verify_page(_card("- 2030 年战新产业占比约 35% 〔原文:「工业战略性新兴产业占规上工业比重达到35%左右」〕"), SOURCE)
    lists, body = _lists(out)
    assert st == {"kept": 1, "dropped": 0}
    assert "2030 年战新产业占比约 35%" in body and "〔原文" not in body
    assert lists["evidence"] == ["工业战略性新兴产业占规上工业比重达到35%左右"]


def test_摘录原文里没有_拿出正文进unverified():
    out, st = v.verify_page(_card("- 新材料破万亿 〔原文:「新材料产业集群规模突破1万亿元」〕"), SOURCE)
    lists, body = _lists(out)
    assert st["dropped"] == 1 and "新材料破万亿" not in body
    assert "摘录原文里找不到" in lists["unverified"][0]


def test_摘录对上了但数字被改了_也拿掉():
    # 7:3 写成 3:7 这类: 摘录是真的, 事实里的数字却不对
    out, st = v.verify_page(_card("- 研发经费年均增长 15% 〔原文:「研发经费投入年均增长10%」〕"), SOURCE)
    lists, _ = _lists(out)
    assert st["dropped"] == 1 and "15" in lists["unverified"][0]


def test_没给摘录的事实_不进正文():
    out, st = v.verify_page(_card("- 这份规划非常重要"), SOURCE)
    lists, body = _lists(out)
    assert st == {"kept": 0, "dropped": 1} and "非常重要" not in body
    assert "没给原文摘录" in lists["unverified"][0]


def test_全半角和标点差异不算对不上():
    out, st = v.verify_page(_card("- 文号 〔原文:「闽政 [2026] 12 号」〕"), SOURCE)
    assert st == {"kept": 1, "dropped": 0}


def test_太短的摘录不算():
    out, st = v.verify_page(_card("- 占比 〔原文:「35%」〕"), SOURCE)
    assert st["dropped"] == 1


def test_合并多出来的数字拿掉():
    old = _card("- 研发经费年均增长 10%")
    merged = _card("- 研发经费年均增长 10%\n- 新材料集群 9500 亿元")
    out = v.check_merged_numbers(merged, [old], SOURCE)
    lists, body = _lists(out)
    assert "9500" not in body and "9500" in lists["unverified"][0]
    assert "10%" in body


def test_合并丢了evidence_补回来():
    before = v.verify_page(_card("- 占比 35% 〔原文:「工业战略性新兴产业占规上工业比重达到35%左右」〕"), SOURCE)[0]
    merged = _card("- 占比 35%")  # LLM 合并输出没带 evidence
    out = v.carry_lists(merged, [before])
    assert _lists(out)[0]["evidence"]


def test_一条都没对上_标待确认(tmp_path):
    import catfish_memory_wiki as w
    content, _ = v.verify_page(_card("- 瞎编的 〔原文:「原文里根本没有这句话啊」〕"), SOURCE)
    status, reasons = w._classify_ontology_status(tmp_path, "wiki/concepts/规划.md", content)
    assert status == "pending" and "unverified_facts" in reasons


def test_摘录附近的日期_拆开写也认():
    out, st = v.verify_page(_card("- 2026-07-30 印发 〔原文:「闽政〔2026〕12号, 2026-07-30 印发」〕"), SOURCE)
    assert st == {"kept": 1, "dropped": 0}


def test_别处有这个数_但不在摘录附近_也不算():
    far = "这里提到12个地市。" + "无关内容" * 80 + "研发经费投入年均增长10%。"
    out, st = v.verify_page(_card("- 研发经费年均增长 12% 〔原文:「研发经费投入年均增长10%」〕"), far)
    assert st["dropped"] == 1


def test_省略号拼的两处_都在原文且离得近_算对上():
    out, st = v.verify_page(_card("- 2030 占比 35% 〔原文:「到2030年…比重达到35%左右」〕"), SOURCE)
    assert st == {"kept": 1, "dropped": 0}


def test_省略号拼的两处_有一处原文没有_不算():
    out, st = v.verify_page(_card("- 占比 35% 〔原文:「到2030年…比重提高到40%」〕"), SOURCE)
    assert st["dropped"] == 1


def test_模型的旁白_直接丢_不进待审():
    out, st = v.verify_page(_card("- 占比 〔原文:「工业战略性新兴产业占规上工业比重达到35%左右」〕\n(注：Facts 里没有别的摘录)\n修正思考：检查了一遍"), SOURCE)
    lists, body = _lists(out)
    assert st == {"kept": 1, "dropped": 0}
    assert "注" not in body and not lists.get("unverified")


def test_数字是标题的一部分_离摘录远也认():
    src = "党组书记、董事长柯瑞文在庆祝中国共产党成立 105 周年暨表彰大会上的讲话。" + "正文内容" * 100 + "2026 年 7 月 3 日印发"
    out, st = v.verify_page(_card("- 《庆祝中国共产党成立105周年暨表彰大会上的讲话》于2026年7月3日印发 〔原文:「2026 年 7 月 3 日印发」〕"), src)
    assert st == {"kept": 1, "dropped": 0}


def test_一个标记里两段摘录_分别核对():
    out, st = v.verify_page(_card("- 2030 占比 35%, 研发增长 10% 〔原文:「比重达到35%左右」「研发经费投入年均增长10%」〕"), SOURCE)
    assert st == {"kept": 1, "dropped": 0}
    assert len(_lists(out)[0]["evidence"]) == 2


def test_摘录比原文少了几个字_算对上_依据存原话():
    src = "2020年5月至7月，中电福富公司时任信息安全BG高级总监陈耿生为完成个人业绩任务。"
    out, st = v.verify_page(_card("- 时任信息安全BG高级总监 〔原文:「中电福富信息安全BG高级总监」〕"), src)
    assert st == {"kept": 1, "dropped": 0}
    assert _lists(out)[0]["evidence"] == ["中电福富公司时任信息安全BG高级总监"]


def test_摘录多了字或改了字_不算():
    src = "某省某市分公司后勤服务中心时任主任王某某，未经集体决策擅自。"
    out, st = v.verify_page(_card("- 时任副主任 〔原文:「某市分公司后勤服务中心时任副主任」〕"), src)
    assert st["dropped"] == 1


def test_跳过太多字_不算凑出来的():
    src = "中" + "无关" * 40 + "电福富信息安全"
    out, st = v.verify_page(_card("- x 〔原文:「中电福富信息安全」〕"), src)
    assert st["dropped"] == 1
