"""BL-FED2.5 — 沙箱可跑的 mini E2E (不真起端口/LLM, 跑代码链路).

验证 BL-FED2.1 + 2.2 + 2.3 三层数据流是真接得上的 (没字段错位 / schema 不匹配):

  1. tool-bridge/expertise.py save_expertise() 写 yaml
  2. gateway/a2a_self_register.py _load_confirmed_expertise() 读出来 (BL-FED2.2 关键 roundtrip)
  3. mock identity-server by-expertise → matches
  4. tool-bridge/expert_consult.py 路由 → 选 bob → mock a2a 返答案
  5. assert: 完整链路 ok, expertise tag → 路由到正确员工

跑这个测试可以替代真起 fed_demo.sh 端口 — 沙箱 CI 也能跑.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CATFISH_HOME", str(home / ".catfish"))
    monkeypatch.setenv("CATFISH_USER_SUB", "alice@ffcs.cn")
    monkeypatch.setenv("CATFISH_REGISTRY_URL", "http://mock-registry:18998")
    monkeypatch.setenv("CATFISH_GATEWAY_URL", "http://mock-gateway:18999")
    return home


def test_fed25_full_chain(fake_home: Path):
    """BL-FED2.5 完整数据链 sandbox 友好版."""
    # === Step 1: Bob 那边写 expertise.yaml (BL-FED2.1 输出) ===
    from catfish_tool_bridge import expertise
    # 模拟 bob 的 catfish_home (在 fake_home 下另起目录)
    bob_home = fake_home / "bob-catfish"
    bob_home.mkdir(parents=True)
    expertise.EXPERTISE_PATH = bob_home / "expertise.yaml"
    expertise.save_expertise({
        "extracted_at": "2026-05-12T10:00:00",
        "source": "employee_journal.md",
        "auto_review_pending": False,
        "tags": [
            {
                "tag": "资质审核",
                "confidence": 0.92,
                "evidence_count": 27,
                "aliases": ["资质"],
                "status": "confirmed",
                "last_reviewed_at": "2026-05-12T10:00:00",
            },
            {
                "tag": "外勤报销",
                "confidence": 0.7,
                "evidence_count": 8,
                "aliases": [],
                "status": "pending",  # 未 confirm — 黄页应该看不到
                "last_reviewed_at": None,
            },
        ],
    })
    yaml_text = (bob_home / "expertise.yaml").read_text(encoding="utf-8")
    assert "资质审核" in yaml_text
    assert "status: confirmed" in yaml_text

    # === Step 2: gateway self_register 解析 yaml — 应只返 confirmed ===
    import sys
    # 临时把 bob_home 当作 ~/.catfish 让 _load_confirmed_expertise 读 (它走 Path.home())
    # 修补 a2a_self_register._expertise_yaml_path
    from catfish_gateway import a2a_self_register
    orig_path = a2a_self_register._expertise_yaml_path
    a2a_self_register._expertise_yaml_path = lambda: bob_home / "expertise.yaml"
    try:
        confirmed_tags = a2a_self_register._load_confirmed_expertise()
    finally:
        a2a_self_register._expertise_yaml_path = orig_path

    assert "资质审核" in confirmed_tags
    assert "外勤报销" not in confirmed_tags, "pending 不该泄露到中央 registry"

    # === Step 3: mock identity-server by-expertise → 返 bob ===
    # 不真起端口 — 直接用 expert_consult.tool_expert_consult 的 http mock 入口
    from catfish_tool_bridge import expert_consult

    def mock_registry_get(url: str) -> dict[str, Any]:
        """模拟中央 registry /by-expertise endpoint, 用 confirmed_tags 决定 match."""
        # URL 长这样: http://mock-registry:18998/registry/by-expertise?tag=资质审核
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        query_tag = qs.get("tag", [""])[0]
        if query_tag in confirmed_tags:
            return {
                "tag": query_tag,
                "matched_count": 1,
                "online_count": 1,
                "matches": [{
                    "sub": "bob@ffcs.cn",
                    "department": "法务部",
                    "expertise": list(confirmed_tags),
                    "online": True,
                    "last_seen": "2026-05-12T10:30:00+00:00",
                }],
            }
        return {"tag": query_tag, "matched_count": 0, "online_count": 0, "matches": []}

    # mock gateway /a2a/internal/ask
    captured_a2a: dict[str, Any] = {}

    def mock_a2a_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured_a2a.update(body)
        return {"ok": True, "answer": "走 OA 工单, 类目选合规", "chunks_count": 5}

    # === Step 4: alice 调 catfish_expert_consult ===
    resp = expert_consult.tool_expert_consult(
        {
            "expertise_tag": "资质审核",
            "question": "资质审核怎么搞?",
        },
        http_get=mock_registry_get,
        http_post=mock_a2a_post,
    )

    # === Step 5: 验证全链路 ===
    assert resp["ok"] is True, f"expert_consult 失败: {resp}"
    assert resp["routed_to"] == "bob@ffcs.cn", "应该路由到 bob"
    assert resp["routed_department"] == "法务部"
    assert resp["answer"] == "走 OA 工单, 类目选合规"
    assert resp["matched_count"] == 1
    assert resp["online_count"] == 1
    # a2a body 透传 from_sub=alice (env)
    assert captured_a2a["from_sub"] == "alice@ffcs.cn"
    assert captured_a2a["to_sub"] == "bob@ffcs.cn"
    assert captured_a2a["question"] == "资质审核怎么搞?"
    # purpose 自动加 expert_consult: 前缀
    assert captured_a2a["purpose"] == "expert_consult:资质审核"


def test_fed25_pending_tag_not_routable(fake_home: Path):
    """**隐私防御**: pending 的 tag 黄页查不到 → 不会被路由."""
    from catfish_tool_bridge import expertise, expert_consult

    bob_home = fake_home / "bob-catfish"
    bob_home.mkdir(parents=True)
    expertise.EXPERTISE_PATH = bob_home / "expertise.yaml"
    expertise.save_expertise({
        "extracted_at": "2026-05-12T10:00:00",
        "tags": [{
            "tag": "敏感专长",
            "confidence": 0.9,
            "evidence_count": 5,
            "status": "pending",  # 关键: 未 confirm
            "last_reviewed_at": None,
        }],
    })

    from catfish_gateway import a2a_self_register
    a2a_self_register._expertise_yaml_path = lambda: bob_home / "expertise.yaml"
    confirmed_tags = a2a_self_register._load_confirmed_expertise()
    assert confirmed_tags == [], "pending tag 必须不出员工 mac"

    # 模拟 registry: alice 查"敏感专长", 因 bob 没上报 → matched_count=0
    def mock_registry_get(url: str) -> dict[str, Any]:
        return {"tag": "敏感专长", "matched_count": 0, "online_count": 0, "matches": []}

    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "敏感专长", "question": "?"},
        http_get=mock_registry_get,
        http_post=lambda u, b: {"ok": True, "answer": ""},
    )
    assert resp["ok"] is False
    assert resp["matched_count"] == 0
    assert "没人" in resp["error"]


def test_fed25_rejected_tag_not_routable(fake_home: Path):
    """rejected 的 tag 同样不路由."""
    from catfish_tool_bridge import expertise
    from catfish_gateway import a2a_self_register

    bob_home = fake_home / "bob-catfish"
    bob_home.mkdir(parents=True)
    expertise.EXPERTISE_PATH = bob_home / "expertise.yaml"
    expertise.save_expertise({
        "extracted_at": "2026-05-12T10:00:00",
        "tags": [
            {"tag": "X", "confidence": 0.9, "evidence_count": 5, "status": "rejected",
             "last_reviewed_at": None},
            {"tag": "Y", "confidence": 0.9, "evidence_count": 5, "status": "confirmed",
             "last_reviewed_at": None},
        ],
    })
    a2a_self_register._expertise_yaml_path = lambda: bob_home / "expertise.yaml"
    confirmed_tags = a2a_self_register._load_confirmed_expertise()
    assert "X" not in confirmed_tags
    assert "Y" in confirmed_tags
