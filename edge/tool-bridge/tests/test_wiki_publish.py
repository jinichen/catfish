"""wiki_publish 单测 (P3.3.18 Phase 2, 6/10).

测覆盖:
- helper: _parse_frontmatter / _extract_field / _infer_kind_from_path /
  _load_sensitive_terms / _scan_sensitive_terms
- main flow: 文件不存在 / 路径不合法 / namespace 不合法 / 凭据拒 /
  PII 警告 (默认拒) / ack_warnings 跳警告 (OAuth 缺时验证 scan 已过)

不跑 httpx upload (避免 mock OAuth + gateway), 都在扫描阶段验证.
"""
from __future__ import annotations

from catfish_tool_bridge import wiki_publish


# ─── helper 单测 ────────────────────────────────────────────


def test_parse_frontmatter_standard():
    text = "---\nname: x\nversion: '1.0'\n---\n\n# Body\n"
    fm, body = wiki_publish._parse_frontmatter(text)
    assert "name: x" in fm
    assert "version: '1.0'" in fm
    assert body.startswith("# Body")


def test_parse_frontmatter_missing():
    """无 frontmatter → fm 空, body 全文."""
    text = "# Just a wiki, no frontmatter\n\nBody content"
    fm, body = wiki_publish._parse_frontmatter(text)
    assert fm == ""
    assert body == text


def test_parse_frontmatter_broken():
    """frontmatter 开始没结束 → fm 空, body 原文."""
    text = "---\nname: x\nnothing closes this"
    fm, body = wiki_publish._parse_frontmatter(text)
    assert fm == ""
    assert body == text


def test_extract_field_basic():
    fm = "name: foo\ntitle: 我的笔记\nversion: '1.0'"
    assert wiki_publish._extract_field(fm, "title") == "我的笔记"
    assert wiki_publish._extract_field(fm, "name") == "foo"
    assert wiki_publish._extract_field(fm, "version") == "1.0"


def test_extract_field_missing():
    fm = "name: x"
    assert wiki_publish._extract_field(fm, "missing") == ""


def test_infer_kind_from_path():
    assert wiki_publish._infer_kind_from_path("wiki/entities/老李.md") == "entity"
    assert wiki_publish._infer_kind_from_path("wiki/concepts/CSMM-4.md") == "concept"
    assert wiki_publish._infer_kind_from_path("wiki/queries/2026-06-10.md") == "query"
    # fallback
    assert wiki_publish._infer_kind_from_path("wiki/strange/x.md") == "entity"


def test_load_sensitive_terms_missing(monkeypatch, tmp_path):
    """文件不存在 → 空 list, 不报错."""
    monkeypatch.setattr(wiki_publish, "SENSITIVE_TERMS_PATH", tmp_path / "no_such")
    assert wiki_publish._load_sensitive_terms() == []


