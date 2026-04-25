"""relevance.py 离线测试。不依赖 Chrome / 飞书 / LLM，能 CI 里跑。"""
from __future__ import annotations

from catfish_feishu.config import RelevanceConfig
from catfish_feishu.relevance import Relevance, judge, sanitize_for_log


def _cfg(**overrides) -> RelevanceConfig:
    base = dict(
        strong_names=["陈鸿波", "鸿波"],
        strong_mentions=["@陈鸿波"],
        soft_projects=["鲶鱼", "catfish", "合规平台"],
        soft_systems=["网关"],
        soft_people=["张总"],
    )
    base.update(overrides)
    return RelevanceConfig(**base)


def test_dm_always_strong():
    cfg = _cfg()
    v = judge("随便一句", "李工", is_direct_message=True, cfg=cfg)
    assert v.level == Relevance.STRONG
    assert "DM" in v.reason


def test_at_mention_strong():
    cfg = _cfg()
    v = judge("@陈鸿波 鲶鱼那个对接怎么样", "某领导", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.STRONG
    assert "@陈鸿波" in v.matched


def test_bare_name_strong():
    cfg = _cfg()
    v = judge("这个事鸿波看一下吧", "某领导", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.STRONG
    assert "鸿波" in v.matched


def test_project_soft():
    cfg = _cfg()
    v = judge("鲶鱼这个项目进度如何", "同事", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.SOFT
    assert "鲶鱼" in v.matched


def test_project_english_lowercase_match():
    cfg = _cfg()
    v = judge("catfish 文档有人更新了吗", "同事", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.SOFT


def test_system_soft():
    cfg = _cfg()
    v = judge("网关那边接口报错了", "运维", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.SOFT
    assert "网关" in v.matched


def test_colleague_mention_soft():
    cfg = _cfg()
    v = judge("张总刚才发了个文档", "同事", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.SOFT
    assert "张总" in v.matched


def test_irrelevant_none():
    cfg = _cfg()
    v = judge("今天午饭吃啥", "同事", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.NONE


def test_empty_message_none():
    cfg = _cfg()
    v = judge("   ", "某人", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.NONE


def test_priority_strong_over_soft():
    """既有项目名又 @ 了姓名，应该是 STRONG（@ 优先）。"""
    cfg = _cfg()
    v = judge("@陈鸿波 鲶鱼这个对接", "领导", is_direct_message=False, cfg=cfg)
    assert v.level == Relevance.STRONG


def test_verdict_action_flags():
    cfg = _cfg()
    strong = judge("@陈鸿波 ?", "x", False, cfg)
    soft = judge("鲶鱼 ?", "x", False, cfg)
    none = judge("吃啥", "x", False, cfg)

    assert strong.should_notify and strong.should_inbox and strong.should_draft
    assert not soft.should_notify and soft.should_inbox and not soft.should_draft
    assert not none.should_notify and not none.should_inbox and not none.should_draft


def test_sanitize_for_log_truncates():
    assert sanitize_for_log("") == ""
    assert sanitize_for_log("短的") == "短的"
    long = "a" * 200
    out = sanitize_for_log(long, 40)
    assert len(out) <= 43  # 40 + "..."
    assert out.endswith("...")


def test_sanitize_removes_newlines():
    assert "\n" not in sanitize_for_log("a\nb\nc")
