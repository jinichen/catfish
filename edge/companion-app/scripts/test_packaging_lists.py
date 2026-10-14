#!/usr/bin/env python3
"""打包清单的自查 (9/28) —— 只用标准库, CI 里直接 `python3 scripts/test_packaging_lists.py`。

钉住两件事, 都是「装机时静默缺功能」那一类:
  · build_hermes_wheelhouse.py (10/14 起取代 fetch_hermes_deps.py) 读的额外包清单跟
    Companion 装机自检读的是同一份, 解析对;
  · catfish-edge-runtime.tar.gz 里带了上传文件解析脚本 (9/28 Windows 上传文件
    报「Python 解释器找不到」, 修好解释器之后下一步就是找不到脚本), 且没把测试
    和打包脚本带进员工机器。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hermes_deps_list() -> None:
    wh = _load("build_hermes_wheelhouse")
    binary, source_only = wh.extras()
    names = {wh.norm(r.split("=")[0].split("<")[0]) for r in binary + source_only}
    for want in ("jieba", "playwright", "watchdog", "pyyaml", "pypdfium2", "openpyxl", "python_docx",
                 "python_pptx", "xlrd"):
        assert want in names, f"清单缺 {want}: {sorted(names)}"
    assert "jieba" in source_only, "jieba 只有源码包, 要标 sdist"
    assert any(r.startswith("playwright==") for r in binary), "playwright 要钉版本 (决定打进包的 Chromium)"
    assert wh.SOURCE_BUILD["x86_64-pc-windows-msvc"] == set()


def test_edge_runtime_carries_file_parse_scripts() -> None:
    build = _load("build_edge_runtime")
    names = [arcname for arcname, _ in build.collect()]
    parse = sorted(n for n in names if n.startswith("file-parse/"))
    assert "file-parse/parse_file.py" in parse and "file-parse/attachment_bm25.py" in parse, parse
    # parse_file.py 按同目录 import 这几个 —— 少一个就是装上了、一上传就挂
    for helper in ("parse_file_common", "parse_file_office", "parse_file_pdf", "parse_file_audio"):
        assert f"file-parse/{helper}.py" in parse, parse
    assert not [n for n in parse if "/test_" in n or n.endswith(".sh")], parse
    assert set(build.MUST_EXIST) <= set(names)


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    for test in tests:
        test()
        print(f"  ✓ {test.__name__}")
    print(f"✓ 打包清单自查 {len(tests)} 项通过")
    sys.exit(0)
