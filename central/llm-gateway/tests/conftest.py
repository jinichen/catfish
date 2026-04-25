"""pytest 启动钩子 —— 自动加载项目根目录的 .env。

为什么要做：
    test_smoke 里读 CATFISH_DEV_TOKEN，但 pytest 自己不会读 .env，
    所以如果不在 shell 里手动 export，token 会用默认 'dev-token-local'，
    跟 gateway 实际启动用的 .env token 对不上，所有需要 auth 的测试都会 401。

    放这里就不用每次跑测试都 `set -a; source .env; set +a` 了。
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv_for_tests() -> None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
    except ImportError:
        return
    # 项目根 = tests/ 的父目录
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        # override=False 让 shell 里手动 export 的优先级更高
        load_dotenv(env_file, override=False)


_load_dotenv_for_tests()

# 让 BASE 默认也能被 conftest 修改前后的 env 一起影响。
# 实际 BASE / TOKEN 在 test_smoke.py 顶层读，conftest 先于 test 模块导入。
_ = os.environ.get("CATFISH_DEV_TOKEN", "")  # touch to keep import lint quiet
