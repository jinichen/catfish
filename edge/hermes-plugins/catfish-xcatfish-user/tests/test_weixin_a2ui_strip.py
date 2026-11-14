"""11/15 A2UI 卡片试点: 微信显示不了卡片, 外发前 ```a2ui 代码块换成一句话 (plugin_weixin_zh._strip_a2ui_cards)。"""
from __future__ import annotations

from plugin_weixin_zh import A2UI_WEIXIN_NOTE, _strip_a2ui_cards

CARD = '出一张采集卡：\n\n```a2ui\n[{"version":"1.0","createSurface":{"surfaceId":"jjh","catalogId":"catfish:cards/v1"}}]\n```\n\n填好点按钮就行。'


def test_card_block_replaced_with_note():
    out = _strip_a2ui_cards(CARD)
    assert "createSurface" not in out and "```" not in out
    assert A2UI_WEIXIN_NOTE in out
    assert out.startswith("出一张采集卡") and out.endswith("填好点按钮就行。")


def test_unclosed_block_also_replaced():
    out = _strip_a2ui_cards('看这张：\n```a2ui\n[{"version":"1.0"')
    assert "version" not in out and A2UI_WEIXIN_NOTE in out


def test_other_code_blocks_and_plain_text_untouched():
    text = "示例：\n```python\nprint(1)\n```\n普通文字"
    assert _strip_a2ui_cards(text) == text
    assert _strip_a2ui_cards("没有卡片") == "没有卡片"
    assert _strip_a2ui_cards(None) is None
