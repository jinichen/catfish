"""`python -m catfish_gateway.app` 不许再撞循环 import (8/18 实撞)。

# 病历

3069ad4 把 chat 执行路径从 app.py 拆到 chat_runtime.py, 拆的时候 chat_runtime
模块级写了 `from . import app as _app`, 而 app.py 第 410 行又
`from .chat_runtime import ...` —— 一个环。

平时不炸, 所以合并进 main 了: uvicorn 走 `catfish_gateway.app:app`, 那时
`catfish_gateway.app` 已经在 sys.modules 里 (只初始化了一半, 但 chat_runtime
要读的三个属性正好都在第 410 行之前定义好) —— 靠**行的先后顺序**成立。

`python -m catfish_gateway.app` 不成立: runpy 把 app.py 当 `__main__` 跑,
`catfish_gateway.app` 不在 sys.modules, 那行触发 app.py 的**第二次完整执行**,
第二遍走到第 410 行时 chat_runtime 才执行到第 18 行:

    ImportError: cannot import name '_invoke_chat_completion' from partially
    initialized module 'catfish_gateway.chat_runtime'

鸿波 8/18 重启网关就卡在这儿, 而这正好是他为了让 tool-bridge 加载新代码
必须做的那一步。

# 这个文件钉什么

  1. `python -m catfish_gateway.app` 能走完 import (不真起服务, 靠 -c 模拟 runpy)
  2. chat_runtime **模块级**不许 import app —— 运行时那条只在 `python -m` 下才
     炸, 而 CI 一般走 uvicorn 路径, 光靠运行时断言会绿着放过改回去的写法
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"


def test_run_as_main_不撞循环import():
    """★★★ 直接复现那条命令的 import 阶段。

    不用真 `python -m catfish_gateway.app` —— 那会起 uvicorn 占端口。runpy 的
    关键行为是"app.py 以 __main__ 之名执行, 且 catfish_gateway.app 不在
    sys.modules", 用 run_module(run_name="__main__") 精确复刻, 再在 run() 之前
    退出。
    """
    code = (
        "import runpy, sys\n"
        # app.py 末尾 `if __name__ == '__main__': run()` 会起 uvicorn —— 用一个
        # 假的 uvicorn 拦住, 只验 import 阶段。
        "import types\n"
        "fake = types.ModuleType('uvicorn')\n"
        "fake.run = lambda *a, **k: print('IMPORT_OK')\n"
        "sys.modules['uvicorn'] = fake\n"
        "runpy.run_module('catfish_gateway.app', run_name='__main__')\n"
    )
    # ⚠ 继承当前环境, 只**加** PYTHONPATH。
    #   第一版我传了个全新的 env={...}, 结果子进程找不到 litellm, 测试挂在
    #   "环境不对"上而不是被测行为上 —— 报错还长得像真失败。
    env = {**os.environ, "PYTHONPATH": str(_SRC)}
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=300, env=env, check=False,
    )
    assert "cannot import name" not in r.stderr, (
        "循环 import 又回来了 —— `python -m catfish_gateway.app` 起不来。\n"
        "chat_runtime 模块级不要 `from . import app`, 用 _app_mod() 惰性拿。\n\n"
        + r.stderr[-1500:]
    )
    assert "IMPORT_OK" in r.stdout, f"没走到 run(): \n{r.stderr[-1500:]}"


def test_chat_runtime_模块级不许import_app():
    """★★ 源码级拦写法退化。

    上面那条要真跑一遍 import (慢, 且依赖环境)。而改回模块级 import 的人多半
    是"顺手把 _app_mod() 展开回去" —— 直接看 AST 更硬。
    """
    tree = ast.parse((_SRC / "catfish_gateway/chat_runtime.py").read_text(encoding="utf-8"))
    bad = [
        f"第 {n.lineno} 行"
        for n in tree.body                      # 只看模块级, 函数体里的是对的
        if isinstance(n, ast.ImportFrom)
        and n.level == 1
        and any(a.name == "app" for a in n.names)
    ]
    assert not bad, (
        f"chat_runtime 模块级又 import app 了 ({', '.join(bad)})。\n"
        "app.py 会 import 本模块, 这是个环 —— uvicorn 下靠行序侥幸成立, "
        "`python -m catfish_gateway.app` 必炸。放函数里 (见 _app_mod)。"
    )


def test_keepalive常量只有一处定义():
    """★ 它是 `_stream_with_keepalive` 的**默认参数**, import 时求值 ——

    不能改成惰性取值。同时 app.py 那份要 re-export 回来, 别两处各写一个 30
    然后哪天改了一处。
    """
    rt = (_SRC / "catfish_gateway/chat_runtime.py").read_text(encoding="utf-8")
    app = (_SRC / "catfish_gateway/app.py").read_text(encoding="utf-8")
    assert "_KEEPALIVE_INTERVAL_SECS = 30" in rt, "chat_runtime 该是定义方"
    assert "_KEEPALIVE_INTERVAL_SECS = 30" not in app, (
        "app.py 又自己定义了一份 —— 该从 chat_runtime import 回去"
    )
    sys.path.insert(0, str(_SRC))
    m = importlib.import_module("catfish_gateway.app")
    assert hasattr(m, "_KEEPALIVE_INTERVAL_SECS"), "对外名字丢了"
