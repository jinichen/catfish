"""skill_guard 测试 — gateway 工程级强制保护.

验证:
  - 意图检测准确 (汇报/请示/立项 等命中, 闲聊不命中)
  - tools 列表里有 catfish_run_skill → 加 REQUIRED block (强制调 skill)
  - 缺 catfish_run_skill → 加 MISSING block (警告员工 Cmd+R)
  - 没意图 → 不动 messages
  - 幂等
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.skill_guard import (  # noqa: E402
    has_skill_intent,
    has_skill_tool_in_request,
    inject_skill_guard,
)


# ── 意图检测 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "user_text",
    [
        "写一份给公司领导汇报材料",
        "帮我写工作汇报",
        "写一份请示件",
        "起草一份立项报告",
        "写一份呈批件",
        "给王总写一份汇报",
        "给集团汇报",
        "上报材料一份",
        "决策事项报告",
    ],
)
def test_intent_hits(user_text):
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": user_text},
    ]
    assert has_skill_intent(msgs) is True


@pytest.mark.parametrize(
    "user_text",
    [
        "你好",
        "今天天气怎么样",
        "帮我写个 Python 函数",
        "解释一下什么是 OAuth",
        "写一段代码",  # 不含汇报/请示等
    ],
)
def test_intent_misses(user_text):
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": user_text},
    ]
    assert has_skill_intent(msgs) is False


def test_intent_only_checks_last_user_message():
    """历史里有触发词不算, 只看最后一条 user."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "之前我让你写过汇报"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "现在帮我查个天气"},
    ]
    assert has_skill_intent(msgs) is False


def test_intent_handles_multimodal_content():
    """user content 是 list (multimodal) 时也能扫."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [
            {"type": "text", "text": "写一份请示件"},
            {"type": "image_url", "image_url": {"url": "..."}},
        ]},
    ]
    assert has_skill_intent(msgs) is True


# ── tools 检测 ──────────────────────────────────────────────────


def test_tool_present_openai_format():
    body = {"tools": [
        {"type": "function", "function": {"name": "catfish_run_skill"}},
        {"type": "function", "function": {"name": "execute_code"}},
    ]}
    assert has_skill_tool_in_request(body) is True


def test_tool_present_flat_format():
    """兼容某些客户端用平铺格式 {name: ...}."""
    body = {"tools": [{"name": "catfish_run_skill"}]}
    assert has_skill_tool_in_request(body) is True


def test_tool_missing():
    body = {"tools": [
        {"type": "function", "function": {"name": "execute_code"}},
        {"type": "function", "function": {"name": "terminal"}},
    ]}
    assert has_skill_tool_in_request(body) is False


def test_tool_no_tools_field():
    """body 没 tools 字段 → 视为缺."""
    assert has_skill_tool_in_request({}) is False
    assert has_skill_tool_in_request({"tools": None}) is False
    assert has_skill_tool_in_request({"tools": []}) is False


# ── inject 行为 ────────────────────────────────────────────────


def test_inject_required_when_intent_and_tool_present():
    """意图触发 + 工具就位 → REQUIRED block."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写一份请示件"},
    ]
    body = {
        "tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]
    }
    out = inject_skill_guard(msgs, body)
    sys_content = out[0]["content"]
    assert "必须" in sys_content
    assert "catfish_run_skill" in sys_content
    assert "严禁" in sys_content
    # 关键: 严禁 execute_code 字样
    assert "execute_code" in sys_content


def test_inject_missing_when_intent_but_no_tool():
    """意图触发 + 工具缺失 → MISSING block + Cmd+R 引导."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写一份请示件"},
    ]
    body = {"tools": []}  # 没 catfish_run_skill
    out = inject_skill_guard(msgs, body)
    sys_content = out[0]["content"]
    assert "未加载" in sys_content  # BL-SKILL-METADATA-DYNAMIC: 措辞改"未加载"更准
    assert "Cmd" in sys_content  # 引导用户 Cmd+R
    assert "刷新" in sys_content


def test_inject_no_intent_does_nothing():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "你好"},
    ]
    body = {"tools": []}
    out = inject_skill_guard(msgs, body)
    assert out == msgs


def test_inject_no_system_message_does_nothing():
    msgs = [{"role": "user", "content": "写汇报"}]
    out = inject_skill_guard(msgs, {})
    assert out == msgs


def test_inject_idempotent():
    """同样内容连调两次, 不重复追加."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写汇报"},
    ]
    body = {
        "tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]
    }
    once = inject_skill_guard(msgs, body)
    twice = inject_skill_guard(once, body)
    assert once[0]["content"] == twice[0]["content"]


