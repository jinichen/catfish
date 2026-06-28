"""skill_verify — P3.5.128 E1 (6/26 鸿波拍板, 借鉴 Skill-DisCo arxiv 2606.26669
Compilation phase 的 "verify before include in callable library" gate).

# 为什么不是论文 holdout split

论文 Compilation 阶段把 candidate skill 在 held-out 任务上跑 verify, 只有通过
才入 callable library. 这要求同 skill 有多条独立 trace 可 split — 鲶鱼现状每
skill 凝固时只 1 条 trace, 不满足. 真 holdout 留后续 P3.5.13x 收 trace 后做.

# 第一阶段 verify (β — AST + 工具白名单)

零副作用, 不实际执行 script.py, 用 ast 解析做 4 个检查:

  1. 语法合法 (`ast.parse`)
  2. 存在 `def render_<fn_name>(...)` 函数 (freeze 模板的唯一入口)
  3. 该函数所有 `_call("...", ...)` 调用第一参数必须是 Constant str
  4. 第一参数值必须在 trace_recorder.RECORDED_TOOLS 白名单

失败拒入 — script.py 重命名为 `.unverified-<ts>`, SKILL.md 不写. 这样:
  - 后续 catfish_run_skill 不会撞坏的 skill (因为 SKILL.md 没 ship)
  - 员工可以 vim .unverified 看到底哪行错了, 再决定"再教一次" 还是手修

# 不做的事

  - 不实际 import 或 exec script.py — 一开 import 就触发 catfish_browser_*
    side effect, 不安全
  - 不检查 _call 第二参数 args dict 形状 — 这个 _emit_step 生成时已保证, 二次
    校验属过度
  - 不检查 secret_ref 模式 — 那是 _emit_step 拒明文密码已经做过的事
"""
from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class VerifyResult:
    ok: bool
    errors: list[str]
    # 给员工调试用 — 知道认到几个 _call 节点
    call_count: int
    found_render_fn: bool


def verify_frozen_script(
    script_py_text: str,
    allowed_tools: frozenset[str],
    expected_fn_name: str,
) -> VerifyResult:
    """对 catfish_freeze_skill 凝出来的 script.py 做静态 verify gate.

    Args:
        script_py_text: script.py 文件全文
        allowed_tools: 允许在 _call(...) 第一参数出现的 tool 名集合.
            ↪ 调用方应传 trace_recorder.RECORDED_TOOLS.
        expected_fn_name: 期望 render 函数名 (不带 'render_' 前缀, 例 'eis_login').
            ↪ 调用方应传 _slugify(skill_name).

    Returns:
        VerifyResult — ok=True 表示通过可入库, errors 全空; ok=False 时 errors
        含所有问题点 (一次性返完, 不 short-circuit, 让员工一次看全).
    """
    errors: list[str] = []

    # 1) 语法合法
    try:
        tree = ast.parse(script_py_text)
    except SyntaxError as e:
        return VerifyResult(
            ok=False,
            errors=[f"语法错: {e.msg} (line {e.lineno})"],
            call_count=0,
            found_render_fn=False,
        )

    # 2) 找 render_<fn_name>(...) 函数
    expected_full = f"render_{expected_fn_name}"
    render_fn: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == expected_full:
            render_fn = node
            break
    if render_fn is None:
        return VerifyResult(
            ok=False,
            errors=[f"找不到入口函数 def {expected_full}(...)"],
            call_count=0,
            found_render_fn=False,
        )

    # 3) 遍历 render_fn 内部所有 _call("tool", {...}) 调用
    call_count = 0
    for node in ast.walk(render_fn):
        if not isinstance(node, ast.Call):
            continue
        # 只关心 _call(...) 形式
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "_call":
            continue
        call_count += 1

        if not node.args:
            errors.append(f"_call() at line {node.lineno} 缺第一参数")
            continue
        first = node.args[0]
        # 4) 第一参数必须是 Constant str
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            errors.append(
                f"_call() at line {node.lineno} 第一参数必须是字符串常量, "
                f"实际 {type(first).__name__}"
            )
            continue
        tool_name = first.value
        # 5) tool 必须在白名单
        if tool_name not in allowed_tools:
            errors.append(
                f"_call() at line {node.lineno} 用了未知工具 "
                f"{tool_name!r} — 不在 trace_recorder.RECORDED_TOOLS 白名单"
            )

    if call_count == 0:
        # render_fn 没调任何 _call — 空壳, 凝固模板没出有效 step. 失败.
        errors.append(
            f"render_{expected_fn_name}() 体内没找到 _call(...) — script 是空壳, "
            f"trace 解析没出有效步骤"
        )

    return VerifyResult(
        ok=len(errors) == 0,
        errors=errors,
        call_count=call_count,
        found_render_fn=True,
    )


__all__ = ["VerifyResult", "verify_frozen_script"]
