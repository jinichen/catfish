"""wiki Step 2 的 prompt 必须真的拼得出来 (8/15 晚)。

# 病历: 一个让功能整天零产出、却没有任何红色信号的 bug

`_build_generation_prompt()` 原来是:

    return _GENERATION_PROMPT_TEMPLATE.format(today=time.strftime("%Y-%m-%d"))

而模板里有三处**写给 LLM 看的字面量花括号** (frontmatter 示例):

    {name: "<名字>", rel: "<关系, 2-4 字>"}     ×2
    {name: "中电福富", rel: "隶属"}              ×1

`.format()` 见到 `{name: ...}` 就去找名叫 `name` 的参数 → `KeyError('name')`。

## 后果链条

    KeyError
      → _call_generation_llm 的 `except Exception` 吞成 warning
      → 返 None
      → 日志打 INFO "wiki Step 2 generation 返空 (skip)"

最后那句读起来像"这次没什么可生成的", 实际是"每次都失败"。8/15 当天
agent.log 里 17 次, 09:30 到 18:16 —— **wiki 条目生成一整天零产出**,
而员工侧、日志的 INFO 行、单测, 没有一处会说它坏了。

跟当天反复遇到的是同一族: **`except` 把失败变成了沉默**。

## 为什么之前没测出来

`_build_generation_prompt()` 一条测试都没有。它是个无参函数、返回一个字符串
—— 看起来"没什么好测的"。但正因为没人调它验证过, 一个 `.format` 的参数错误
就能藏三个月。

# 这个文件钉什么

  1. 这个函数**能跑通** (最基本的一条, 也是当初缺的那条)
  2. 日期真的被替换了, 没有残留哨兵
  3. 给 LLM 看的花括号示例**原样保留** —— 它们是提示词内容, 不是占位符
  4. 模板不许再改回 `.format` 那条路
"""
from __future__ import annotations

import ast
import re
import sys
import time
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from catfish_memory_prompts import (  # noqa: E402
    _GENERATION_PROMPT_TEMPLATE,
    _TODAY_PLACEHOLDER,
    _build_generation_prompt,
)


def test_能跑通():
    """★★★ 就是当初缺的那一条。修之前跑这条: KeyError('name')。"""
    p = _build_generation_prompt()
    assert isinstance(p, str) and len(p) > 500, f"prompt 太短或不是字符串: {len(p)}"


def test_日期真的替换了():
    p = _build_generation_prompt()
    today = time.strftime("%Y-%m-%d")
    n_tpl = _GENERATION_PROMPT_TEMPLATE.count(_TODAY_PLACEHOLDER)
    assert n_tpl > 0, "模板里一个 __TODAY__ 都没有了 —— frontmatter 的日期还注得进去吗?"
    assert p.count(today) == n_tpl, (
        f"模板里有 {n_tpl} 处 {_TODAY_PLACEHOLDER}, 结果里只出现 {p.count(today)} 次日期"
    )
    assert _TODAY_PLACEHOLDER not in p, "还有没替换掉的哨兵串漏给 LLM 了"


def test_给LLM看的花括号示例原样保留():
    """★★ 那三处 `{name: ...}` 是**提示词内容**, 不是占位符。

    它教 LLM 怎么写 frontmatter 里的 related 项。被转义成 `{{name}}` 或者被当
    参数吃掉, LLM 就学不到正确格式 —— 而那种坏法同样不报错, 只是生成出来的
    wiki 条目 frontmatter 格式不对, 得等有人翻条目才发现。

    ⚠ 第一版这条写的是 `p.count("{name:") == 3`, **抓不住转义**:
      `{{name:` 里含有子串 `{name:`, count 照样是 3。做变异验证 (把花括号转义)
      时它绿着过去了。改成数"恰好一个左花括号"。
    """
    p = _build_generation_prompt()
    # 只认单个 `{`: 前面不是 `{`、后面也不是 `{`
    single = re.findall(r"(?<!\{)\{name:", p)
    assert len(single) == 3, (
        f"frontmatter 的 related 示例剩 {len(single)} 处单花括号 (应该 3 处)。\n"
        f"整串 '{{name:' 出现 {p.count('{name:')} 次 —— 两个数对不上就是被转义成 "
        "`{{name:` 了。那三处是给 LLM 看的格式示例, 不是占位符。"
    )
    assert '{name: "中电福富", rel: "隶属"}' in p, "具体例子没了"
    assert "{{" not in p, "prompt 里出现了 `{{` —— 转义符漏给 LLM 了"


def test_不许改回_format():
    """★★★ 防复发。

    转义成 `{{}}` 也能让 .format 跑通, 但那是个雷: 提示词要经常改, 下一个往
    里加 JSON / frontmatter 示例的人不会知道这里跑过 .format, 加完照样炸,
    照样静默。所以直接禁掉这条路。
    """
    src = (_DIR / "catfish_memory_prompts.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
                and isinstance(node.func.value, ast.Name)
                and "PROMPT" in node.func.value.id):
            bad.append(f"{node.func.value.id}.format() @ 第 {node.lineno} 行")
    assert not bad, (
        "\n".join(bad) + "\n\n"
        "提示词模板里有写给 LLM 看的字面量花括号, .format 会把它们当占位符, "
        "抛 KeyError 之后被上层 except 吞掉 —— 功能静默失效。用 str.replace "
        "配一个内容里不会出现的哨兵串。"
    )


@pytest.mark.parametrize("name", ["_ANALYSIS_PROMPT", "_DISTILL_PROMPT",
                                  "_SUMMARIZE_PROMPT", "_GENERATION_PROMPT_TEMPLATE"])
def test_有字面量花括号的模板不许走format(name: str):
    """把四个模板都扫一遍, 报出各自有几处字面量花括号。

    ⚠ 第一版这条是**基线就红**的 —— 它用裸子串 `".format(" not in 文件内容`
      判断, 结果查中了我自己 docstring 里引用的旧代码。判据比真事宽,
      而且红在一个跟本意完全相反的地方 (注释写得越清楚越容易挂)。
      改成走 AST, 只看真调用。
    """
    import catfish_memory_prompts as P

    tpl = getattr(P, name)
    braces = re.findall(r"\{([^{}]*)\}", tpl)
    literal = [b for b in braces if not b.strip().isidentifier()]
    if not literal:
        pytest.skip(f"{name} 没有字面量花括号, 走不走 format 都安全")

    tree = ast.parse((_DIR / "catfish_memory_prompts.py").read_text(encoding="utf-8"))
    fmt_calls = [
        f"{n.func.value.id}.format() @ 第 {n.lineno} 行"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "format" and isinstance(n.func.value, ast.Name)
    ]
    assert not fmt_calls, (
        f"{name} 里有 {len(literal)} 处字面量花括号 "
        f"(例: {{{literal[0][:40]}}}), 而这个文件里还有真的 .format 调用:\n  "
        + "\n  ".join(fmt_calls)
    )