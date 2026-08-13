"""文件日志必须真的覆盖 uvicorn 的访问日志。

# 病历 (8/13)

`_setup_file_logging` 的注释写着「加到 root logger, **所有 catfish.* / uvicorn /
litellm 日志都进文件**」。实测 `gateway.log` 里 `HTTP/1.1` **0 条** ——
uvicorn 的 access log 从来没落过盘。

真因 (uvicorn.config.LOGGING_CONFIG, 实测 uvicorn 0.52.2):

    uvicorn         propagate: false
    uvicorn.access  propagate: false

挂在 root 上的 handler 收不到它们。**注释描述的是一件没发生的事。**

# 代价不是"少点日志"

8/13 当天撞了两次:

  · `gpt-5.6-luna` 打到 8999 拿 404, 想事后查是哪个组件在调 —— 没有落盘的
    访问日志可查, 只能靠人贴终端输出
  · 想统计"哪些端点从没被调过"(P18 / P30 那类死路由), 数出来 39/39 全零,
    差点报成 39 个死端点 —— 实际是访问日志压根不在文件里

网关一重启终端输出就没了, HTTP 层的所有证据都是易失的。

# 这个文件钉什么

不是"注释要写对", 是"handler 真的挂上了"。判据从 logging 的运行时状态取,
不看注释。
"""
from __future__ import annotations

import ast
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parent.parent / "src/catfish_gateway/app.py"
_SRC = _APP.read_text(encoding="utf-8")


def _attach_fn_source(*, code_only: bool = False) -> str:
    """取 `_attach_file_handler_to_uvicorn` 的源码。

    `code_only=True` 会去掉 docstring —— 写"源码里不许出现 X"这类检查时必须用它。

    ⚠ 这是同一个坑今天第 4 次: docstring 里**正当地**写着 X (它在解释为什么不许
       出现 X), 于是注释写得越清楚测试越容易红在一个跟本意完全相反的地方。
       前三次分别在 box_parser、P18 拆分 (两条)。这次是
       `test_does_not_flip_propagate` —— 函数注释里写着「改 `propagate = True`
       会导致 stdout 双写」, 结果被判成"动了 propagate"。
    """
    node = next(
        n for n in ast.parse(_SRC).body
        if isinstance(n, ast.FunctionDef) and n.name == "_attach_file_handler_to_uvicorn"
    )
    if not code_only:
        return ast.get_source_segment(_SRC, node) or ""
    stmts = [
        s for s in node.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str))
    ]
    return "\n".join(ast.get_source_segment(_SRC, s) or "" for s in stmts)


def _setup_fn_source() -> str:
    node = next(
        n for n in ast.parse(_SRC).body
        if isinstance(n, ast.FunctionDef) and n.name == "_setup_file_logging"
    )
    return ast.get_source_segment(_SRC, node) or ""


def test_uvicorn_loggers_do_not_propagate():
    """先证明前提成立 —— 这条红了说明 uvicorn 改了默认行为, 下面的修法就该重审。

    没有这条的话, uvicorn 哪天把 propagate 改成 True, 我们这边就变成"挂了两次
    handler", 日志每条写两遍 (6/30 P3.5.149 刚修过一次同款问题)。
    """
    uv = pytest.importorskip("uvicorn.config", reason="本机没装 uvicorn")
    cfg = uv.LOGGING_CONFIG["loggers"]
    assert cfg["uvicorn"].get("propagate") is False, (
        "uvicorn 改成冒泡了 —— app.py 里单独挂 handler 那段要重审, 否则会双写"
    )
    assert cfg["uvicorn.access"].get("propagate") is False


def test_attach_helper_covers_uvicorn_loggers():
    """★ 光挂 root 不够, 必须显式给 uvicorn 的三个 logger 也挂。"""
    node = next(
        n for n in ast.parse(_SRC).body
        if isinstance(n, ast.FunctionDef) and n.name == "_attach_file_handler_to_uvicorn"
    )
    seg = ast.get_source_segment(_SRC, node) or ""
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        assert f'"{name}"' in seg, f"没给 {name} 挂 handler"
    assert "addHandler" in seg


