"""`_apply_patches` 的每个调用点，跟重构**之前**的版本语义必须一致。

# 为什么是拿 git 历史对拍，而不是拿当前代码自检

8/13 把 19 个一模一样的 try 块收成 `_try_patch(fn, msg)` 时，AST 转换只取了
`call.func.id`，结果吞掉两样东西：

  1. **P42 的实参** —— 原来是 `_patch_p42_memory_skip_background(CV_CF_SOURCE)`。
     参数没了 → TypeError → memory 来源闸整个没装上。那道闸挡的是"员工邮件正文
     进个人知识库"，是 8/8 专门修过的隐私红线。
  2. **19 个 handler 的 `exc_info=True`** —— patch 失败不再有堆栈。

当时是做了验证的，而且全绿："19 条 error 文案逐字一致"。问题在于**我只比了
`args[0]`，也就是我自己选择要保留的那个东西**。验证范围等于设计范围，所以它
天然抓不到没想到的部分。形状检查同理：验了 `len(handler.args) == 2`，没看
`handler.keywords`；验了 try 体是单个 `_patch_*` 调用，没看那个调用有没有实参。

所以这个文件的做法反过来：**不预设该保留什么**，直接把重构前那一版从 git 取出来，
逐个调用点比对「函数名 + 实参 + 错误文案 + handler 的全部 kwargs」。凡是当年
存在的，现在都得在。

`REFACTOR_BASE` 是重构前最后一个提交。它固定不动 —— 这条测试比的是"跟历史一致"，
不是"跟上一版一致"，所以基准不该随着后续提交漂移。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
_REPO = _DIR.parents[2]
_REL = "edge/hermes-plugins/catfish-xcatfish-user/plugin.py"

#: 把 19 个 try 块收成 _try_patch 之前的最后一个提交。
REFACTOR_BASE = "cc4d71a"

#: **有意删掉**的 patch —— 必须逐个写理由。
#:
#: 这个名单存在的意义是「删除得是个决定，不是个意外」。基准提交里有、现在没有的
#: patch，要么在这里有名字有理由，要么这条测试红。
#:
#: 为什么不干脆把基准提交往前挪: 挪了就等于把所有历史差异一笔勾销，包括那些
#: **不该发生**的丢失。8/13 那次就是靠跟历史对拍才发现 P42 的实参被吞掉的。
INTENTIONALLY_REMOVED: dict[str, str] = {
    "_patch_p18_compress_endpoint": (
        "8/13 删。P18 (6/17) 做的是「Companion 在 80% 上下文时主动触发 hermes 压缩」，"
        "但查证后确认它解的问题已经被别的机制解掉了:\n"
        "  · 网关 conversation_compressor 自动压，阈值 min(context×0.7, 12万) —— "
        "    **比 P18 想要的 80% 还低**，员工根本走不到 80%\n"
        "  · 实测网关每轮都在压 (12:31 三次, 省 41~43%)，微信那条路同样覆盖\n"
        "  · hermes 自己的 ContextCompressor 在 agent.log 里**一次都没压过**\n"
        "  · P18 的 endpoint 注册了 25 次，**真调用 0 次**，TS/Rust 里零引用\n"
        "而且它同一天 (6/17) 被 P3.5.17.c.2 抽掉了前提——ContextCounter 因为"
        "「cumulative cost ≠ ctx 占用，数学错」被砍，注释写着「不需 Companion 算」。"
        "Phase 2 两个月没做不是没排上，是前提当天就没了。"
    ),
}


def _old_source() -> str | None:
    try:
        r = subprocess.run(
            ["git", "show", f"{REFACTOR_BASE}:{_REL}"],
            capture_output=True, text=True, cwd=_REPO, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout else None


def _fn_of(node: ast.Call) -> str | None:
    return node.func.id if isinstance(node.func, ast.Name) else None


def _old_call_sites(src: str) -> dict[str, dict]:
    """重构前: 每个 try 块 → {args, msg, handler_kwargs}"""
    n = next(x for x in ast.parse(src).body
             if getattr(x, "name", None) == "_apply_patches")
    out: dict[str, dict] = {}
    for st in n.body:
        if not isinstance(st, ast.Try):
            continue
        call = st.body[0].value
        h = st.handlers[0].body[0].value
        out[_fn_of(call)] = {
            "args": [ast.unparse(a) for a in call.args],
            "msg": ast.literal_eval(h.args[0]),
            "handler_kwargs": sorted(
                f"{k.arg}={ast.unparse(k.value)}" for k in h.keywords
            ),
        }
    return out


def _new_call_sites() -> dict[str, dict]:
    """重构后: 每个 `_try_patch(fn, msg, *args)` → 同样的三元组。

    handler kwargs 现在是 `_try_patch` 里那一处统一的 logger.error，
    所以从那个函数体里读。
    """
    src = (_DIR / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # _try_patch 内部那次 logger.error 的 kwargs（19 个调用点共用）
    tp = next(x for x in tree.body if getattr(x, "name", None) == "_try_patch")
    shared_kwargs: list[str] = []
    for c in ast.walk(tp):
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "error"):
            shared_kwargs = sorted(
                f"{k.arg}={ast.unparse(k.value)}" for k in c.keywords
            )
    ap = next(x for x in tree.body if getattr(x, "name", None) == "_apply_patches")
    out: dict[str, dict] = {}
    for c in ast.walk(ap):
        if not (isinstance(c, ast.Call) and _fn_of(c) == "_try_patch"):
            continue
        fn = c.args[0].id
        out[fn] = {
            "args": [ast.unparse(a) for a in c.args[2:]],
            "msg": ast.literal_eval(c.args[1]),
            "handler_kwargs": shared_kwargs,
        }
    return out


@pytest.fixture(scope="module")
def pair():
    old = _old_source()
    if old is None:
        pytest.skip(f"取不到基准提交 {REFACTOR_BASE}（浅克隆 / 非 git 环境）")
    old_sites = _old_call_sites(old)
    # 有意删掉的从基准里摘出去 —— 但摘之前先确认它当年真在，
    # 免得名单里堆着一堆早就不存在的名字，久了没人敢清。
    for name in INTENTIONALLY_REMOVED:
        assert name in old_sites, (
            f"INTENTIONALLY_REMOVED 里的 {name} 在基准提交 {REFACTOR_BASE} 里并不存在 —— "
            "名单过期了，删掉这条"
        )
        old_sites.pop(name)
    return old_sites, _new_call_sites()


def test_same_set_of_patches(pair):
    """调用点集合只准按 INTENTIONALLY_REMOVED 收缩，不准无声消失。

    删一个 patch 是个决定，得有名字有理由。这条测试逼那个决定显式化 ——
    否则"某个 patch 悄悄没了"跟"某个 patch 被吞了实参"一样看不出来。
    """
    old, new = pair
    assert set(old) == set(new), (
        f"调用点集合变了 —— 少了 {set(old) - set(new)}, 多了 {set(new) - set(old)}。\n"
        "有意删的请写进 INTENTIONALLY_REMOVED 并附理由。"
    )


def test_removed_patches_leave_no_trace():
    """有意删掉的 patch，代码里不能还剩引用。

    P18 那次删了 4 处：re-export / _apply_patches 调用 / _patched_app_init 里的
    路由注册 / 一整段设计注释。漏掉任何一处的表现都不同——漏 re-export 是
    AttributeError，漏路由注册是 handler 不存在时 500，漏注释是误导下一个人。
    """
    src = (_REPO / _REL).read_text(encoding="utf-8")
    for name in INTENTIONALLY_REMOVED:
        assert name not in src, f"{name} 已声明删除，但 plugin.py 里还有引用"


def test_no_call_lost_its_arguments(pair):
    """★ P42 就是栽在这条上。

    `_patch_p42_memory_skip_background(CV_CF_SOURCE)` 的实参被吞掉后，patch 抛
    TypeError 被 except 接住，只留一行 error —— 而当时收尾日志还是写死的
    "✓ (15 patches applied)"，所以什么都看不出来。
    """
    old, new = pair
    lost = {k: old[k]["args"] for k in old
            if old[k]["args"] and old[k]["args"] != new[k]["args"]}
    assert not lost, f"这些 patch 的实参丢了或变了: {lost}"


def test_no_handler_lost_its_kwargs(pair):
    """★ `exc_info=True` 就是栽在这条上。

    19 个 handler 原来都带 exc_info=True。丢了之后 patch 失败只剩一句话，
    "name 'functools' is not defined" 这种错根本不知道在哪一行。
    """
    old, new = pair
    lost = {k: old[k]["handler_kwargs"] for k in old
            if set(old[k]["handler_kwargs"]) - set(new[k]["handler_kwargs"])}
    assert not lost, f"这些 handler 的 kwargs 丢了: {lost}"


def test_error_messages_unchanged(pair):
    """文案是历次事故留下的，重构不该顺手改掉一个字。"""
    old, new = pair
    diff = {k: (old[k]["msg"], new[k]["msg"]) for k in old
            if old[k]["msg"] != new[k]["msg"]}
    assert not diff, f"文案变了: {diff}"


def test_try_patch_forwards_args():
    """`_try_patch` 得真把 *args 转给 fn —— 光在签名上写着不算。

    第一版签名里连 *args 都没有；补上之后如果只是收着不转发，P42 一样是死的，
    而且更难看出来（不报错，闸就是不生效）。
    """
    src = (_DIR / "plugin.py").read_text(encoding="utf-8")
    tp = next(x for x in ast.parse(src).body
              if getattr(x, "name", None) == "_try_patch")
    assert tp.args.vararg is not None, "_try_patch 没有 *args"
    forwarded = any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "fn"
        and any(isinstance(a, ast.Starred) for a in c.args)
        for c in ast.walk(tp)
    )
    assert forwarded, "*args 收了但没转发给 fn —— P42 会静默失效"
