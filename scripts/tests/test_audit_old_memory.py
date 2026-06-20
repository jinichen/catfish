"""P3.5.42.2 — audit-old-memory.py 单测.

跑法 (从 catfish 仓库根):
  python3 -m pytest scripts/tests/test_audit_old_memory.py -q

覆盖:
- 加载 memory_enforce module 不挂
- _read_entries 切 ENTRY_DELIMITER 正确 (跟 hermes-memory-cleanup.py 对齐)
- _classify_all 端到端 (mock LLM 返不同 route)
- build_report 统计算对
- render_markdown 不挂 + 每个 section 都覆盖
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# 加载 scripts/audit-old-memory.py 这个非 importable 名字的脚本
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "audit-old-memory.py"


@pytest.fixture
def audit_mod():
    spec = importlib.util.spec_from_file_location("audit_old_memory", _SCRIPT_PATH)
    assert spec and spec.loader, "spec_from_file_location 挂"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tmp_hermes(tmp_path, monkeypatch):
    """临时 ~/.hermes/memories/, 写预设 USER.md + MEMORY.md."""
    hermes = tmp_path / ".hermes" / "memories"
    hermes.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() 在 Linux 走 HOME env, 在 macOS 走 pw_dir, 这里 monkeypatch 也兜
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return hermes


def test_load_memory_enforce_finds_module(audit_mod):
    """_load_memory_enforce 真能从 catfish 仓库内 plugin 加载."""
    enforce = audit_mod._load_memory_enforce()
    assert enforce is not None, "memory_enforce module 加载失败"
    assert hasattr(enforce, "_classify_memory_route")
    assert hasattr(enforce, "get_verifier_model")


def test_read_entries_splits_on_delimiter(audit_mod, tmp_hermes):
    """ENTRY_DELIMITER = '\\n§\\n' 按 hermes memory_provider 切."""
    (tmp_hermes / "MEMORY.md").write_text(
        "entry 1 content\n§\nentry 2 content\n§\nentry 3 content",
        encoding="utf-8",
    )
    entries = audit_mod._read_entries("MEMORY")
    assert len(entries) == 3
    assert entries[0] == "entry 1 content"
    assert entries[2] == "entry 3 content"


def test_read_entries_strips_and_drops_empty(audit_mod, tmp_hermes):
    (tmp_hermes / "USER.md").write_text(
        "  e1  \n§\n\n§\n  e2  ", encoding="utf-8",
    )
    entries = audit_mod._read_entries("USER")
    assert entries == ["e1", "e2"]


def test_read_entries_missing_file_returns_empty(audit_mod, tmp_hermes):
    assert audit_mod._read_entries("MEMORY") == []


def _mock_enforce(route_map):
    """造 fake memory_enforce module. P3.5.42.6 后 audit 不直接调 enforce 的 LLM
    函数, 走 stdlib; enforce 只用来拿 prompt 常量 + get_verifier_model. classify
    行为靠 monkeypatch audit_mod._classify_via_stdlib."""
    class _Fake:
        _CLASSIFY_SYSTEM_PROMPT = "test classify prompt"  # P3.5.42.6 需要这常量

        @staticmethod
        def get_verifier_model():
            return "test-model"

        # _classify_memory_route 留个旧接口测兜底
        @staticmethod
        def _classify_memory_route(content, model):
            return None
    _Fake._route_map = route_map  # type: ignore[attr-defined]
    return _Fake()


def _mock_classify_via_stdlib(route_map):
    """造一个 _classify_via_stdlib 的 mock, 按 content 关键字返预设."""
    def _impl(content, model, prompt, gateway_url, timeout=30.0):
        for key, route in route_map.items():
            if key in content:
                return {"route": route, "reason": f"matched {key}",
                        "confidence": 0.9}, ""
        return None, ""  # 没 match → fail-silent (空 error)
    return _impl


def test_classify_all_keep_decision(audit_mod, capsys, monkeypatch):
    """route == target → decision=keep."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        _mock_classify_via_stdlib({"ISO 流程": "memory"}))
    results = audit_mod._classify_all("MEMORY", ["ISO 流程 详细步骤"], _mock_enforce({}))
    assert len(results) == 1
    assert results[0]["decision"] == "keep"
    assert results[0]["llm_route"] == "memory"


