"""task_assessment 的字段, 后端必须真的产得出来 —— 前端夹具不许凭空造值。

# 病历 (8/13)

`skill_guard_fired` 在后端是 `sg_fired = False` **写死的字面量**, 5/26 砍
skills_loader 时留下的。而前端把它当判据:

    app.py           sg_fired = False
      → task_assessment.skill_guard_fired = false   (每一条, 无例外)
      → promiseCheck.ts:79   if (!skill_guard_fired) return 不报警
      → runOneRound.ts:189   check.is_promise_only 永不为真
      → ChatMessage.tsx:413  ⚠ 嘴炮 badge **一次都没显示过**

嘴炮检测 (BL-TASK-ASSESS, 4/29 demo 反复翻车专门做的) 就这么死了 79 天。

# 为什么没人发现

前端测试**全绿**。promiseCheck.test.ts 的夹具写着:

    skill_guard_fired: true      ← 后端永远产不出这个值

夹具自己造了一个现实中不存在的输入, 于是测的是"如果后端给 true 会怎样",
而不是"后端实际给什么"。这跟今天撞的另外两次是同一个形状:

  · box_parser 夹具缺失 → 10 条测试一直 error, 没人看
  · "文案逐字一致"的核对只比了 args[0] —— 我自己挑出来要保留的那部分

共同点: **判据来自我想验的东西, 不是来自真实产出。**

# 这个文件钉什么

从 app.py 源码里把 task_assessment 那个 dict 的字面量取出来, 逐字段问:
这个值是写死的常量吗? 是的话, 前端就不该拿它当分支判据 —— 要么它是真常量
(那前端的分支是死的), 要么它该算真值。

不查前端文件 (跨语言解析脆), 只钉后端这一侧: **不许再出现写死的布尔字段**。
"""
from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "src/catfish_gateway/app.py"
_SRC = _APP.read_text(encoding="utf-8")


def _task_assessment_dict() -> ast.Dict:
    """定位 `task_assessment = {...}` 那个字面量。"""
    for node in ast.walk(ast.parse(_SRC)):
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if node.targets[0].id != "task_assessment":
            continue
        if isinstance(node.value, ast.Dict):
            return node.value
    raise AssertionError(
        "找不到 task_assessment = {...} —— 结构变了, 这个测试的定位方式要更新"
    )


