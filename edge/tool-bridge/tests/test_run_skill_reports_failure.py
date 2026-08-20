"""skill 说自己没跑成时, run_skill 必须如实报告。

# 病历 (8/20)

`run_skill` 原来在"render 函数没抛异常"这条路上硬编码返回:

    response = {"ok": True, "result": result, "summary": f"已通过 {skill_path} ..."}

完全不看 result 里写了什么。于是:

    script.py 返:  {"ok": False, "error": "验证码识别失败超过 max_captcha_retry 次"}
    run_skill 返:  {"ok": True,  "summary": "已通过 department/eis-login ..."}

模型收到「已通过」, 员工收到「已通过」, 而 EIS 根本没登进去。

8/17-8/18 有 7 次是这样。审计 (`~/.catfish/skill_audit.jsonl`) 里全记成
`ok=True` —— 因为审计写的是同一个硬编码值。所以**两个信号都是坏的**:

  · 审计说 141 次调用 87% 成功  → `ok` 字段根本不看结果
  · mcp-stderr 里数失败行说 0 成功 → 那个 logger 只在失败时写

在唯一能交叉验证的窗口 (7/27-8/20), 审计的 7 次"成功"全部对应 stderr 里的
`skill step ... 失败` —— 100% 是假成功。

更糟的是它把上游那条铁律架空了: `catfish_tool_schemas_skill.py` 写着
「本 tool 返 ok=false 时**必须**报告员工、禁止手工接管」。它永远等不到 false,
于是模型既不报告也不接管, 直接往下走。

# 判据为什么是「显式 falsy」

不是所有 skill 都返 dict、都带 `ok`: 渲染类可能直接返路径字符串或
`{"docx": "/path/x.docx"}`。把「没有 ok」当失败会一次性判死一批能用的 skill ——
那是把判据从太宽拨到太窄, 换个方向犯同一个错。

所以只在**明确返了 ok 且为假**时判失败。下面三条正好钉住这个边界。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from catfish_tool_bridge.catfish_tools_skill_ops import run_skill  # noqa: E402


def _make_skill(root: Path, name: str, body: str) -> None:
    """在 root 下造一个 department/<name> skill。

    签名故意写成**无参**: `run_skill` 是 `fn(**params)` 展开调用的,
    传 params={} 就等于 `fn()`。

    (顺带记一笔: propose/install 生成的空壳签名是 `render_x(params)` ——
     单个位置参数。`fn(**{})` 缺参数、`fn(**{"a":1})` 又是意外关键字,
     所以那类空壳其实**永远调不起来**, 必然 TypeError → ok=False。
     这是它至今没造成假成功的真正原因。)
    """
    d = root / "department" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nversion: 1.0.0\n---\n\n# {name}\n", encoding="utf-8"
    )
    (d / "script.py").write_text(
        f"def render_{name.replace('-', '_')}():\n{body}\n", encoding="utf-8"
    )


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    (root / "department").mkdir(parents=True)
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(root))
    # 审计写到 tmp, 别污染员工机的 ~/.catfish/skill_audit.jsonl
    monkeypatch.setenv("HOME", str(tmp_path))
    return root


# ─────────────────────────────────────────────────────────────────────
# 1. 显式失败 —— 必须如实报告
# ─────────────────────────────────────────────────────────────────────
def test_skill返ok_false时_run_skill也必须返false(skills_root):
    _make_skill(
        skills_root, "failing-skill",
        '    return {"ok": False, "error": "验证码识别失败超过 max_captcha_retry 次"}',
    )
    r = run_skill({"skill_path": "department/failing-skill", "params": {}})

    assert r["ok"] is False, (
        f"skill 明确返了 ok=False, run_skill 却返 {r['ok']} —— "
        "模型会把失败当成功报给员工"
    )
    assert "验证码识别失败" in json.dumps(r, ensure_ascii=False), (
        "失败原因没透出来, 员工看不到为什么没成"
    )
    assert "已通过" not in r.get("summary", ""), (
        f"summary 还在说「已通过」: {r.get('summary')}"
    )


def test_失败也要写进审计(skills_root, tmp_path):
    _make_skill(skills_root, "failing-skill2", '    return {"ok": False, "error": "boom"}')
    run_skill({"skill_path": "department/failing-skill2", "params": {}})

    audit = tmp_path / ".catfish" / "skill_audit.jsonl"
    assert audit.exists(), "审计文件没写"
    rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]
    mine = [r for r in rows if r.get("skill_path") == "department/failing-skill2"]
    assert mine, "审计里没有这次调用"
    assert mine[-1]["ok"] is False, (
        "审计把失败记成了成功 —— 这正是「141 次 87% 成功」那个假数字的来源"
    )


# ─────────────────────────────────────────────────────────────────────
# 2. 边界 —— 别把判据拨到太窄, 判死能用的 skill
# ─────────────────────────────────────────────────────────────────────
def test_没有ok字段的返回_仍算成功(skills_root):
    """渲染类 skill 常直接返 {"docx": path} —— 没有 ok 字段, 不能判成失败。"""
    _make_skill(
        skills_root, "render-skill",
        '    return {"docx": "/tmp/x.docx"}',
    )
    r = run_skill({"skill_path": "department/render-skill", "params": {}})
    assert r["ok"] is True, "没有 ok 字段被误判成失败 —— 会一次性判死一批渲染类 skill"


def test_返字符串路径_仍算成功(skills_root):
    _make_skill(skills_root, "path-skill", '    return "/tmp/out.docx"')
    r = run_skill({"skill_path": "department/path-skill", "params": {}})
    assert r["ok"] is True, "返路径字符串被误判成失败"


def test_显式ok_true_照旧成功(skills_root):
    _make_skill(skills_root, "good-skill", '    return {"ok": True, "summary": "done"}')
    r = run_skill({"skill_path": "department/good-skill", "params": {}})
    assert r["ok"] is True
    assert "已通过" in r.get("summary", "")