def test_classify_all_suggest_retarget(audit_mod, capsys, monkeypatch):
    """target=MEMORY → actual_target='memory' 但 route='user' → suggest_retarget."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        _mock_classify_via_stdlib({"鸿波偏好": "user"}))
    results = audit_mod._classify_all("MEMORY", ["鸿波偏好 直接输出"], _mock_enforce({}))
    assert results[0]["decision"] == "suggest_retarget"
    assert results[0]["llm_route"] == "user"


def test_classify_all_suggest_delete_journal(audit_mod, capsys, monkeypatch):
    """route=journal → suggest_delete."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        _mock_classify_via_stdlib({"飞抵福州": "journal"}))
    results = audit_mod._classify_all("MEMORY", ["陈某 6/19 飞抵福州 MF878"], _mock_enforce({}))
    assert results[0]["decision"] == "suggest_delete"
    assert results[0]["llm_route"] == "journal"


def test_classify_all_suggest_delete_todo(audit_mod, capsys, monkeypatch):
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        _mock_classify_via_stdlib({"9 月底": "todo"}))
    results = audit_mod._classify_all("MEMORY", ["9 月底前提交资质报告"], _mock_enforce({}))
    assert results[0]["decision"] == "suggest_delete"
    assert results[0]["llm_route"] == "todo"


def test_classify_all_skip_when_llm_fails(audit_mod, capsys, monkeypatch):
    """classify 返 None → decision=skip."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        _mock_classify_via_stdlib({}))
    results = audit_mod._classify_all("MEMORY", ["xxx"], _mock_enforce({}))
    assert results[0]["decision"] == "skip"
    assert results[0]["llm_route"] is None


def test_build_report_counts(audit_mod):
    results = [
        {"decision": "keep"}, {"decision": "keep"},
        {"decision": "suggest_retarget"},
        {"decision": "suggest_delete"}, {"decision": "suggest_delete"},
        {"decision": "skip"},
    ]
    # 补必要字段防 render_markdown 挂
    for r in results:
        r.update({
            "index": 1, "target": "MEMORY", "actual_target": "memory",
            "content_preview": "x", "content_len": 1,
            "llm_route": "memory", "llm_reason": "test", "confidence": 0.5,
            "content": "x",
        })
    rep = audit_mod.build_report(results)
    assert rep["total"] == 6
    assert rep["keep"] == 2
    assert rep["suggest_retarget"] == 1
    assert rep["suggest_delete"] == 2
    assert rep["skip"] == 1


def test_render_markdown_covers_all_sections(audit_mod):
    """每种 decision 至少一个, render_markdown 不挂 + 输出含 sections.

    P3.5.42.3 改: skip section 只显计数, 不罗列内容预览 (鸿波 6/20 catch).
    """
    results = [
        {"index": 1, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "ISO 流程", "content_len": 10,
         "llm_route": "memory", "llm_reason": "稳定项目常量", "confidence": 0.9,
         "decision": "keep", "content": "x"},
        {"index": 2, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "鸿波偏好", "content_len": 10,
         "llm_route": "user", "llm_reason": "员工偏好", "confidence": 0.85,
         "decision": "suggest_retarget", "content": "x"},
        {"index": 3, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "陈某飞抵", "content_len": 10,
         "llm_route": "journal", "llm_reason": "单次事件 + 日期", "confidence": 0.95,
         "decision": "suggest_delete", "content": "x"},
        {"index": 4, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "xxx", "content_len": 3,
         "llm_route": None, "llm_reason": "classify 失败", "confidence": 0.0,
         "decision": "skip", "content": "x"},
    ]
    rep = audit_mod.build_report(results)
    md = audit_mod.render_markdown(rep)

    assert "# Hermes Memory 旧数据 LLM 审查报告" in md
    assert "❌ 建议删" in md
    assert "⚠️ 建议改 target" in md
    assert "## ⏭ 跳过 (1)" in md  # section 标题只显计数
    assert "xxx" not in md  # skip entry 内容预览不在 markdown 里
    assert "✅ 留" in md
    # cleanup 命令清单存在
    assert "hermes-memory-cleanup.py delete MEMORY 3" in md
    # JSON-safe — confidence float 格式化对
    assert "0.95" in md


def test_render_markdown_no_skip_section_when_zero_skip(audit_mod):
    """P3.5.42.3: 0 skip 时连 header 计数都不出 skip 行."""
    results = [
        {"index": 1, "target": "MEMORY", "actual_target": "memory",
         "content_preview": "x", "content_len": 1,
         "llm_route": "memory", "llm_reason": "y", "confidence": 0.9,
         "decision": "keep", "content": "x"},
    ]
    rep = audit_mod.build_report(results)
    md = audit_mod.render_markdown(rep)
    # 没有 skip section 标题 (## ⏭ 跳过 ...)
    assert "## ⏭ 跳过" not in md


def test_render_markdown_no_skip_section_when_all_skip(audit_mod):
    """P3.5.42.3: skip 占 100% 时 render 也不列 skip section 详情.

    (实际 main() 会 exit 1 早 abort, 这测兜底 render_markdown 自己也 robust.)
    """
    results = [
        {"index": i, "target": "MEMORY", "actual_target": "memory",
         "content_preview": f"e{i}", "content_len": 2,
         "llm_route": None, "llm_reason": "fail", "confidence": 0.0,
         "decision": "skip", "content": "x"}
        for i in range(1, 5)
    ]
    rep = audit_mod.build_report(results)
    md = audit_mod.render_markdown(rep)
    assert "## ⏭ 跳过" not in md  # 全挂时不列 skip section 详情
    assert "e1" not in md  # entry 内容不漏


def test_classify_all_early_abort_after_3_consecutive_skip(audit_mod, capsys, monkeypatch):
    """P3.5.42.3: 连续 3 条 classify 挂 → 早 abort, 不跑完后面."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        lambda *a, **kw: (None, "test error"))

    class _Enforce:
        _CLASSIFY_SYSTEM_PROMPT = "p"

        @staticmethod
        def get_verifier_model():
            return "broken-model"

    entries = [f"entry {i}" for i in range(1, 11)]
    results = audit_mod._classify_all("MEMORY", entries, _Enforce())
    assert len(results) == 3, f"早 abort 应只跑 3 条, 实际 {len(results)}"
    assert all(r["decision"] == "skip" for r in results)
    captured = capsys.readouterr()
    assert "早 abort" in captured.err


