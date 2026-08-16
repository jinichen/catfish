"""Skill freeze 模板 helpers — 抽自 skill_freeze.py (5/20 拆分).

  _is_captcha_fill / _looks_like_password — 凝固时识别敏感数据
  _quote_str / _slugify — 字符串处理
  _emit_step — trace 一步生成 script.py 段
  _infer_params + _format_params_block — 推 render() 签名
  _build_skill_md — SKILL.md 模板

主入口 freeze_skill 在 skill_freeze.py 内部 import 这些 helper.
"""
from __future__ import annotations

import json
import re
import time  # noqa: F401  used by _emit_step's emitted code (legacy reference)
from typing import Any

# captcha selector 白名单 — _is_captcha_fill 用. 5/20 拆分从 skill_freeze.py 移过来.
_CAPTCHA_SELECTORS = ("#captcha", "input[name=captcha]", "input[name=vcode]")

#: 这几个工具的输出是"给 LLM 看的", 本身不改变页面状态, 凝固时不发代码.
#:
#: ⚠ 但 find_by_text / locate 有例外 —— 见 _pair_text_anchors. 它俩的返回值
#:   如果被紧跟着的 click 拿去当 selector 用, 那它就不是"给 LLM 看的",
#:   而是**定位步骤**, 跳掉就把语义定位降级成了位置定位.
_LLM_ONLY_TOOLS = frozenset({
    "catfish_browser_snapshot",
    "catfish_browser_screenshot",
    "catfish_browser_find_by_text",
    "catfish_browser_locate",
})

#: 会返回 elements[] 供后续步骤定位的工具.
_ANCHOR_FINDERS = ("catfish_browser_find_by_text", "catfish_browser_locate")

def _is_captcha_fill(args: dict, prev_captcha_result: dict | None) -> bool:
    """fill 的 selector 是验证码框 + text 跟上一个 recognize_captcha 一致?"""
    sel = (args.get("selector") or "").strip()
    text = (args.get("text") or "").strip()
    if not sel or not text:
        return False
    if not any(s in sel for s in _CAPTCHA_SELECTORS):
        return False
    if prev_captcha_result is None:
        return False
    return prev_captcha_result.get("text", "").strip() == text


def _looks_like_password(args: dict) -> bool:
    """启发: text 字段像明文密码 (8-32 字 + 含数字/符号)? 拒绝凝固保护."""
    text = args.get("text")
    if not isinstance(text, str):
        return False
    # secret_ref 形态明确不是明文
    if "secret_ref" in args and args.get("secret_ref"):
        return False
    # 启发: 8-32 字符 + 含数字 + 含字母 = 像密码
    if 8 <= len(text) <= 32 and re.search(r"\d", text) and re.search(r"[A-Za-z]", text):
        # 但也可能是验证码 / 用户名. 加一个进 selector 判断 — 只有 #pwd 等
        # 密码框 selector 才算密码
        sel = (args.get("selector") or "").lower()
        if any(s in sel for s in ("#pwd", "[name=password]", "[type=password]", "password")):
            return True
    return False


# ─── 模板化主流程 ────────────────────────────────────────────────────


def _quote_str(s: str) -> str:
    """python 字符串字面量, escape 引号."""
    return repr(s)


