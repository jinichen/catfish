"""P47: hermes approvals suggest 接 UI —— 判据 + 形状契约 (8/9)。

本模块**不重写** hermes 的挖掘/排名/安全过滤, 所以这里不测那些 (那是 hermes
自己的测试面)。这里测的是我们加的那一层:

  · 拿不到 hermes / 拿不到 state.db 时说的是哪一种失败 (封闭词表)
  · **按 pattern 应用而不是按序号** —— 这是跟 CLI 唯一的有意分歧, 也是这层
    最容易被"简化"回去的地方
  · 送回来的 pattern 已经不在提议里时, 拒掉并报回去, 不静默忽略
  · 参数上限 (防 ?limit=999999 扫爆 state.db)
"""
from __future__ import annotations

import sys
import types

import pytest

import approvals_bridge as ab


class _FakeProposal:
    def __init__(self, pattern, kind="glob", count=3, classes=None, examples=None):
        self.pattern = pattern
        self.kind = kind
        self.count = count
        self.classes = set(classes or ["container lifecycle"])
        self.examples = list(examples or ["docker restart web"])


def _install_fake_hermes(monkeypatch, *, proposals, db_exists=True, applied_sink=None):
    """伪造 hermes_cli.approvals_suggest + tools.approval, 让判据能在没 hermes 时测。"""
    class _Path:
        def exists(self):
            return db_exists

        def __str__(self):
            return "/fake/state.db"

    mod = types.ModuleType("hermes_cli.approvals_suggest")
    mod.default_db_path = lambda: _Path()
    mod.scan_approval_history = lambda db, days=90: [("docker restart web", "container lifecycle")]
    mod.build_proposals = lambda records, existing=None, min_count=2, limit=20: list(proposals)

    def _apply(objs, indices):
        if applied_sink is not None:
            applied_sink.extend(objs[i].pattern for i in indices)
        return {"already", *(objs[i].pattern for i in indices)}

    mod.apply_proposals = _apply

    pkg = types.ModuleType("hermes_cli")
    pkg.approvals_suggest = mod
    approval_mod = types.ModuleType("tools.approval")
    approval_mod.load_permanent_allowlist = lambda: {"already"}
    tools_pkg = types.ModuleType("tools")
    tools_pkg.approval = approval_mod

    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.approvals_suggest", mod)
    monkeypatch.setitem(sys.modules, "tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.approval", approval_mod)
    return mod


# ── 列提议 ────────────────────────────────────────────────────

def test_没有_hermes_时说清楚是哪一种失败(monkeypatch):
    monkeypatch.setattr(ab, "_load_hermes", lambda: None)
    out = ab.compute_proposals()
    assert out["ok"] is False
    assert out["reason"] == ab.REASON_HERMES_MISSING
    assert out["proposals"] == []


def test_没有_state_db_跟没有_hermes_要分得开(monkeypatch):
    _install_fake_hermes(monkeypatch, proposals=[], db_exists=False)
    assert ab.compute_proposals()["reason"] == ab.REASON_DB_MISSING


def test_正常返回逐字段对齐_hermes_json(monkeypatch):
    _install_fake_hermes(monkeypatch, proposals=[_FakeProposal("docker restart *")])
    out = ab.compute_proposals()
    assert out["ok"] is True
    p = out["proposals"][0]
    assert set(p) == {"n", "pattern", "kind", "count", "classes", "examples"}
    assert p["n"] == 1
    assert p["pattern"] == "docker restart *"
    assert p["classes"] == ["container lifecycle"]


def test_挖掘出错返结构化结果不抛(monkeypatch):
    mod = _install_fake_hermes(monkeypatch, proposals=[])

    def _boom(*a, **k):
        raise RuntimeError("db 炸了")

    mod.scan_approval_history = _boom
    out = ab.compute_proposals()
    assert out["ok"] is False
    assert out["reason"] == ab.REASON_SCAN_FAILED


# ── 应用: 按 pattern 不按序号 ─────────────────────────────────

def test_按_pattern_应用_而不是序号(monkeypatch):
    sink: list = []
    _install_fake_hermes(
        monkeypatch,
        proposals=[_FakeProposal("git push *"), _FakeProposal("docker restart *")],
        applied_sink=sink,
    )
    out = ab.apply_patterns(["docker restart *"])
    assert out["ok"] is True
    assert out["applied"] == ["docker restart *"]
    assert sink == ["docker restart *"], "应用的不是员工点的那一条"