def test_load_sensitive_terms_parsing(monkeypatch, tmp_path):
    """跳过 # 注释 + 空行, 收集真实词."""
    f = tmp_path / "sensitive_terms.txt"
    f.write_text(
        "# 客户名\n"
        "FFCS\n"
        "\n"
        "中电福富\n"
        "  # 项目代号\n"
        "CSMM-4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(wiki_publish, "SENSITIVE_TERMS_PATH", f)
    terms = wiki_publish._load_sensitive_terms()
    assert "FFCS" in terms
    assert "中电福富" in terms
    assert "CSMM-4" in terms
    assert len(terms) == 3  # # 注释 + 空行不算


def test_scan_sensitive_terms_hit():
    text = "今天跟 FFCS 的人开了个会, 谈 CSMM-4 的下一步."
    terms = ["FFCS", "CSMM-4", "未提及词"]
    hits = wiki_publish._scan_sensitive_terms(text, terms)
    hit_terms = {h["term"] for h in hits}
    assert "FFCS" in hit_terms
    assert "CSMM-4" in hit_terms
    assert "未提及词" not in hit_terms


def test_scan_sensitive_terms_case_insensitive():
    text = "ffcs is here"
    terms = ["FFCS"]
    hits = wiki_publish._scan_sensitive_terms(text, terms)
    assert len(hits) == 1


def test_scan_sensitive_terms_empty():
    """terms 空 → 空 list (防被空 term 误命中)."""
    text = "anything"
    assert wiki_publish._scan_sensitive_terms(text, []) == []


# ─── main flow 单测 ─────────────────────────────────────────


def test_publish_rejects_missing_wiki_rel_path():
    result = wiki_publish.wiki_publish({"namespace": "dept/finance"})
    assert result["ok"] is False
    assert "wiki_rel_path" in result["error"]


def test_publish_rejects_missing_namespace():
    result = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/x.md"})
    assert result["ok"] is False
    assert "namespace" in result["error"]


def test_publish_rejects_path_traversal():
    result = wiki_publish.wiki_publish({
        "wiki_rel_path": "wiki/../etc/passwd",
        "namespace": "dept/finance",
    })
    assert result["ok"] is False


def test_publish_rejects_path_not_starting_wiki():
    result = wiki_publish.wiki_publish({
        "wiki_rel_path": "other/x.md",
        "namespace": "dept/finance",
    })
    assert result["ok"] is False
    assert "wiki/" in result["error"]


def test_publish_rejects_invalid_namespace_format():
    """namespace 必须 dept/<部门>, 公司级不允许."""
    result = wiki_publish.wiki_publish({
        "wiki_rel_path": "wiki/entities/x.md",
        "namespace": "public",  # 不是 dept/X 格式
    })
    assert result["ok"] is False
    assert "dept/" in result["error"]


def test_publish_rejects_credentials(monkeypatch, tmp_path):
    """凭据扫命中 → 拒 scan_phase=credentials."""
    # 假装 catfish_home → tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    wiki_dir = fake_home / ".catfish" / "wiki" / "entities"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "x.md").write_text(
        "---\ntitle: T\n---\n\napi_key: abcd1234efgh5678ijkl9012\n",
        encoding="utf-8",
    )

    result = wiki_publish.wiki_publish({
        "wiki_rel_path": "wiki/entities/x.md",
        "namespace": "dept/finance",
    })
    assert result["ok"] is False
    assert result.get("scan_phase") == "credentials"


def test_publish_warns_on_pii_default_rejects(monkeypatch, tmp_path):
    """默认 ack=false 时, PII 撞 → 拒 + warnings 字段 + acknowledge_warnings_available=true."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    wiki_dir = fake_home / ".catfish" / "wiki" / "entities"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "x.md").write_text(
        "---\ntitle: T\n---\n\n老李电话 13800138000\n",
        encoding="utf-8",
    )

    result = wiki_publish.wiki_publish({
        "wiki_rel_path": "wiki/entities/x.md",
        "namespace": "dept/finance",
    })
    assert result["ok"] is False
    assert result.get("scan_phase") == "warnings"
    assert result.get("acknowledge_warnings_available") is True
    warnings = result.get("warnings", [])
    pii_warns = [w for w in warnings if w["category"] == "pii"]
    assert len(pii_warns) == 1


def test_publish_ack_warnings_passes_scan(monkeypatch, tmp_path):
    """acknowledge_warnings=true → 跳警告进 OAuth 阶段 (OAuth 缺时 error 不是 'warnings')."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    wiki_dir = fake_home / ".catfish" / "wiki" / "entities"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "x.md").write_text(
        "---\ntitle: T\n---\n\n老李电话 13800138000\n",
        encoding="utf-8",
    )
    # 让 OAuth 路径不存在
    monkeypatch.setattr(
        wiki_publish, "OAUTH_ID_TOKEN_PATH", fake_home / "no_such_token",
    )
    # 同时也要让 skill_publish 内 OAUTH_ID_TOKEN_PATH 不存在 (wiki_publish 复用它)
    monkeypatch.setattr(
        wiki_publish.skill_publish, "OAUTH_ID_TOKEN_PATH",
        fake_home / "no_such_token",
    )

    result = wiki_publish.wiki_publish({
        "wiki_rel_path": "wiki/entities/x.md",
        "namespace": "dept/finance",
        "acknowledge_warnings": True,
    })
    # 应该不是 warnings 阶段了, 卡 OAuth
    assert result["ok"] is False
    assert result.get("scan_phase") != "warnings"
    assert "OAuth" in result.get("error", "")