def _emit_step(
    step: dict,
    prev_captcha_result: dict | None,
    is_first_goto: bool = False,
) -> tuple[list[str], dict | None]:
    """单个 trace step 模板化成 python 行(s).

    Returns: (lines, new_prev_captcha_result). new_prev 用于下一 step 判断
    captcha 数据流.

    v2.1 (5/12): is_first_goto=True 时第一步 goto 加 chrome 状态预检 —
    actual_url 跟 expected url 不一致 (e.g. chrome 已登录 redirect 到 dashboard)
    → 报清楚错误而不是继续 fill 撞 timeout.
    """
    tool = step.get("tool")
    args = step.get("args") or {}
    result = step.get("result") or {}

    # v2.2 (5/12): catfish_run_skill 嵌套调用 (例: eis-checkin 内部调 eis-login)
    if tool == "catfish_run_skill":
        skill_path = args.get("skill_path", "")
        skill_params = args.get("params", {})
        # v2.3 (8/15): 连续两次一模一样的嵌套 skill = 教学时手抖重跑, 不是业务需要.
        # 照抄的代价是每次都多跑一整套 (eis-checkin 里就是多跑一遍完整登录, 含验证码 OCR).
        if step.get("_dup_of_prev"):
            return [
                f'        # deduped: 上一步已经跑过 run_skill:{skill_path} (同 params).',
                f'        #   教学 trace 里连着记了两次 — 凝固时去重. 真要跑两遍的话,',
                f'        #   中间插一步别的 (例 goto), 或者走"再教一次"把意图录清楚.',
            ], prev_captcha_result
        return [
            f'        last_step = "run_skill:{skill_path}"',
            f'        _r = _call("catfish_run_skill", {{"skill_path": {_quote_str(skill_path)}, "params": {skill_params!r}}})',
            f'        if not _r.get("ok"): raise _SkillStepFailure("run_skill:{skill_path}", _r.get("error", "嵌套 skill 失败"))',
        ], prev_captcha_result

    # 跳过类: 输出只给 LLM 看, 不改变页面状态 → 不发代码.
    # find_by_text / locate 被下一步 click 当定位用时, _pair_text_anchors 会给
    # 那个 click 挂上 _anchor, 定位逻辑跟着 click 一起发 (见下面 click 分支).
    if tool in _LLM_ONLY_TOOLS:
        if step.get("_anchor_folded"):
            return [
                f"        # {tool}: 定位逻辑已并进下一步 click (见 _resolve_anchor)"
            ], prev_captcha_result
        return [f"        # skip (LLM-only): {tool}"], prev_captcha_result

    # goto
    if tool == "catfish_browser_goto":
        url = args.get("url") or ""
        wait_until = args.get("wait_until") or "load"
        lines = [
            f'        last_step = "goto:{url}"',
            f'        _r = _call("catfish_browser_goto", {{"url": {_quote_str(url)}, "wait_until": {_quote_str(wait_until)}}})',
            f'        if not _r.get("ok"): raise _SkillStepFailure("goto", _r.get("error", "goto 失败"))',
        ]
        # v2.1 chrome 状态预检 (仅第一步 goto): 检查 actual_url 跟 expected
        # 不一致 → chrome 已登录 redirect 到 dashboard → 报清楚错误.
        if is_first_goto:
            lines.append(
                f'        _actual_url = _r.get("actual_url", "") or _r.get("raw", {{}}).get("actual_url", "")'
            )
            lines.append(
                f'        if _actual_url and {_quote_str(url)} not in _actual_url and not _actual_url.startswith({_quote_str(url[:25])}):'
            )
            lines.append(
                f'            # 期望从 {url} 起步, 实际 redirect 到别的地方 (例 chrome 已登录跳 dashboard)'
            )
            lines.append(
                f'            raise _SkillStepFailure("goto_state_check", f"chrome 状态不符: 期望从 {url} 起步, 实际在 " + _actual_url + ". 建议: 关 Catfish Chrome 重启 (干净未登录态), 或调别的 skill 处理已登录场景.")'
            )
        return lines, prev_captcha_result

    # captcha 识别 — 用 retry loop 代替单次调用
    if tool == "catfish_recognize_captcha":
        selector = args.get("selector") or "#captchaImg"
        hint = args.get("hint") or "alphanumeric_4"
        block = _CAPTCHA_RETRY_BLOCK.format(
            captcha_selector=selector, hint=hint
        )
        # 记 result 给下一步 fill 判断
        new_prev = result if isinstance(result, dict) else None
        return [block], new_prev

    # fill
    if tool == "catfish_browser_fill":
        selector = args.get("selector") or ""
        # secret_ref 路径 — 把 secret_ref 透传给 catfish_browser_fill,
        # 它内部用 secret_resolver.resolve_secret(ref) 把 keychain://xxx 解析成明文
        # 喂给 page.fill, 明文不进 LLM 上下文也不进 script 字面量
        if args.get("secret_ref"):
            sref = args["secret_ref"]
            return [
                f'        last_step = "fill_secret:{selector}"',
                f'        _r = _call("catfish_browser_fill", {{"selector": {_quote_str(selector)}, "secret_ref": password_ref}})',
                f'        if not _r.get("ok"): raise _SkillStepFailure("fill_secret", _r.get("error", "fill 失败"))',
            ], prev_captcha_result
        # 明文密码 — 拒绝
        if _looks_like_password(args):
            return [
                f'        # SECURITY: refused — args 含明文密码 (selector={selector}).',
                f'        raise RuntimeError("refused to bake plaintext password")',
            ], prev_captcha_result
        # captcha 字段 — 用上一步识别结果
        if _is_captcha_fill(args, prev_captcha_result):
            return [
                f'        last_step = "fill_captcha:{selector}"',
                f'        _r = _call("catfish_browser_fill", {{"selector": {_quote_str(selector)}, "text": captcha_text}})',
                f'        if not _r.get("ok"): raise _SkillStepFailure("fill_captcha", _r.get("error", "fill 失败"))',
            ], prev_captcha_result
        # 普通 text fill (username 走这条, 把 text 抽成参数 username 而不是 hard-code)
        text = args.get("text", "")
        sel_l = selector.lower()
        is_username = any(c in sel_l for c in ("#name", "[name=username]", "username"))
        text_repr = "username" if is_username else _quote_str(text)
        return [
            f'        last_step = "fill:{selector}"',
            f'        _r = _call("catfish_browser_fill", {{"selector": {_quote_str(selector)}, "text": {text_repr}}})',
            f'        if not _r.get("ok"): raise _SkillStepFailure("fill", _r.get("error", "fill 失败"))',
        ], prev_captcha_result

    # click
    if tool == "catfish_browser_click":
        coords = args.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) == 2:
            x, y = coords
            return [
                f'        last_step = "click_coords:{x},{y}"',
                f'        _r = _call("catfish_browser_click", {{"coordinates": [{x}, {y}]}})',
                f'        if not _r.get("ok"): raise _SkillStepFailure("click_coords", _r.get("error", "click 失败"))',
            ], prev_captcha_result
        selector = args.get("selector") or ""

        # v2.3 (8/15): 这个 click 的 selector 来自上一步 find_by_text → 运行时重新定位.
        #
        # 老写法把当天那个 selector 焊死。eis-checkin 焊的是 `div.title` ——
        # 那是 CSS 选择器, 匹配页面上**所有** class=title 的 div, 不是"上班打卡
        # 那个按钮"。5/14 点中是因为它恰好排在 DOM 前面。
        # (同一份 trace 里 find_by_text 其实返了 2 个元素: div.title 和 div.box-in。)
        #
        # 页面一改版: 轻则点不中报错, 重则**点中别的按钮还返 ok=True** —— 比沉默更糟。
        anchor = step.get("_anchor")
        if anchor:
            expect = anchor.get("expect_text") or ""
            return [
                f'        last_step = "click:{expect or selector}"',
                f'        _el = _resolve_anchor({anchor.get("find_args", {})!r}, {_quote_str(expect)})',
                f'        _r = _click_element(_el, fallback_selector={_quote_str(selector)})',
                f'        if not _r.get("ok"): raise _SkillStepFailure("click", _r.get("error", "click 失败"))',
            ], prev_captcha_result

        return [
            f'        last_step = "click:{selector}"',
            f'        _r = _call("catfish_browser_click", {{"selector": {_quote_str(selector)}}})',
            f'        if not _r.get("ok"): raise _SkillStepFailure("click", _r.get("error", "click 失败"))',
        ], prev_captcha_result

    return [f'        # unhandled tool: {tool}'], prev_captcha_result