def _enclosing_function(lineno: int) -> ast.AST:
    best = None
    for n in ast.walk(ast.parse(_SRC)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                n.lineno <= lineno <= (n.end_lineno or 0):
            if best is None or (n.end_lineno - n.lineno) < (best.end_lineno - best.lineno):
                best = n
    assert best is not None
    return best


def _field_value_node(field: str) -> ast.AST:
    d = _task_assessment_dict()
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value == field:
            return v
    raise AssertionError(f"task_assessment 里没有 {field} 字段了 —— 契约变了")


def _assignments_to(name: str, fn: ast.AST) -> list[ast.AST]:
    """函数体里所有给 `name` 赋的值。

    ⚠ 8/13 第一版栽在这: 只看 `task_assessment = {...}` 那个 dict 字面量, 而
       写死发生在**上一行的独立赋值**里 (`sg_fired = False`), dict 里放的是
       `ast.Name`。于是"有没有写死"这个问题被问到了一个永远答"没有"的地方 ——
       把 sg_fired 改回 False, 测试照样绿。

       同一轮里第二条也栽了: 拿"函数源码里有没有 body.get('tools')"当判据, 而
       外层函数有 426 行, 那个片段别处本来就有 → 恒真。

       判据必须落到**那个具体的名字是怎么来的**, 不是"附近有没有出现过某个词"。
    """
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    out.append(n.value)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            t = n.target
            if isinstance(t, ast.Name) and t.id == name and n.value is not None:
                out.append(n.value)
    return out


def _is_effectively_constant(node: ast.AST, fn: ast.AST) -> bool:
    """这个字段值实际上是不是一个恒定的布尔?

    ⚠ 判据是「**所有赋值都是同一个**布尔字面量」, 不是「赋的都是字面量」。

       第一版写成后者, 当场误伤了两个正常字段:

           cum_has_tool_call                     ← False, 然后条件里 True, True
           ever_called_catfish_run_skill_in_session ← False, 然后 True

       这是最常见的旗标写法 (先置默认, 命中条件再翻) —— 赋的全是字面量, 但值
       随请求而变, 完全正常。真正的写死是"从头到尾只赋过一次, 且是常量",
       就像 sg_fired = False 那样。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return True
    if not isinstance(node, ast.Name):
        return False
    vals = _assignments_to(node.id, fn)
    if not vals:
        return False
    consts = [
        a.value for a in vals
        if isinstance(a, ast.Constant) and isinstance(a.value, bool)
    ]
    if len(consts) != len(vals):
        return False          # 有非字面量来源 → 是算出来的
    return len(set(consts)) == 1   # 只可能取一个值 → 写死


def test_no_hardcoded_boolean_fields():
    """★★★ task_assessment 里不许有写死的布尔值。

    写死 = 前端拿它当分支判据时, 那条分支要么恒真要么恒假 —— 而两边的测试
    都会绿, 因为各自造各自的夹具。skill_guard_fired 就是这么死的。

    真要有常量字段, 说明它已经不承载信息了, 应该从 payload 里删掉,
    而不是继续发一个永远不变的值给前端做判断。
    """
    d = _task_assessment_dict()
    fn = _enclosing_function(d.lineno)
    hardcoded = [
        k.value for k, v in zip(d.keys, d.values)
        if isinstance(k, ast.Constant) and _is_effectively_constant(v, fn)
    ]
    assert not hardcoded, (
        f"task_assessment 里有写死的布尔字段: {hardcoded}。\n"
        "前端拿它当判据的话那条分支是死的, 而两边测试都会绿 —— "
        "skill_guard_fired 就是这样让嘴炮检测死了 79 天。\n"
        "要么算真值, 要么从 payload 里删掉。"
    )


def test_skill_guard_fired_is_computed_from_the_request():
    """★★ skill_guard_fired 必须从**本轮请求**算出来, 不能是常量。

    现在的语义: gateway 这一轮到底有没有把工具递给模型。
    这正是嘴炮的定义 —— 给了工具, 一个没调, 还说自己做完了。

    判据取赋给它的那个名字, 再回到函数体里看那个名字是怎么来的。
    """
    val = _field_value_node("skill_guard_fired")
    assert isinstance(val, ast.Name), (
        "skill_guard_fired 直接写了字面量 —— 必须是算出来的变量"
    )

    fn = _enclosing_function(_task_assessment_dict().lineno)

    # 顺着变量名往回追: sg_fired ← _tools_offered ← body.get("tools")
    # 只追一层间接, 够用且不会追飞
    chain = [val.id]
    seen = set()
    src_parts = []
    while chain:
        name = chain.pop()
        if name in seen:
            continue
        seen.add(name)
        for a in _assignments_to(name, fn):
            src_parts.append(ast.get_source_segment(_SRC, a) or "")
            if isinstance(a, ast.Name):
                chain.append(a.id)
    # 那个名字后续被条件改写的地方也算 (if tool_choice == "none": _tools_offered = False)
    for n in ast.walk(fn):
        if isinstance(n, ast.If):
            assigns_seen = any(
                isinstance(s, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in seen for t in s.targets
                )
                for s in ast.walk(n)
            )
            if assigns_seen:
                src_parts.append(ast.get_source_segment(_SRC, n) or "")

    derivation = "\n".join(src_parts)
    assert derivation.strip(), f"追不到 {val.id} 的赋值 —— 判据来源不明"
    assert 'body.get("tools")' in derivation, (
        f"skill_guard_fired ← {val.id}, 但它的来源里没有 body['tools']:\n{derivation}\n"
        "它应该反映'本轮 gateway 有没有把工具递给模型'"
    )
    assert "tool_choice" in derivation, (
        f"没排除 tool_choice='none':\n{derivation}\n"
        "调用方明确说了'这轮别调工具'时, 模型不调是遵命, 不该被判成嘴炮"
    )


def test_frontend_fixture_value_is_reachable():
    """★★★ 前端夹具用的值, 后端必须真的产得出来。

    promiseCheck.test.ts 的夹具写 `skill_guard_fired: true`。在改之前后端
    **永远给 false** —— 夹具造了一个现实里不存在的输入, 测试于是测了一件
    不会发生的事, 全绿了 79 天。

    这条直接读前端夹具文件 (存在才读), 把它断言的值跟后端的可达值对一下。
    找不到前端仓 (只跑 gateway 的 CI) 就 skip, 不制造假红。
    """
    # app.py 在 <repo>/central/llm-gateway/src/catfish_gateway/ 下 → 上溯 4 层到仓根
    # (第一版写了 parents[3], 停在 central/, 于是这条测试一直 skip —— 一条永远
    #  skip 的测试跟没有是一回事, 而且它伪装成"跑过了")
    fixture = (
        _APP.resolve().parents[4]
        / "edge/companion-app/src/lib/promiseCheck.test.ts"
    )
    if not fixture.exists():
        import pytest
        pytest.skip(f"没有 companion-app 源码 ({fixture}), 跳过跨端契约检查")

    text = fixture.read_text(encoding="utf-8")
    assert "skill_guard_fired: true" in text, (
        "前端夹具不再有 skill_guard_fired: true —— 契约变了, 这条测试要跟着更新"
    )
    # 后端能不能产出 true? 用跟上面同一套"是否恒定"的判据 ——
    # 浅判 isinstance(v, ast.Constant) 不够: 写死发生在 `sg_fired = False`
    # 那一行, dict 里放的是变量名, 浅判会漏掉 (第一版就是这么漏的)。
    d = _task_assessment_dict()
    fn = _enclosing_function(d.lineno)
    assert not _is_effectively_constant(
        _field_value_node("skill_guard_fired"), fn
    ), (
        "前端夹具断言 skill_guard_fired: true, 但后端这个字段恒定 —— "
        "夹具在测一件永远不会发生的事 (这就是 5/26 → 8/13 那 79 天)"
    )