def test_列表变了之后_对不上的_pattern_被拒且报回来(monkeypatch):
    """UI 渲染和点击之间隔着时间, 列表会变。这是这层存在的全部意义。

    加错一条 = 一个本该弹审批的命令从此静默放行, 所以宁可拒也不能猜。
    """
    _install_fake_hermes(monkeypatch, proposals=[_FakeProposal("git push *")])
    out = ab.apply_patterns(["docker restart *", "git push *"])
    assert out["ok"] is True
    assert out["applied"] == ["git push *"]
    assert out["rejected"] == ["docker restart *"], "没进去的那条必须报回来"


def test_一条都对不上就整体拒(monkeypatch):
    _install_fake_hermes(monkeypatch, proposals=[_FakeProposal("git push *")])
    out = ab.apply_patterns(["早就没了的 pattern"])
    assert out["ok"] is False
    assert out["reason"] == ab.REASON_NOTHING_MATCHED
    assert out["applied"] == []


def test_空输入不写任何东西(monkeypatch):
    sink: list = []
    _install_fake_hermes(monkeypatch, proposals=[_FakeProposal("git push *")], applied_sink=sink)
    for bad in ([], ["", "  "], None):
        out = ab.apply_patterns(bad)
        assert out["ok"] is False
        assert out["reason"] == ab.REASON_NOTHING_MATCHED
    assert sink == [], "空输入居然写了东西"


def test_重复_pattern_只应用一次(monkeypatch):
    sink: list = []
    _install_fake_hermes(monkeypatch, proposals=[_FakeProposal("git push *")], applied_sink=sink)
    ab.apply_patterns(["git push *", "git push *"])
    assert sink == ["git push *"]


def test_非字符串项被忽略不抛(monkeypatch):
    _install_fake_hermes(monkeypatch, proposals=[_FakeProposal("git push *")])
    out = ab.apply_patterns([None, 42, {"pattern": "x"}, "git push *"])  # type: ignore[list-item]
    assert out["applied"] == ["git push *"]


# ── 参数收敛 ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,default,low,high,want", [
    ("50", 20, 1, 100, 50),
    ("999999", 20, 1, 100, 100),
    ("0", 20, 1, 100, 1),
    ("不是数字", 20, 1, 100, 20),
    (None, 20, 1, 100, 20),
])
def test_参数收敛防扫爆(raw, default, low, high, want):
    assert ab._clamp(raw, default, low, high) == want


def test_默认值跟_hermes_CLI_一致():
    """UI 和命令行看到的结果必须是同一个东西, 否则对账时会互相怀疑。

    来源: hermes_cli/subcommands/approvals.py 的 add_argument default。
    """
    assert ab.DEFAULT_DAYS == 90
    assert ab.DEFAULT_MIN_COUNT == 2
    assert ab.DEFAULT_LIMIT == 20


# ── 形状契约: 静态读 hermes 源码 ──────────────────────────────

def _hermes_src():
    import os
    from pathlib import Path
    root = Path(os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent"))
    f = root / "hermes_cli" / "approvals_suggest.py"
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return None


requires_hermes = pytest.mark.skipif(_hermes_src() is None, reason="没找到 hermes 源码")


@requires_hermes
@pytest.mark.parametrize("fn", [
    "def scan_approval_history(",
    "def build_proposals(",
    "def apply_proposals(",
    "def default_db_path(",
])
def test_契约_我们调的四个函数还在(fn):
    """hermes 改了函数名 → 这条先红, 而不是等仪表盘卡片默默空着。"""
    assert fn in _hermes_src()


@requires_hermes
def test_契约_Proposal_还是那几个字段():
    src = _hermes_src()
    for field in ("pattern", "kind", "count", "classes", "examples"):
        assert field in src, f"Proposal 少了 {field} —— 我们的 JSON 形状要跟着改"


@requires_hermes
def test_契约_安全姿态没被放宽():
    """hermes 自己承诺"绝不自动应用 / 破坏性类永不提议"。

    这层完全依赖那个承诺 —— 我们不做二次过滤 (做了反而是两套判据打架)。
    所以它一旦变了, 我们必须知道。
    """
    src = _hermes_src()
    assert "Never auto-applies" in src
    assert "detect_hardline_command" in src