# ─── v2.3 (8/15) 凝固前的配对 pre-pass ────────────────────────────────
#
# 为什么要有这一步 (8/15 查 EIS 打卡 skill 时发现):
#
#   教学 trace:
#       3. catfish_browser_find_by_text  {"role":"button","text":"上班打卡"}
#       4. catfish_browser_click         {"selector":"div.title"}
#
#   凝固出来:
#       # skip (LLM-only): catfish_browser_find_by_text
#       _call("catfish_browser_click", {"selector": 'div.title'})
#
#   语义定位 ("找到写着上班打卡的按钮") 被降级成了位置定位 ("点第一个 div.title")。
#   而 `div.title` 是 find_by_text 当天**返回**的结果, 不是稳定标识。
#   旁证: eis-checkout 焊的是 `div.able` —— 同类按钮两个不同的通用 class 名,
#   说明这俩 selector 都是位置巧合。
#
# 17 份 trace 里 find_by_text → click 出现 6 次, → fill 0 次。
# 所以只配 click 这一条, 不写没人跑的路径。


def _pair_text_anchors(trace: list[dict]) -> int:
    """标出「find_by_text 定位 → click 用它的 selector」这种配对。

    就地给 trace 打标 (跟 skill_freeze.py 里的 trace.sort 一样是原地操作):
      - 消费方 click  → step["_anchor"] = {find_args, expect_text}
      - 定位方 find   → step["_anchor_folded"] = True

    配对判据: click 的 selector **确实出现在** find 返回的 elements 里。
    只要 selector 对不上就不配 —— 那说明 click 的目标另有来源 (员工自己知道
    DOM), 硬配上去会把一个本来稳定的 selector 换成猜的。

    Returns: 配上了几对。
    """
    paired = 0
    for i, step in enumerate(trace):
        if step.get("tool") not in _ANCHOR_FINDERS:
            continue
        result = step.get("result") or {}
        els = [e for e in (result.get("elements") or []) if isinstance(e, dict)]
        if not els:
            continue
        by_sel = {e.get("selector"): e for e in els if e.get("selector")}

        # 往后找第一个"会改变页面状态"的步骤。中间的 snapshot / 又一次 find
        # 都跳过 —— 它们不消费定位结果。
        for nxt in trace[i + 1:]:
            if nxt.get("tool") in _LLM_ONLY_TOOLS:
                continue
            if nxt.get("tool") != "catfish_browser_click":
                break                      # 例: find → run_skill, 不是定位消费方
            sel = (nxt.get("args") or {}).get("selector")
            if not sel or sel not in by_sel:
                break                      # selector 不是这次 find 返的 → 不配
            nxt["_anchor"] = {
                "find_args": step.get("args") or {},
                # 用**元素自己的文字**当锚, 不用 find 的搜索词 —— 搜索词可能是
                # 模糊匹配 (exact=false), 元素文字才是运行时要比对的那个东西。
                "expect_text": by_sel[sel].get("text") or "",
            }
            step["_anchor_folded"] = True
            paired += 1
            break
    return paired