def test_classify_all_override_model_used_when_passed(audit_mod, capsys, monkeypatch):
    """P3.5.42.4: 传 override_model → 直接用, 不调 enforce.get_verifier_model()."""
    seen = {}

    def _spy(content, model, prompt, gateway_url, timeout=30.0):
        seen["model"] = model
        return {"route": "memory", "reason": "ok", "confidence": 0.9}, ""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib", _spy)

    class _Capture:
        _CLASSIFY_SYSTEM_PROMPT = "p"

        @staticmethod
        def get_verifier_model():
            seen["picker_called"] = True
            return "should-not-use"

    audit_mod._classify_all("MEMORY", ["e1"], _Capture(),
                            override_model="catfish-public-deepseek-flash")
    assert seen["model"] == "catfish-public-deepseek-flash"
    assert "picker_called" not in seen  # 不调 picker chain
    err = capsys.readouterr().err
    assert "--model 覆盖" in err  # stderr 报源


def test_classify_all_no_override_falls_to_picker(audit_mod, capsys, monkeypatch):
    """P3.5.42.4: 不传 override → 走 enforce.get_verifier_model() (picker chain)."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        lambda *a, **kw: ({"route": "memory", "reason": "ok",
                                           "confidence": 0.9}, ""))

    class _Picker:
        _CLASSIFY_SYSTEM_PROMPT = "p"

        @staticmethod
        def get_verifier_model():
            return "catfish-private-main"

    audit_mod._classify_all("MEMORY", ["e1"], _Picker())
    err = capsys.readouterr().err
    assert "picker chain" in err
    assert "catfish-private-main" in err


def test_classify_all_early_abort_hint_recommends_model_flag_for_private(
        audit_mod, capsys, monkeypatch):
    """P3.5.42.4: 走 picker + 选了 private model + 全挂 → 早 abort 提示加 --model."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        lambda *a, **kw: (None, "TimeoutError: upstream unreachable"))

    class _DeadPrivate:
        _CLASSIFY_SYSTEM_PROMPT = "p"

        @staticmethod
        def get_verifier_model():
            return "catfish-private-main"

    audit_mod._classify_all("MEMORY", [f"e{i}" for i in range(5)], _DeadPrivate())
    err = capsys.readouterr().err
    assert "--model catfish-public-deepseek-flash" in err
    assert "数据已在本机 MEMORY.md" in err


