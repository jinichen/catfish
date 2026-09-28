"""上传文件解析的字符集 (9/28)。

9/28 Windows 上传文件:
    UnicodeEncodeError: 'gbk' codec can't encode character '\\xa5'
中文 Windows 上 Python 往管道写 stdout 默认 GBK, 文件里有个 "¥" 就崩。这里用
PYTHONIOENCODING=gbk 在任何平台上复现那台机器的输出环境。

顺带钉住输入侧: 中文 Windows 上 Excel 另存的 CSV、老记事本存的 txt 是 GBK,
记事本「Unicode」是带 BOM 的 UTF-16 —— 原来一律按 UTF-8 读, CSV 直接报错、
txt 读成一片 "�"。

跑法: pytest src-tauri/scripts/test_parse_file_encoding.py  (只用标准库)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _run(script: str, *args: str) -> dict:
    """模拟中文 Windows: 子进程的 stdout 编码是 GBK; 输出按 UTF-8 解 (Rust 端就是这么读的)。"""
    env = {**os.environ, "PYTHONIOENCODING": "gbk"}
    env.pop("PYTHONUTF8", None)
    res = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, env=env)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")[-600:]
    return json.loads(res.stdout.decode("utf-8"))


def test_gbk_编码不了的字不再崩(tmp_path: Path) -> None:
    f = tmp_path / "报价.txt"
    f.write_text("单价 ¥100, 已确认 👍", encoding="utf-8")
    r = _run("parse_file.py", str(f))
    assert "¥100" in r["preview_text"] and "👍" in r["preview_text"]
    assert "encoding" not in r["meta"], "UTF-8 文件不该多出编码说明"


def test_bm25_输出同样不崩(tmp_path: Path) -> None:
    sidecar = tmp_path / "x.parsed.txt"
    sidecar.write_text("第一段 价格 ¥100\n\n第二段 别的内容", encoding="utf-8")
    r = _run("attachment_bm25.py", "--text-path", str(sidecar), "--query", "价格")
    assert "¥100" in r["passages"][0]["text"]


def test_gbk_csv(tmp_path: Path) -> None:
    f = tmp_path / "工资.csv"
    f.write_bytes("姓名,金额\n张三,100\n李四,200\n".encode("gbk"))
    r = _run("parse_file.py", str(f))
    assert "张三" in r["preview_text"]
    assert r["meta"]["encoding"] == "gb18030"
    assert "encoding='gb18030'" in r["preview_text"], "LLM 读完整数据时要知道编码"


def test_gbk_txt(tmp_path: Path) -> None:
    f = tmp_path / "通知.txt"
    f.write_bytes("关于国庆节放假的通知".encode("gbk"))
    r = _run("parse_file.py", str(f))
    assert r["preview_text"] == "关于国庆节放假的通知"
    assert r["meta"]["encoding"] == "gb18030"


def test_记事本_unicode_即_utf16(tmp_path: Path) -> None:
    f = tmp_path / "备忘.txt"
    f.write_bytes("会议纪要".encode("utf-16"))  # 带 BOM
    r = _run("parse_file.py", str(f))
    assert r["preview_text"] == "会议纪要"
    assert r["meta"]["encoding"] == "utf-16"


def test_utf8_bom_csv_不当成别的编码(tmp_path: Path) -> None:
    f = tmp_path / "a.csv"
    f.write_bytes("﻿列,值\n甲,1\n".encode("utf-8"))
    r = _run("parse_file.py", str(f))
    assert "甲" in r["preview_text"] and "encoding" not in r["meta"]