def test_inject_does_not_mutate_input():
    msgs = [
        {"role": "system", "content": "原始"},
        {"role": "user", "content": "写汇报"},
    ]
    original = msgs[0]["content"]
    inject_skill_guard(msgs, {"tools": []})
    assert msgs[0]["content"] == original


def test_inject_targets_last_system_when_multiple():
    msgs = [
        {"role": "system", "content": "first"},
        {"role": "user", "content": "u1"},
        {"role": "system", "content": "second"},
        {"role": "user", "content": "写汇报"},
    ]
    body = {
        "tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]
    }
    out = inject_skill_guard(msgs, body)
    assert out[0]["content"] == "first"  # 第一段不动
    assert "catfish_run_skill" in out[2]["content"]  # 第二段被追加


def test_inject_without_body_assumes_tool_present():
    """body=None 时假设工具就位 (避免误警告)."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写汇报"},
    ]
    out = inject_skill_guard(msgs, None)
    assert "必须" in out[0]["content"]  # REQUIRED 路径
    assert "未注册" not in out[0]["content"]


# ── BL-SKILL-INTENT-PPT (5/15 14:24 鸿波撞 agent 自走 execute_code 不调 skill) ──
#
# pattern 之前只列 leadership-briefing + weekly-report 触发词. 用户点名
# 'creative/guizang-ppt-magazine' 或说 'PPT / 杂志风 / slides' 都没触发,
# gateway 没注入铁律 → agent 自由选 execute_code 嘴炮不真调.


@pytest.mark.parametrize("content", [
    "用 creative/guizang-ppt-magazine skill 做企业资质分析, 杂志风",
    "帮我做一份 PPT",
    "做一份杂志风 PPT",
    "我要做 10 页 slides",
    "做一份瑞士风的 deck",
    "做个 keynote 给老板看",
    "幻灯片整理一下",
    "调 creative/guizang-ppt-magazine",
    "用 department/leadership-briefing 写一份汇报",
    "run department/weekly-report",
    "做一份给陈总的演讲稿",
])
def test_ppt_intent_keywords_trigger(content):
    """新加的 PPT / slides / deck / keynote / 杂志风 / 演讲稿 等关键词"""
    msgs = [{"role": "user", "content": content}]
    assert has_skill_intent(msgs), f"应触发: {content}"


@pytest.mark.parametrize("content", [
    "creative/guizang-ppt-magazine",  # 裸 path, 真存在
    "用 department/leadership-briefing",
    "调 department/weekly-report",
    "invoke department/eis-checkin",
])
def test_explicit_skill_path_triggers(content):
    """显式 skill path 命中**真存在的** skill 时触发 (BL-SKILL-METADATA-DYNAMIC:
    新版只触发真存在的 skill_path, 不再匹配 finance/q3-report 这种幻觉路径)."""
    msgs = [{"role": "user", "content": content}]
    assert has_skill_intent(msgs), f"显式 path 应触发: {content}"


@pytest.mark.parametrize("content", [
    "调 finance/q3-report",  # finance 不存在
    "use creative/foo-bar",   # foo-bar 不存在
    "department/no-such-skill",
])
def test_nonexistent_skill_path_does_not_trigger(content):
    """显式 path 但 skill 不存在 — 新版不触发 (老版会幻觉触发)"""
    msgs = [{"role": "user", "content": content}]
    assert not has_skill_intent(msgs), f"不存在的 path 不该触发: {content}"


@pytest.mark.parametrize("content", [
    "什么是 deep learning",  # learning 不是 skill 词, deep 也不是
    "讲讲北京天气",
    "1+1 = ?",
    "今天股价多少",
    "我喜欢 swift 但不喜欢 typescript",  # 没 skill 关键词
])
def test_non_skill_chat_does_not_trigger(content):
    """普通闲聊不应触发"""
    msgs = [{"role": "user", "content": content}]
    assert not has_skill_intent(msgs), f"不该触发: {content}"


def test_ppt_skill_with_tool_present_injects_required_block():
    """PPT 意图 + body.tools 含 catfish_run_skill → 注入 REQUIRED 铁律"""
    msgs = [
        {"role": "system", "content": "You are 鲶鱼."},
        {"role": "user", "content": "用 creative/guizang-ppt-magazine skill 做杂志风 PPT"},
    ]
    body = {
        "tools": [
            {"type": "function", "function": {"name": "catfish_run_skill"}},
            {"type": "function", "function": {"name": "execute_code"}},
        ],
    }
    out = inject_skill_guard(msgs, body)
    system = out[0]["content"]
    assert "铁律 1" in system, "缺 REQUIRED 铁律"
    assert "creative/guizang-ppt-magazine" in system, "应明确告诉 agent 走哪个 skill"
    # BL-SKILL-INTENT-PPT 新加的反嘴炮铁律 3
    assert "嘴炮" in system, "反嘴炮铁律必须注入"
    # 指令型 skill 铁律 7
    assert "指令型 skill" in system, "指令型 skill 铁律必须注入"


def test_ppt_intent_message_in_history_with_followup_does_not_re_trigger():
    """skill_guard 只看最后一条 user, 不扫历史 (历史里 PPT 但当前 user 是问候 → 不触发)"""
    msgs = [
        {"role": "user", "content": "用 creative/guizang-ppt-magazine 做 PPT"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "你好吗"},  # 最近 user 是闲聊
    ]
    assert not has_skill_intent(msgs)


# ─── BL-SKILL-METADATA-DYNAMIC (5/15 鸿波 '半半的工作造成更大困恼') ─────
#
# skill_guard 完全 dynamic — 触发词 / 推荐 skill_path / 接力步骤
# 全部从 SkillMeta.triggers + .kind 拼装. 加新 skill 不用动 gateway 代码.

from dataclasses import dataclass


@dataclass
class _FakeSkill:
    """单测专用 — 模拟 SkillMeta. 不依赖 discover_skills 真扫盘."""
    skill_path: str
    name: str = ""
    description: str = ""
    triggers: tuple = ()
    kind: str = "procedural"
    deprecated: bool = False


def test_dynamic_trigger_map_from_skills_metadata():
    """trigger 列表完全来自 SkillMeta.triggers — 不硬编码"""
    from catfish_gateway.skill_guard import _build_trigger_map

    skills = [
        _FakeSkill(skill_path="creative/foo", triggers=("FooBar", "酷东西")),
        _FakeSkill(skill_path="department/bar", triggers=("条形码扫描", "barcode")),
    ]
    pattern, m = _build_trigger_map(skills)
    assert pattern is not None
    assert pattern.search("我要 FooBar 一下")
    assert pattern.search("帮我做个条形码扫描")
    assert pattern.search("scan a barcode") is not None  # 大小写不敏感
    assert pattern.search("跟我聊聊天气") is None
    # 映射回 skill_path
    assert m["foobar"] == "creative/foo"
    assert m["条形码扫描"] == "department/bar"


def test_dynamic_no_trigger_when_skills_empty():
    """skills 空时 — pattern 是 None, intent 永不触发"""
    from catfish_gateway.skill_guard import _build_trigger_map

    pattern, m = _build_trigger_map([])
    assert pattern is None
    assert m == {}
    assert not has_skill_intent([{"role": "user", "content": "PPT"}], skills=[])


def test_dynamic_deprecated_skill_excluded_from_trigger():
    """deprecated=True 的 skill 的 triggers 不参与匹配"""
    from catfish_gateway.skill_guard import _build_trigger_map

    skills = [
        _FakeSkill(skill_path="old/foo", triggers=("过期词",), deprecated=True),
        _FakeSkill(skill_path="new/bar", triggers=("新词",)),
    ]
    pattern, m = _build_trigger_map(skills)
    assert pattern is not None
    assert pattern.search("过期词") is None, "deprecated trigger 不该匹配"
    assert pattern.search("新词") is not None
    assert "过期词" not in m


def test_dynamic_inject_uses_real_skill_path_in_block():
    """注入铁律时, 推荐的 skill_path 必须是真命中那个 — 不写死"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "用 myorg/wacky-tool 做点啥"},
    ]
    body = {"tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]}
    fake_skills = [
        _FakeSkill(
            skill_path="myorg/wacky-tool",
            name="wacky",
            description="完全虚构的 skill 用来测 dynamic 注入",
            triggers=("wacky",),
        ),
    ]
    out = inject_skill_guard(msgs, body, skills=fake_skills)
    sys_content = out[0]["content"]
    assert "`myorg/wacky-tool`" in sys_content, "铁律必须 dynamic 含真命中 skill_path"
    assert "wacky" in sys_content  # description 也注入


def test_dynamic_instructional_skill_adds_relay_iron_rule():
    """命中 instructional skill → 铁律 7 (接力步骤) 动态注入"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "做 PPT"},
    ]
    body = {"tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]}
    fake_skills = [
        _FakeSkill(
            skill_path="creative/some-ppt",
            triggers=("PPT",),
            kind="instructional",
        ),
    ]
    out = inject_skill_guard(msgs, body, skills=fake_skills)
    sys_content = out[0]["content"]
    assert "instructional" in sys_content
    assert "接力" in sys_content
    assert "preferred_template" in sys_content
    assert "write_file" in sys_content


def test_dynamic_procedural_only_skill_no_relay_rule():
    """全 procedural 命中 → 铁律 7 (instructional 接力) 不注入, 防废话"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "做周报"},
    ]
    body = {"tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]}
    fake_skills = [
        _FakeSkill(
            skill_path="department/weekly-report",
            triggers=("周报",),
            kind="procedural",
        ),
    ]
    out = inject_skill_guard(msgs, body, skills=fake_skills)
    sys_content = out[0]["content"]
    assert "preferred_template" not in sys_content, "procedural skill 不该有接力铁律"


def test_dynamic_longer_trigger_wins_over_shorter():
    """trigger 按长度倒序: '立项报告' 优先 '立项' (防短词抢匹配)"""
    from catfish_gateway.skill_guard import _build_trigger_map

    skills = [
        _FakeSkill(skill_path="dept/short", triggers=("立项",)),
        _FakeSkill(skill_path="dept/long", triggers=("立项报告",)),
    ]
    pattern, m = _build_trigger_map(skills)
    text = "起草一份立项报告"
    matches = pattern.findall(text)
    # 长 trigger 应该先匹配, 至少包含 "立项报告"
    assert "立项报告" in matches


def test_dynamic_no_change_to_gateway_code_when_adding_skill():
    """这是断言: 加新 skill 真的不需要改 gateway 代码.

    实现层面: skill_guard 只 import skills_loader.discover_skills, 没任何硬编码
    skill_path / trigger / 铁律映射. 加 SKILL.md 后 discover_skills 自动拾到.

    本测试用 reflection 检查 skill_guard 源码不含硬编码 skill 名."""
    import inspect

    from catfish_gateway import skill_guard

    src = inspect.getsource(skill_guard)
    # 不该再有这些硬编码引用 (BL-SKILL-METADATA-DYNAMIC 之前残留)
    banned = [
        "leadership-briefing",
        "weekly-report",
        "guizang-ppt-magazine",
        "杂志风",  # 旧 BL-SKILL-INTENT-PPT 硬编码
        "周报材料",
        "汇报材料",
        "_SKILL_INTENT_PATTERN",
        "_SKILL_REQUIRED_BLOCK",
        "_SKILL_MISSING_BLOCK",
    ]
    found = [b for b in banned if b in src]
    assert not found, f"skill_guard.py 还有硬编码 skill 名/旧常量: {found}"