def test_attach_is_called_again_in_lifespan():
    """★★ 这条是那个 bug 的真正要害 —— **挂早了会被抹掉**。

    第一版只在 `_setup_file_logging`(模块导入期) 挂, 重启后 gateway.log 里
    HTTP/1.1 **仍是 0 条**。真因:

        uvicorn.config.Config.configure_logging()
          → logging.config.dictConfig(LOGGING_CONFIG)
        而 LOGGING_CONFIG 给 uvicorn.access 指定了 handlers: ["access"]
          → dictConfig **替换整个 handler 列表**, 先挂上的被抹掉

    模块导入发生在 uvicorn 配置**之前**, 所以那次注定无效。只有 lifespan
    (dictConfig 已跑完) 那次留得住。

    在真 uvicorn 上隔离验证过: 只模块层挂 → 文件 0 条; 加 lifespan → 3 条。
    """
    lifespan = next(
        n for n in ast.parse(_SRC).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "lifespan"
    )
    called = any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        and c.func.id == "_attach_file_handler_to_uvicorn"
        for c in ast.walk(lifespan)
    )
    assert called, (
        "lifespan 里没再调 _attach_file_handler_to_uvicorn() —— "
        "模块导入期挂的那次会被 uvicorn 的 dictConfig 抹掉, 访问日志仍然不落盘"
    )


def test_dictconfig_really_replaces_handlers():
    """钉住前提: uvicorn 的 LOGGING_CONFIG 确实给 access 指定了独占 handler。

    它哪天不再指定 handlers (或改成 propagate), 上面那套"lifespan 再挂一次"
    的理由就不成立了, 该重审。
    """
    uv = pytest.importorskip("uvicorn.config", reason="本机没装 uvicorn")
    acc = uv.LOGGING_CONFIG["loggers"]["uvicorn.access"]
    assert acc.get("handlers"), (
        "uvicorn.access 不再指定 handlers —— dictConfig 不会再抹掉我们挂的那个, "
        "lifespan 里那次补挂可以重新评估"
    )


def test_does_not_flip_propagate():
    """不许改 uvicorn 的 propagate。

    改了会让每条访问日志同时走 uvicorn 自己的 stdout handler 和 root handler,
    stdout 里打两遍 —— 6/30 P3.5.149 就是修"log 每条写两遍", 别重蹈。
    正确做法是**只加 handler**。
    """
    seg = _attach_fn_source(code_only=True)   # 去 docstring, 见 helper 注释
    assert "propagate" not in seg, (
        "动了 propagate —— 会造成 stdout 双写, 改用只 addHandler"
    )


def test_handler_attach_is_idempotent():
    """重复调用不能累加 handler。

    启动序列会让这个函数跑两次 (`python -m catfish_gateway.app` 触发一次,
    uvicorn 再 import 一次 —— 见函数里 6/30 那段注释)。root 那边已经有防重
    guard, 新加的 uvicorn 那段也必须有, 否则又是"每条写两遍"。
    """
    seg = _attach_fn_source()
    # 里面要有 RotatingFileHandler + baseFilename 的比对
    assert "RotatingFileHandler" in seg and "baseFilename" in seg, (
        "给 uvicorn 挂 handler 时没做防重检查 —— 启动会挂两次, 日志双写"
    )


def test_smoke_attach_and_detach(tmp_path, monkeypatch):
    """行为层: 真挂一次, 确认 uvicorn.access 上出现了指向该文件的 handler。

    用 tmp_path 的日志文件, 跑完把 handler 摘干净 —— 不能污染同进程里其它测试
    的 logging 状态 (pytest 全程共用一个 logging 树)。
    """
    import importlib
    app_module = importlib.import_module("catfish_gateway.app")

    log_file = tmp_path / "logs" / "gw.log"
    monkeypatch.setenv("CATFISH_LOG_FILE", str(log_file))

    targets = [logging.getLogger(n) for n in ("uvicorn", "uvicorn.access", "uvicorn.error")]
    before = {id(h) for lg in targets for h in lg.handlers}
    try:
        app_module._setup_file_logging()
        attached = [
            h for lg in targets for h in lg.handlers
            if isinstance(h, RotatingFileHandler)
            and os.path.abspath(h.baseFilename) == os.path.abspath(str(log_file))
        ]
        assert attached, "跑完之后 uvicorn 的 logger 上没有指向该文件的 handler"

        # 再跑一次不该翻倍 (幂等)
        n1 = len(attached)
        app_module._setup_file_logging()
        attached2 = [
            h for lg in targets for h in lg.handlers
            if isinstance(h, RotatingFileHandler)
            and os.path.abspath(h.baseFilename) == os.path.abspath(str(log_file))
        ]
        assert len(attached2) == n1, f"重复调用后 handler 从 {n1} 变成 {len(attached2)} —— 会双写"
    finally:
        for lg in targets:
            for h in list(lg.handlers):
                if id(h) not in before:
                    lg.removeHandler(h)
                    h.close()
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, RotatingFileHandler) and \
                    os.path.abspath(h.baseFilename) == os.path.abspath(str(log_file)):
                root.removeHandler(h)
                h.close()
