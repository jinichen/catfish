"""picker 只能有一个读法 —— plugin.py 那份必须就是 model_authority 那份。

# 为什么值得一条测试

「模型只能 picker 模型」是硬规矩。8/13 之前这条规矩在本 plugin 里有**两份**实现:

    plugin.py:_read_catfish_picker_model     ← P21 / P23 / P39 走这份
    model_authority.read_picker_model        ← P46 decide_model 走那份

两份并存本身不出错, 出错的是**飘**。一旦飘了, 表现是"有的路径听 picker、有的
不听" —— 跟 8/9 那次会话级 model override 一模一样: 同一个员工、同一个界面,
不同请求用不同模型, 而且没有任何地方显示这件事。那次是靠翻 state.db 才查出来的。

现在 plugin.py 那份改成转调。这个文件钉两件事:
  1. 它确实是转调, 不是又抄了一份 (源码层)
  2. 两者在各种脏输入下输出一致 (行为层)

第 2 条即使将来有人把转调改回独立实现, 也还能兜住。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_DIR))

import model_authority as ma  # noqa: E402


#: 8/15: 从"写死 plugin.py"改成"在 plugin*.py 里找"。
#:
#: 那天把 plugin.py 从 3226 行拆成 8 个模块, _read_catfish_picker_model 跟着
#: P23 搬到了 plugin_runtime.py, 这两条测试就红了 —— 而它们要守的规矩
#: (picker 只能有一份读法) 一个字没变。
#:
#: 判据钉在**文件位置**上、守的却是**内容**, 跟同一天 test_outputs_dir_convention
#: 栽的是同一条。改成扫目录。
def _find_impl_source():
    """在 plugin*.py 里找 _read_catfish_picker_model 的定义, 返 (文件名, 源码)。

    顺带钉住"只有一份": 两个模块各定义一份的话直接报出来 —— 那正是本文件
    开头说的"飘"的起点。
    """
    hits = []
    for f in sorted(_PLUGIN_DIR.glob("plugin*.py")):
        src = f.read_text(encoding="utf-8")
        for n in ast.parse(src).body:
            if getattr(n, "name", None) == "_read_catfish_picker_model":
                hits.append((f.name, src, n))
    assert hits, "plugin*.py 里找不到 _read_catfish_picker_model"
    assert len(hits) == 1, (
        f"_read_catfish_picker_model 有 {len(hits)} 份定义: "
        f"{[h[0] for h in hits]} —— 又回到两份各自飘的老路了"
    )
    return hits[0]


def _plugin_impl():
    """把那一个函数单独抠出来执行。

    不 import 整个模块 —— plugin.py 一加载就会去 patch hermes, 而这条测试跟
    hermes 装没装无关。
    """
    fname, src, node = _find_impl_source()
    seg = ast.get_source_segment(src, node)
    # 拆分后函数体里是 `_model_authority().read_picker_model(...)`,
    # 那个访问器是延迟取兄弟模块用的 (见 plugin_runtime._sib)。
    # 这里不想真去加载兄弟模块, 直接喂一个返回 ma 的桩。
    ns: dict = {"model_authority": ma, "_model_authority": lambda: ma}
    exec(seg, ns)  # noqa: S102
    return ns["_read_catfish_picker_model"]


def test_plugin_delegates_instead_of_reimplementing():
    """源码层: 函数体必须是转调, 不能自己再去读文件。

    有人把它改回独立实现时这条会红 —— 那正是需要人看一眼的时刻。
    """
    _fname, src, node = _find_impl_source()
    # 只看**语句**, 跳过 docstring —— docstring 里正当地写着 picker_state.json
    # 这些词 (它在解释为什么不再自己读文件)。第一版没跳, 于是这条测试红在了
    # 一个跟本意完全相反的地方: 注释写得越清楚越容易挂。
    stmts = [s for s in node.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                     and isinstance(s.value.value, str))]
    body_src = "\n".join(ast.get_source_segment(src, s) or "" for s in stmts)

    # 8/15: 只认 `read_picker_model` 这个**转调目标**, 不再要求前缀逐字是
    # `model_authority.` —— 拆分后它成了 `_model_authority().read_picker_model`
    # (延迟取兄弟模块的访问器)。要守的是"转调给唯一权威", 不是前缀长什么样。
    assert "read_picker_model" in body_src, "应该转调, 别再抄一份"
    for smell in ("picker_state.json", "json.loads", "read_text"):
        assert smell not in body_src, (
            f"函数体里出现了 {smell!r} —— 看起来又自己读文件了, "
            "那就回到了两份实现各自飘的老路"
        )


# ── 行为层: 脏输入下两份必须给同一个答案 ────────────────────────

CASES = [
    ("文件不存在", None),
    ("空文件", ""),
    ("只有空白", "   \n"),
    ("坏 json", "{不是json"),
    ("顶层是数组", '["a"]'),
    ("顶层是字符串", '"hello"'),
    ("没有 chat_model", '{"other": 1}'),
    ("chat_model 空串", '{"chat_model": ""}'),
    ("chat_model 全空格", '{"chat_model": "   "}'),
    ("chat_model 是数字", '{"chat_model": 123}'),
    ("chat_model 是 null", '{"chat_model": null}'),
    ("chat_model 前后带空格", '{"chat_model": "  deepseek  "}'),
    ("正常", '{"chat_model": "catfish-public-deepseek-flash"}'),
]


@pytest.mark.parametrize("label,content", CASES, ids=[c[0] for c in CASES])
def test_two_implementations_agree(label, content, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".catfish").mkdir(parents=True)
    picker = home / ".catfish" / "picker_state.json"
    if content is not None:
        picker.write_text(content, encoding="utf-8")

    # ⚠ 必须打 PICKER_STATE_PATH, 不能只打 Path.home()。
    # `model_authority.PICKER_STATE_PATH = Path.home() / ".catfish" / ...` 是
    # **模块导入时**求值的, 之后再改 Path.home 影响不到它。第一版只打了 Path.home,
    # 于是无参调用读的还是真实 HOME 下那个文件, 测试挂在一个跟本意无关的地方。
    # 生产上无所谓 (进程活着时 HOME 不变), 但这一点必须在测试里说清楚。
    monkeypatch.setattr(ma, "PICKER_STATE_PATH", picker)

    assert _plugin_impl()() == ma.read_picker_model(picker), (
        f"两份 picker 读法在「{label}」上不一致"
    )


def test_picker_path_is_frozen_at_import_and_points_at_the_right_file():
    """PICKER_STATE_PATH 是导入时定的, 位置必须是 ~/.catfish/picker_state.json。

    Companion 写的就是这个路径 (picker_state.json 的 chat_model 字段)。这里钉住
    文件名和目录, 免得哪天有人"顺手"改成 ~/.catfish/state/picker.json 之类 ——
    改了不报错, 只是 picker 永远读不到, 而调用方全是 `if picker_model:` 的真值
    判断, 表现就是"选了模型没反应"。
    """
    assert ma.PICKER_STATE_PATH.name == "picker_state.json"
    assert ma.PICKER_STATE_PATH.parent.name == ".catfish"


def test_blank_and_missing_both_mean_no_opinion():
    """空 / 缺失都必须是空串, 不能是 None 或抛异常。

    调用方全是 `if picker_model:` 这种真值判断。返 None 也能过, 但一旦有人写
    `.strip()` 就 AttributeError; 抛异常则会被上层 except 吞成 warning ——
    两种都是"picker 静默失效"。
    """
    assert ma.read_picker_model(Path("/definitely/not/here.json")) == ""
    assert isinstance(ma.read_picker_model(Path("/definitely/not/here.json")), str)


def test_json_with_extra_keys_still_reads_chat_model():
    """picker 文件将来加字段不该影响读取 —— Companion 写它, 我们只读一个键。"""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "picker_state.json"
        p.write_text(json.dumps({
            "chat_model": "catfish-public-gemini-pro",
            "updated_at": "2026-08-13T04:34:11+00:00",
            "future_field": {"nested": True},
        }), encoding="utf-8")
        assert ma.read_picker_model(p) == "catfish-public-gemini-pro"
