"""凝固时不许把语义定位降级成位置定位 (8/15)。

# 病历: 一个会让员工以为打了卡、其实点了系统公告的 bug

`skills/department/eis-checkin` 的教学 trace 是这样的:

    3. catfish_browser_find_by_text  {"role":"button","text":"上班打卡"}
    4. catfish_browser_click         {"selector":"div.title"}

凝固管道把第 3 步当成"给 LLM 看的"跳掉, 只把第 4 步的 selector 焊死:

    # skip (LLM-only): catfish_browser_find_by_text
    _call("catfish_browser_click", {"selector": 'div.title'})

## 为什么这是错的

`div.title` 不是"上班打卡那个按钮", 是 **CSS 选择器**, 匹配页面上所有
class=title 的 div。`page.click()` 点的是 DOM 里第一个。5/14 那天点中,
靠的是它恰好排在前面。

同一份 trace 里 find_by_text 其实返了 2 个元素 (div.title 和 div.box-in,
后者文字是 "上班打卡\\n已打卡 08:30")。旁证: eis-checkout 焊死的是
`div.able` —— 同一类按钮两个不同的通用 class 名, 说明都是位置巧合。

## 后果

页面改版后, 如果页面上还有别的 `div.title` (几乎必然 —— "title" 是最通用的
class 名):

    旧模板:  ok=True,  点了 div.title = "系统公告"     ← 员工以为打了卡
    新模板:  ok=False, 没点, 报"页面上找不到 '上班打卡'"

**比沉默更糟**: 沉默至少不会骗人, 这个会返成功。

跟 8/15 一整天遇到的是同一族: 失败没有信号。只是这次信号是反的。

# 这个文件钉什么

  1. 配对只在 click 的 selector **确实来自** 那次 find 时才发生 (判据不能宽)
  2. 生成的脚本在文字找不到时**抛**, 不点任何东西
  3. class 改名但文字还在时**自愈**
  4. 连续两次一模一样的 run_skill 去重, 隔开的不去重
  5. 渲染结果里不许残留 `{{` (模板走 .format, 花括号漏转义会静默吃掉内容)
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from catfish_tool_bridge.skill_freeze_template import (  # noqa: E402
    _SCRIPT_FOOTER,
    _SCRIPT_HEADER,
    _emit_step,
    _format_params_block,
    _infer_params,
    _mark_duplicate_run_skills,
    _pair_text_anchors,
)

#: 5/14 真实 trace 的形状 (session_eis-checkin_20260514_124403_09ae4f.jsonl)。
#: find_by_text 返两个元素 —— 这是"selector 不唯一"最直接的证据, 别改掉。
_BTN = {"selector": "div.title", "text": "上班打卡",
        "center": {"x": 1383, "y": 117}, "in_viewport": True, "score": 56}
_BTN2 = {"selector": "div.box-in", "text": "上班打卡\n已打卡 08:30",
         "center": {"x": 1365, "y": 131}, "in_viewport": True, "score": 30}


def _trace(click_selector: str = "div.title") -> list[dict]:
    return [
        {"seq": 1, "tool": "catfish_run_skill",
         "args": {"skill_path": "department/eis-login", "params": {"username": "chenhb"}}},
        {"seq": 2, "tool": "catfish_run_skill",
         "args": {"skill_path": "department/eis-login", "params": {"username": "chenhb"}}},
        {"seq": 3, "tool": "catfish_browser_find_by_text",
         "args": {"role": "button", "text": "上班打卡"},
         "result": {"type": "ok", "elements": [dict(_BTN), dict(_BTN2)]}},
        {"seq": 4, "tool": "catfish_browser_click", "args": {"selector": click_selector}},
    ]


# ── 1. 配对判据 ─────────────────────────────────────────────


def test_selector来自find时配对():
    tr = _trace()
    assert _pair_text_anchors(tr) == 1
    assert tr[2]["_anchor_folded"] is True
    anchor = tr[3]["_anchor"]
    assert anchor["find_args"] == {"role": "button", "text": "上班打卡"}
    # 锚用**元素自己的文字**, 不是 find 的搜索词 —— 搜索词可能是模糊匹配
    assert anchor["expect_text"] == "上班打卡"


def test_selector不是find返的就不配对():
    """★★ 判据不能宽。

    click 的 selector 如果不在 find 的返回里, 说明它另有来源 (员工自己知道
    DOM 结构)。那种 selector 往往比 find 猜的更稳, 硬配上去是帮倒忙。
    """
    tr = _trace(click_selector="#punchInBtn")
    assert _pair_text_anchors(tr) == 0
    assert "_anchor" not in tr[3]
    assert "_anchor_folded" not in tr[2]


def test_find后面不是click就不配对():
    """17 份真 trace 里 find_by_text → run_skill 出现过 2 次。那不是定位消费。"""
    tr = _trace()[:3] + [{"seq": 4, "tool": "catfish_run_skill",
                          "args": {"skill_path": "department/other", "params": {}}}]
    assert _pair_text_anchors(tr) == 0


def test_find没返元素就不配对():
    tr = _trace()
    tr[2]["result"] = {"type": "ok", "elements": []}
    assert _pair_text_anchors(tr) == 0


# ── 2. run_skill 去重 ───────────────────────────────────────


def test_连续相同的run_skill去重():
    tr = _trace()
    assert _mark_duplicate_run_skills(tr) == 1
    assert "_dup_of_prev" not in tr[0]
    assert tr[1]["_dup_of_prev"] is True


def test_隔开的相同run_skill不去重():
    """★ 「登录 → 干点事 → 再登录」是真实需要, 不能一并砍掉。"""
    tr = [
        {"seq": 1, "tool": "catfish_run_skill", "args": {"skill_path": "a", "params": {}}},
        {"seq": 2, "tool": "catfish_browser_goto", "args": {"url": "http://x"}},
        {"seq": 3, "tool": "catfish_run_skill", "args": {"skill_path": "a", "params": {}}},
    ]
    assert _mark_duplicate_run_skills(tr) == 0


def test_params不同不去重():
    tr = _trace()
    tr[1]["args"] = {"skill_path": "department/eis-login", "params": {"username": "other"}}
    assert _mark_duplicate_run_skills(tr) == 0


# ── 3. 生成的脚本真跑一遍 ───────────────────────────────────


def _render(trace: list[dict]) -> str:
    _pair_text_anchors(trace)
    _mark_duplicate_run_skills(trace)
    body: list[str] = []
    prev = None
    first_goto = False
    for s in trace:
        ifg = not first_goto and s.get("tool") == "catfish_browser_goto"
        if ifg:
            first_goto = True
        lines, prev = _emit_step(s, prev, is_first_goto=ifg)
        body.extend(lines)
    return _SCRIPT_HEADER.format(
        frozen_at=time.strftime("%F %T"), namespace="department", name="eis-checkin",
        name_slug="eis_checkin", fn_name="eis_checkin", description="d",
        trace_path="/x.jsonl", step_count=len(trace),
        params_block=_format_params_block(_infer_params(trace)),
    ) + "\n".join(body) + _SCRIPT_FOOTER


def _run(src: str, page: list[dict], tmp_path: Path):
    """把渲染出来的 script.py 真加载执行, dispatch_native 换成假的。

    假页面语义要跟 Playwright 一致:
      - selector 模式 → CSS 匹配, 命中多个取 DOM 第一个, 一个都没有 → timeout 错
      - coordinates 模式 → 按坐标命中
    否则测出来的东西跟生产对不上。
    """
    calls: list[tuple[str, dict]] = []

    def dispatch_native(tool: str, args: dict):
        calls.append((tool, args))
        if tool == "catfish_run_skill":
            return {"ok": True}
        if tool == "catfish_browser_find_by_text":
            want = args.get("text", "")
            return {"type": "ok",
                    "elements": [dict(e) for e in page if want in e["text"]]}
        if tool == "catfish_browser_click":
            if "coordinates" in args:
                x, _y = args["coordinates"]
                hit = [e for e in page if (e.get("center") or {}).get("x") == x]
                return {"type": "ok", "hit": hit[0]["text"] if hit else "(空白处)"}
            sel = args.get("selector")
            hit = [e for e in page if e["selector"] == sel]
            if not hit:
                return {"type": "error", "error": f"selector {sel} 等不到元素"}
            return {"type": "ok", "hit": hit[0]["text"]}
        return {"ok": True}

    pkg = types.ModuleType("catfish_tool_bridge")
    pkg.__path__ = []                       # type: ignore[attr-defined]
    fake = types.ModuleType("catfish_tool_bridge.catfish_tools")
    fake.dispatch_native = dispatch_native  # type: ignore[attr-defined]
    saved = {k: sys.modules.get(k) for k in
             ("catfish_tool_bridge", "catfish_tool_bridge.catfish_tools")}
    sys.modules["catfish_tool_bridge"] = pkg
    sys.modules["catfish_tool_bridge.catfish_tools"] = fake
    try:
        p = tmp_path / "gen.py"
        p.write_text(src, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"gen_{tmp_path.name}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)        # type: ignore[union-attr]
        return mod.render_eis_checkin(), calls
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_渲染出来能编译且没残留转义符(tmp_path):
    """★★ _SCRIPT_HEADER 走 .format —— 里面的字面量花括号必须写成 `{{`。

    漏了就是 KeyError (那个 8/15 早上让 wiki 生成整天零产出的病)。多写了就是
    `{{` 漏给下游。两头都要钉。
    """
    src = _render(_trace())
    compile(src, "gen.py", "exec")
    assert "{{" not in src, "渲染结果里还有 `{{` —— 转义符漏出来了"
    assert "_resolve_anchor" in src and "_click_element" in src


def test_正常页面用坐标点(tmp_path):
    r, calls = _run(_render(_trace()), [dict(_BTN), dict(_BTN2)], tmp_path)
    assert r["ok"] is True
    clicks = [a for t, a in calls if t == "catfish_browser_click"]
    assert clicks == [{"coordinates": [1383, 117]}], (
        "该走坐标。catfish_browser_click 的 description 明写 coordinates "
        f"'完全绕开 selector 歧义', 而这里实际发的是 {clicks}"
    )
    # 去重生效: eis-login 只跑一次, 不是两次
    assert sum(1 for t, _ in calls if t == "catfish_run_skill") == 1


def test_文字没了要响亮地失败而不是乱点(tmp_path):
    """★★★ 这条就是那个 bug 的复现。

    改回旧模板 (跳过 find_by_text + 焊死 selector) 跑这条:
      页面上还有别的 div.title → 点中它 → ok=True → 员工以为打了卡。
    """
    page = [{"selector": "div.title", "text": "系统公告",
             "center": {"x": 200, "y": 80}, "in_viewport": True, "score": 10}]
    r, calls = _run(_render(_trace()), page, tmp_path)
    assert r["ok"] is False, "文字都没了还返成功 —— 这正是要修的那件事"
    assert r["last_step"] == "resolve"
    assert "上班打卡" in r["error"]
    assert not [a for t, a in calls if t == "catfish_browser_click"], (
        "一个字都没对上就不该点任何东西"
    )


def test_class改名但文字还在能自愈(tmp_path):
    page = [dict(_BTN, selector="button.punch-in")]
    r, calls = _run(_render(_trace()), page, tmp_path)
    assert r["ok"] is True, "文字还在就该找得到 —— 焊死 selector 才会挂"
    assert [a for t, a in calls if t == "catfish_browser_click"] == [
        {"coordinates": [1383, 117]}]


def test_不在视口退回selector模式(tmp_path):
    page = [dict(_BTN, in_viewport=False, center=None)]
    r, calls = _run(_render(_trace()), page, tmp_path)
    assert r["ok"] is True
    assert [a for t, a in calls if t == "catfish_browser_click"] == [
        {"selector": "div.title"}], "没坐标时要退回 selector (它有 auto-waiting)"


# ── 4. 拿真 trace 兜底 ──────────────────────────────────────


def _catfish_home() -> Path:
    """跟 advisor_io.py:25 / a2a_notifications.py:53 同一套: CATFISH_HOME 优先。

    不写死 Path.home() —— 那样这条在任何非员工机器上都只会 skip, 而
    "一直 skip" 跟 "一直绿" 长得一模一样。
    """
    import os
    env = os.environ.get("CATFISH_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".catfish"


_REAL = _catfish_home() / "traces" / "session_eis-checkin_20260514_124403_09ae4f.jsonl"


@pytest.mark.skipif(not _REAL.exists(), reason="真 trace 只在员工机器上")
def test_真的eis_checkin_trace():
    """★ 合成 trace 是我按记忆搭的, 真 trace 才是判据。

    这条在 CI 上会 skip —— 那说明**它没跑过**, 别把 skip 当通过。
    """
    tr = [json.loads(x) for x in _REAL.read_text(encoding="utf-8").splitlines() if x.strip()]
    tr.sort(key=lambda x: x.get("seq", 0))
    assert _pair_text_anchors(tr) == 1
    assert _mark_duplicate_run_skills(tr) == 1
    src = _render(tr)
    compile(src, "gen.py", "exec")
    assert "'div.title'" in src, "fallback_selector 还该留着 (视口外要用)"
    assert '_call("catfish_browser_click", {"selector": \'div.title\'})' not in src, (
        "还在焊死 selector 直点 —— pre-pass 没生效"
    )