def _mark_duplicate_run_skills(trace: list[dict]) -> int:
    """标出连续两次一模一样的 catfish_run_skill (教学时手抖重跑)。

    只认**紧挨着**且 args 完全相同的。中间隔了任何一步都不算 —— 那可能是
    "登录 → 干点事 → 再登录" 这种真实需要。

    Returns: 标了几个。
    """
    marked = 0
    prev_key: str | None = None
    for step in trace:
        if step.get("tool") != "catfish_run_skill":
            prev_key = None
            continue
        key = json.dumps(step.get("args") or {}, sort_keys=True, ensure_ascii=False)
        if key == prev_key:
            step["_dup_of_prev"] = True
            marked += 1
        prev_key = key
    return marked


def _infer_params(trace: list[dict]) -> list[tuple[str, str, str]]:
    """从 trace 推参数列表.

    Returns: [(name, type, default), ...]
    """
    params: list[tuple[str, str, str]] = []
    seen_secret_refs: set[str] = set()
    # username 启发: 第一个非密码 / 非验证码 fill 的 text → 当成 username
    has_username = False
    has_captcha = False
    for step in trace:
        if step.get("tool") != "catfish_browser_fill":
            if step.get("tool") == "catfish_recognize_captcha":
                has_captcha = True
            continue
        args = step.get("args") or {}
        if args.get("secret_ref"):
            sref = args.get("secret_ref", "")
            if sref not in seen_secret_refs:
                seen_secret_refs.add(sref)
                params.append(
                    ("password_ref", "str", _quote_str(sref))
                )
        elif not has_username:
            sel = (args.get("selector") or "").lower()
            text = args.get("text", "")
            if not any(c in sel for c in ("captcha", "vcode", "pwd", "password")):
                # 当 username 参数
                params.append(("username", "str", _quote_str(text)))
                has_username = True
    if has_captcha:
        params.append(("max_captcha_retry", "int", "3"))
    return params


def _format_params_block(params: list[tuple[str, str, str]]) -> str:
    """params → python 参数声明 block."""
    if not params:
        return ""
    lines = []
    for name, type_, default in params:
        lines.append(f"    {name}: {type_} = {default},")
    return "\n".join(lines) + "\n"


# ─── SKILL.md 生成 ──────────────────────────────────────────────────


