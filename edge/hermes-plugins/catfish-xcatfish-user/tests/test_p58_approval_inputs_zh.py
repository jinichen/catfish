"""11/06 hermes v0.23: 有审批卡着时, 微信回「1 / 2」要算批准 / 拒绝 (语言包词表里加上), 不然被当成「重定向当前运行」。"""
import plugin_locale_zh as p58


def test_一二加进审批词表_原有的词都还在():
    bundled = {"approval.inputs.approve": "approve,yes,批准,同意", "approval.inputs.deny": "deny,no,拒绝"}
    pack = p58.build_pack(bundled)
    assert pack["approval.inputs.approve"].split(",") == ["approve", "yes", "批准", "同意", "1"]
    assert pack["approval.inputs.deny"].split(",") == ["deny", "no", "拒绝", "2"]


def test_已经有了不重复加_上游删了的不凭空加():
    pack = p58.build_pack({"approval.inputs.approve": "yes,1"})
    assert "approval.inputs.approve" not in pack, "没变化的不覆盖"
    assert "approval.inputs.deny" not in pack