def test_classify_all_early_abort_hint_no_private_recommend_for_override(
        audit_mod, capsys, monkeypatch):
    """P3.5.42.4: 用户已经用 --model 还全挂 → 别再建议 --model, 报 gateway 配."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        lambda *a, **kw: (None, "gateway HTTP 401: Bearer required"))

    class _AllFail:
        _CLASSIFY_SYSTEM_PROMPT = "p"

        @staticmethod
        def get_verifier_model():
            return "should-not-use"

    audit_mod._classify_all("MEMORY", [f"e{i}" for i in range(5)], _AllFail(),
                            override_model="catfish-public-deepseek-flash")
    err = capsys.readouterr().err
    # 不再 hardcode 重复建议同一个 model
    assert "原因可能: 这是内网 model" not in err
    assert "HTTP 401" in err  # 真错被报


def test_classify_all_reports_real_error_from_stdlib(audit_mod, capsys, monkeypatch):
    """P3.5.42.5/6: classify 第一条挂时直接报真错 (stdlib path)."""
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        lambda *a, **kw: (None, "gateway HTTP 401: Bearer required"))

    class _E:
        _CLASSIFY_SYSTEM_PROMPT = "p"

        @staticmethod
        def get_verifier_model():
            return "test-model"

    audit_mod._classify_all("MEMORY", ["e1", "e2", "e3"], _E())
    err = capsys.readouterr().err
    assert "真错: gateway HTTP 401: Bearer required" in err
    assert "真错 (第一条 entry): gateway HTTP 401" in err


def test_classify_all_fails_when_no_prompt_constant(audit_mod, capsys):
    """P3.5.42.6: enforce 没 _CLASSIFY_SYSTEM_PROMPT (老版本) → 提醒升级 + 返空."""
    class _Old:
        @staticmethod
        def get_verifier_model():
            return "test-model"
        # 故意没 _CLASSIFY_SYSTEM_PROMPT

    results = audit_mod._classify_all("MEMORY", ["e1", "e2", "e3"], _Old())
    assert results == []
    err = capsys.readouterr().err
    assert "版本太老没 _CLASSIFY_SYSTEM_PROMPT" in err


def test_classify_all_no_abort_when_skip_breaks(audit_mod, monkeypatch):
    """P3.5.42.3: 连续 skip 计数被成功 classify 重置, 不会误 abort."""
    calls = iter([
        (None, "fail"),
        (None, "fail"),
        ({"route": "memory", "reason": "ok", "confidence": 0.9}, ""),
        (None, "fail"),
        (None, "fail"),
        ({"route": "memory", "reason": "ok", "confidence": 0.9}, ""),
    ])
    monkeypatch.setattr(audit_mod, "_classify_via_stdlib",
                        lambda *a, **kw: next(calls))

    class _Flaky:
        _CLASSIFY_SYSTEM_PROMPT = "p"

        @staticmethod
        def get_verifier_model():
            return "flaky-model"

    entries = [f"e{i}" for i in range(6)]
    results = audit_mod._classify_all("MEMORY", entries, _Flaky())
    # 6 条全跑完, 不 abort (连续 skip 中间有 keep 打断)
    assert len(results) == 6


def test_classify_via_stdlib_handles_http_error(audit_mod, monkeypatch):
    """P3.5.42.6: stdlib HTTP 401 错码被解析报出来."""
    import io
    import urllib.error as _ue

    def _fake_urlopen(req, timeout=30.0):
        raise _ue.HTTPError(
            req.full_url, 401, "Unauthorized",
            {}, io.BytesIO(b'{"error":"Bearer required"}'),
        )
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    cls, err = audit_mod._classify_via_stdlib(
        "x", "test-model", "test prompt", "http://127.0.0.1:8999",
    )
    assert cls is None
    assert "HTTP 401" in err
    assert "Bearer required" in err


def test_classify_via_stdlib_handles_url_error(audit_mod, monkeypatch):
    """P3.5.42.6: gateway 没起 (ConnectionRefused) → URLError 报清楚."""
    import urllib.error as _ue

    def _fake_urlopen(req, timeout=30.0):
        raise _ue.URLError(ConnectionRefusedError("connection refused"))
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    cls, err = audit_mod._classify_via_stdlib(
        "x", "test-model", "p", "http://127.0.0.1:8999",
    )
    assert cls is None
    assert "HTTP 异常" in err
    assert "connection refused" in err.lower()


def test_classify_via_stdlib_parses_success(audit_mod, monkeypatch):
    """P3.5.42.6: 200 成功 + JSON content 解析对."""
    # hardcoded JSON body 不依赖测里 json.dumps
    _content_json = '{"route":"journal","reason":"单次事件","confidence":0.95}'
    _outer = '{"choices":[{"message":{"content":' + json.dumps(_content_json) + '}}]}'
    _outer_bytes = _outer.encode("utf-8")

    class _FakeResp:
        def read(self):
            return _outer_bytes

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=30.0):
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    cls, err = audit_mod._classify_via_stdlib(
        "陈某飞抵福州", "test-model", "p", "http://127.0.0.1:8999",
    )
    assert err == ""
    assert cls["route"] == "journal"
    assert cls["confidence"] == 0.95