_SKILL_MD_TEMPLATE = '''---
name: {name}
version: "0.1.0-frozen"
deprecated: false
description: |-
  ⭐ {description}

  ⚙️ 由 catfish_freeze_skill 自动凝固 ({frozen_at}, BL-MM9-FREEZE).
  源 trace: {trace_path} (steps {step_start}-{step_end}, ok={ok_count}/{total_count}).
  **不要手改 script.py** — 业务流程变了走"再教一次"路径让管道重新凝固.

  调用入口: `render_{fn_name}({param_names})` (script.py).
---

# {name} — {description}

> 凝固于 {frozen_at}. 教学→凝固→复用闭环 (BL-MM9-FREEZE).

## 怎么调

```
catfish_run_skill(
  skill_path="{namespace}/{name}",
  params={{{example_params}}}
)
```

## 凝固时的 {step_count} 步教学 trace

{trace_summary}

## 参数

{params_table}

## 修改方式

**不要直接改 script.py**. 业务流程变化 (页面改版 / 加新步骤) → 走"再教一次":

  1. 员工: "再教一次 {name}, 改的地方是 ..."
  2. LLM agent 跑新流程, trace_recorder 自动记
  3. 员工: "凝固成 v2"
  4. `catfish_freeze_skill(name="{name}", overwrite=true)`

凝固管道会重新生成 script.py + SKILL.md, 覆盖本份.

## 已知限制 (5/12 MVP)

- 凝固只看顺序, 不解 trace 里的"如果 X 则 Y"分支. 业务有分支 → 拆成多个 skill.
- 等待逻辑: 每个 click / goto 后默认 networkidle wait, 5s 超时.
- captcha retry 是自动插入 (识别 confidence < 0.6 时刷图重识), 不靠 trace.
- secret_ref 必须在教学时就用 keychain://, 不接受明文密码凝固.
'''


def _build_skill_md(
    name: str,
    namespace: str,
    description: str,
    fn_name: str,
    trace: list[dict],
    trace_path: str,
    step_start: int,
    step_end: int,
    ok_count: int,
    total_count: int,
    params: list[tuple[str, str, str]],
) -> str:
    """渲染 SKILL.md."""
    trace_summary_lines = []
    for i, step in enumerate(trace, 1):
        tool = step.get("tool")
        args = step.get("args") or {}
        sel = args.get("selector") or args.get("url") or ""
        line = f"  {i}. `{tool}({sel})`" if sel else f"  {i}. `{tool}(...)`"
        # v2.3 (8/15): 凝固时做过手脚的步骤要在文档里说清楚, 否则 trace 跟
        # script.py 对不上, 下一个看的人会以为哪边错了.
        if step.get("_anchor_folded"):
            line += " — 定位逻辑并进了下一步 click (运行时重新按文字找)"
        elif step.get("_anchor"):
            expect = (step["_anchor"].get("expect_text") or "").strip()
            line += f" — 运行时按文字 {expect!r} 重新定位, 不焊死 `{sel}`"
        elif step.get("_dup_of_prev"):
            line += " — **跟上一步完全相同, 已去重**"
        trace_summary_lines.append(line)
    params_table_rows = []
    param_names_inline = []
    example_params_parts = []
    for n, t, d in params:
        params_table_rows.append(f"| `{n}` | {t} | {d} |")
        param_names_inline.append(n)
        example_params_parts.append(f'"{n}": {d}')
    if params_table_rows:
        params_table = "| 参数 | 类型 | 默认 |\n|---|---|---|\n" + "\n".join(params_table_rows)
    else:
        params_table = "_(无参数)_"
    return _SKILL_MD_TEMPLATE.format(
        name=name,
        namespace=namespace,
        description=description,
        step_count=len(trace),
        frozen_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        trace_path=trace_path,
        step_start=step_start,
        step_end=step_end,
        ok_count=ok_count,
        total_count=total_count,
        fn_name=fn_name,
        trace_summary="\n".join(trace_summary_lines),
        params_table=params_table,
        param_names=", ".join(param_names_inline),
        example_params=", ".join(example_params_parts),
    )


# ─── 主入口 ──────────────────────────────────────────────────────────


def _slugify(s: str) -> str:
    """name → python identifier safe."""
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_") or "skill"


