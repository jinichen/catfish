"""冻结的 eis-login 必须按站点现查密码, 不许再焊死凭据引用。

# 病历 (8/20)

员工在 Companion「登录密码」界面里存了 eis.ffcs.cn / neis.ffcs.cn, 界面显示
"已保存"。而冻结的 `department/eis-login` 读的是**另一条**:

    界面管的      keychain://catfish-teaching:eis.ffcs.cn
    冻结 skill 读  keychain://eis_password        ← 4/28 手工 security 建的

8/20 在员工机上比过两条的 sha256: **不一样**。也就是说这条 skill 一直在拿过期
密码去登, 表现是登录报"账号或密码错误", 而界面上一切正常 —— 两边谁也不知道谁。

这个病 `skill_freeze_template.py:175-190` 早就写着 (8/17 实撞), 修法也在那儿:

    secret_ref 焊死的引用串会**过期** …
    secret_for_site 没有任何"当天的值"可以焊: 站点是**运行时**从 page.url 取的。
    所以这条分支不产生参数, 也不需要产生 —— 焊进去的是"按站点查"这个动作本身,
    换了密码它仍然对。

本 skill 5/12 凝固, 比 secret_for_site (8/18 才加) 早三个月 —— 不是坏了, 是生得早。

# 为什么破例手改了 script.py

SKILL.md 写着「不要手改 script.py, 业务流程变了走"再教一次"」。8/20 破例的理由:

  · 差别只有一行, 而且改出来的**跟凝固管道会生成的逐字一致**(下面第 3 条钉的
    就是这个), 将来真重新凝固也会生成同一段, 不冲突
  · "再教一次"要员工完整跑一遍 EIS 登录

# 这个文件钉什么

  1. script.py 里不许再出现焊死的凭据引用 (password_ref / keychain://)
  2. 密码那一步必须是 secret_for_site
  3. 那一段必须跟 skill_freeze_template 生成的**逐字一致** ← 最值钱的一条

第 3 条是把手改和管道绑在一起: 模板哪天改了 (比如换 last_step 命名、改 raise
的换行), 这条会红, 提醒把 script.py 重新对齐 —— 而不是让两边悄悄分叉。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_SKILL = Path(__file__).resolve().parents[3] / "skills" / "department" / "eis-login"
_SCRIPT = _SKILL / "script.py"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(), reason=f"找不到 {_SCRIPT}"
)


def _code() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _executable_lines() -> list[tuple[int, str]]:
    """只要可执行行 —— 注释里讲"改前是什么"是**故意留的**, 不该被判违规。"""
    out = []
    for i, ln in enumerate(_code().splitlines(), 1):
        st = ln.strip()
        if not st or st.startswith("#"):
            continue
        out.append((i, ln))
    return out


# ─────────────────────────────────────────────────────────────────────
def test_不许再焊死凭据引用():
    """`password_ref` / `keychain://` 不许出现在可执行代码里。

    只要它们还在, 员工在界面改的密码就到不了这条 skill。
    """
    bad = [
        f"{i}: {ln.strip()[:78]}"
        for i, ln in _executable_lines()
        if "password_ref" in ln or "keychain://" in ln
    ]
    assert not bad, (
        "eis-login 又焊死了凭据引用:\n" + "\n".join(bad) + "\n"
        "焊死的引用会过期 —— 员工在 Companion 改密码, 这条 skill 读不到, "
        "登录报「账号或密码错误」而界面显示「已保存」。用 secret_for_site。"
    )


def test_密码那一步必须按站点现查():
    code = _code()
    assert '"secret_for_site": True' in code, (
        "密码那一步不再用 secret_for_site —— 改回焊死的引用会静默拿到过期密码"
    )
    # 顺带确认它填的是密码框, 不是填到别的字段去了
    m = re.search(
        r'_call\("catfish_browser_fill",\s*\{"selector":\s*(\'[^\']+\'),\s*"secret_for_site":\s*True\}\)',
        code,
    )
    assert m, "secret_for_site 那一行的形状变了, 确认它还填在密码框上"
    assert m.group(1) == "'#pwd'", f"密码填到了 {m.group(1)}, 不是 '#pwd'"


def test_手改的那段跟凝固管道生成的逐字一致():
    """把手改和模板绑死 —— 模板变了就提醒重新对齐, 别让两边悄悄分叉。"""
    from catfish_tool_bridge.skill_freeze_template import _emit_step

    tmpl, _ = _emit_step(
        {"tool": "catfish_browser_fill",
         "args": {"selector": "#pwd", "secret_for_site": True}},
        None,
    )
    lines = _code().splitlines()
    starts = [
        i for i, ln in enumerate(lines)
        if 'last_step = "fill_secret_for_site' in ln
    ]
    assert len(starts) == 1, f"secret_for_site 段落出现 {len(starts)} 次, 期望 1 次"
    mine = lines[starts[0]: starts[0] + len(tmpl)]

    assert mine == tmpl, (
        "script.py 里那段跟 skill_freeze_template 生成的对不上了:\n"
        + "\n".join(f"  script.py: {a!r}\n  模板     : {b!r}"
                    for a, b in zip(mine, tmpl) if a != b)
        + "\n把 script.py 那段按模板重新对齐 (或确认模板改动是有意的)。"
    )


def test_签名里没有password_ref但吃得下老调用():
    """删了参数, 但老调用方传进来不能炸 —— run_skill 是 fn(**params) 展开调的。"""
    import ast

    fn = next(
        n for n in ast.walk(ast.parse(_code()))
        if isinstance(n, ast.FunctionDef) and n.name == "render_eis_login"
    )
    names = [a.arg for a in fn.args.args]
    assert "password_ref" not in names, "password_ref 又回到签名里了"
    assert fn.args.kwarg is not None, (
        "签名没有 **kwargs —— 老调用方传 password_ref 会 TypeError。"
        "对话历史 / 别的 skill 里可能还留着老写法。"
    )
