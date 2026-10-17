"""conftest 的凭据隔离名单不能漏: 谁在 import 时算 OAUTH_ID_TOKEN_PATH, 谁就得在名单里。"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src" / "catfish_tool_bridge"
_spec = importlib.util.spec_from_file_location("tool_bridge_tests_conftest", HERE / "conftest.py")
conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conftest)


def test_import时算凭据路径的模块都在隔离名单里():
    defined = {p.stem for p in SRC.glob("*.py")
               if re.search(r"^OAUTH_ID_TOKEN_PATH\s*=", p.read_text(encoding="utf-8"), re.M)}
    assert defined <= set(conftest._TOKEN_MODULES), f"加进 tests/conftest.py _TOKEN_MODULES: {defined - set(conftest._TOKEN_MODULES)}"