_SCRIPT_HEADER = '''"""Auto-generated by catfish_freeze_skill ({frozen_at}).

skill:        {namespace}/{name}
description:  {description}
source trace: {trace_path} ({step_count} steps)
frozen by:    BL-MM9-FREEZE (5/12 鸿波拍板, 教学→凝固管道)

# 这是凝固出来的 skill, 不是手写的

如果你 (员工 / LLM) 看 EIS / 业务流程的细节, 不要改 script.py — 走"再教一次"
路径让 catfish_freeze_skill 重新凝固. 手改容易跟下次凝固冲突.

# 执行模型

script 通过 catfish_tool_bridge.dispatch_native 调用原生 tool. 不自己开
Playwright — 复用 catfish_browser_* 内部已经写好的 CDP connect / secret_ref
resolve / SSRF deny / 等基础设施. 每 step 一次 dispatch.
"""
from __future__ import annotations

import logging
import time as _time
from typing import Any

logger = logging.getLogger("catfish.skill.{name_slug}")


def _call(tool_name: str, args: dict[str, Any], retries: int = 2, retry_delay: float = 2.5) -> dict[str, Any]:
    """走 catfish dispatch 调原生 tool. 返 result dict.

    BL-MM9-FREEZE-v2 schema 兼容 (5/12 鸿波撞坑发现): browser_* 工具用老 schema
    {{"type": "ok/error", "error": "..."}}, 没 `ok` 字段. freeze/recognize 等用
    新 schema {{"ok": true/false, ...}}. _call 同时认两种, 不让 schema 不匹配让
    script.py 把成功当失败 raise.

    cold-start fix: 失败时 sleep 2.5s 重试最多 2 次, 撑过 chrome / 模型 cold.
    """
    from catfish_tool_bridge.catfish_tools import dispatch_native  # noqa: PLC0415
    last_result: dict[str, Any] = {{"ok": False, "error": "not attempted"}}
    for attempt in range(retries + 1):
        try:
            r = dispatch_native(tool_name, args)
            if not isinstance(r, dict):
                return {{"ok": True, "raw": r}}
            # schema 兼容: 失败判定优先级:
            #  1) r["type"] == "error" → 失败 (browser_* 老 schema)
            #  2) r["ok"] is False → 失败 (新 schema)
            #  3) 否则 → 成功
            is_fail = (r.get("type") == "error") or (r.get("ok") is False)
            if not is_fail:
                # 成功 → 统一加 ok=True 给上游 script.py 用
                out = dict(r)
                out.setdefault("ok", True)
                return out
            last_result = dict(r)
            last_result.setdefault("ok", False)
            if not last_result.get("error"):
                last_result["error"] = f"{{tool_name}} 失败 (无 error 详情)"
        except Exception as e:
            last_result = {{"ok": False, "error": f"{{tool_name}} 抛异常: {{type(e).__name__}}: {{e!r}}"}}
        if attempt < retries:
            logger.info("_call retry %d/%d for %s: %s", attempt + 1, retries, tool_name, last_result.get("error", "?"))
            _time.sleep(retry_delay)
    return last_result


def _resolve_anchor(find_args: dict[str, Any], expect_text: str) -> dict[str, Any]:
    """运行时重新按文字定位, 返当前页面上那个元素.

    为什么不直接用凝固时那个 selector (BL-MM9-FREEZE-v2.3, 8/15):

      find_by_text 返的 selector 是**当时**匹配到的那个元素的 CSS 表达式,
      不保证唯一。eis-checkin 焊死的 `div.title` 匹配页面上所有 class=title
      的 div —— 点中"上班打卡"靠的是它恰好排在 DOM 前面。

      焊死之后, 页面改版的表现是: 轻则点不中报错, 重则**点中别的按钮还返
      ok=True**。后者比报错糟得多 —— 没有任何信号, 员工以为打了卡。

    所以这里每次都重新找, 并且**先核对文字**: 找不到期望文字就抛, 绝不退化成
    "点点看"。这是把一次静默的错点换成一次响亮的失败。
    """
    r = _call("catfish_browser_find_by_text", find_args)
    if not r.get("ok"):
        raise _SkillStepFailure("resolve", f"定位 {{expect_text!r}} 失败: " + str(r.get("error", "?")))

    els = [e for e in (r.get("elements") or []) if isinstance(e, dict)]
    _norm = lambda s: "".join((s or "").split())  # noqa: E731  文字带换行, 例 "上班打卡\\n已打卡 08:30"
    want = _norm(expect_text)
    exact = [e for e in els if _norm(e.get("text")) == want]
    loose = [e for e in els if want and want in _norm(e.get("text"))]
    cand = exact or loose

    if not cand:
        seen = ", ".join(repr((e.get("text") or "")[:20]) for e in els[:5]) or "(一个都没返)"
        raise _SkillStepFailure(
            "resolve",
            f"页面上找不到文字 {{expect_text!r}}。find_by_text 返了 {{len(els)}} 个元素: {{seen}}。"
            "页面多半改版了 — 走\\"再教一次\\"重新凝固, 不要手改 script.py。",
        )

    best = max(cand, key=lambda e: e.get("score") or 0)
    if len(exact) > 1:
        logger.warning(
            "resolve %r: %d 个元素文字完全一致, 取 score 最高的 (selector=%r). "
            "页面上有多个同名控件时这里可能挑错 — 教学时用更具体的文字。",
            expect_text, len(exact), best.get("selector"),
        )
    return best


def _click_element(el: dict[str, Any], fallback_selector: str = "") -> dict[str, Any]:
    """点 _resolve_anchor 返回的元素。

    优先坐标: catfish_browser_click 的 description 明写 coordinates "完全绕开
    selector 歧义"。既然 find_by_text 已经给了这个元素的 center, 就没必要再把
    一个可能匹配多个节点的 CSS 表达式丢回去。

    不在视口 / 没坐标 → 退回 selector 模式 (它有 Playwright auto-waiting)。
    """
    center = el.get("center") or {{}}
    x, y = center.get("x"), center.get("y")
    if el.get("in_viewport") and isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return _call("catfish_browser_click", {{"coordinates": [int(x), int(y)]}})
    sel = el.get("selector") or fallback_selector
    if not sel:
        return {{"ok": False, "error": "元素既没坐标也没 selector, 无从点起"}}
    logger.info("_click_element: 不在视口, 退回 selector 模式 (%r)", sel)
    return _call("catfish_browser_click", {{"selector": sel}})


def render_{fn_name}(
{params_block}    **_kwargs: Any,
) -> dict[str, Any]:
    """跑凝固后的 {namespace}/{name} 流程.

    Returns: {{ok, ...}} 业务结果. 失败时 ok=false + error + last_step.
    """
    _started = _time.time()
    captcha_attempts = 0
    captcha_text = None
    last_step = None
    try:
'''

