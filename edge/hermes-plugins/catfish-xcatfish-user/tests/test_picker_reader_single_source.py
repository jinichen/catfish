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


def _plugin_impl():
    """把 plugin.py 里那一个函数单独抠出来执行。

    不 import 整个 plugin.py —— 它一加载就会去 patch hermes, 而这条测试跟
    hermes 装没装无关。
    """
    src = (_PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    seg = next(
        (ast.get_source_segment(src, n) for n in ast.parse(src).body
         if getattr(n, "name", None) == "_read_catfish_picker_model"),
        None,
    )
    assert seg is not None, "plugin.py 里找不到 _read_catfish_picker_model"
    ns: dict = {"model_authority": ma}
    exec(seg, ns)  # noqa: S102
    return ns["_read_catfish_picker_model"]


def test_plugin_delegates_instead_of_reimplementing():
    """源码层: 函数体必须是转调, 不能自己再去读文件。

    有人把它改回独立实现时这条会红 —— 那正是需要人看一眼的时刻。
    """
    src = (_PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body
                if getattr(n, "name", None) == "_read_catfish_picker_model")
    # 只看**语句**, 跳过 docstring —— docstring 里正当地写着 picker_state.json
    # 这些词 (它在解释为什么不再自己读文件)。第一版没跳, 于是这条测试红在了
    # 一个跟本意完全相反的地方: 注释写得越清楚越容易挂。
    stmts = [s for s in node.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                     and isinstance(s.value.value, str))]
    body_src = "\n".join(ast.get_source_segment(src, s) or "" for s in stmts)

    assert "model_authority.read_picker_model" in body_src, "应该转调, 别再抄一份"
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