_SCRIPT_FOOTER = '''
        return {
            "ok": True,
            "captcha_attempts": captcha_attempts,
            "duration_ms": int((_time.time() - _started) * 1000),
        }
    except _SkillStepFailure as fail:
        logger.warning("skill step %s 失败: %s", fail.step, fail.message)
        return {
            "ok": False,
            "error": fail.message,
            "last_step": fail.step,
            "captcha_attempts": captcha_attempts,
            "duration_ms": int((_time.time() - _started) * 1000),
        }
    except Exception as e:
        logger.exception("skill 异常")
        return {
            "ok": False,
            "error": repr(e),
            "last_step": last_step,
            "captcha_attempts": captcha_attempts,
            "duration_ms": int((_time.time() - _started) * 1000),
        }


class _SkillStepFailure(Exception):
    def __init__(self, step: str, message: str) -> None:
        self.step = step
        self.message = message
        super().__init__(f"{step}: {message}")
'''


_CAPTCHA_RETRY_BLOCK = '''
        # captcha retry loop (freeze 引擎自动插入: 识别失败时刷图重识)
        last_step = "captcha_recognize"
        captcha_text = None
        for _attempt in range(max_captcha_retry):
            captcha_attempts += 1
            _r = _call("catfish_recognize_captcha", {{"selector": {captcha_selector!r}, "hint": {hint!r}}})
            if _r.get("ok") and _r.get("confidence", 0) >= 0.6:
                captcha_text = _r.get("text", "")
                break
            # 刷新验证码图
            _call("catfish_browser_click", {{"selector": {captcha_selector!r}}})
            _time.sleep(0.5)
        if not captcha_text:
            raise _SkillStepFailure("captcha_recognize", "验证码识别失败超过 max_captcha_retry 次")
'''
