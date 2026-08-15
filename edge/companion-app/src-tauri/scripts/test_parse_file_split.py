"""parse_file 系列拆分后的守卫 (8/15)。

# 病历

parse_file.py 872 行, 过了 CLAUDE.md §1 的 800 红线。拆成:

    parse_file_common.py   46  PREVIEW_* 常量 + _truncate (只依赖标准库)
    parse_file_office.py  244  Excel / xls / pptx / docx (都靠第三方库)
    parse_file_pdf.py     270  PDF preview + anchor 表格识别
    parse_file.py         450  json / csv / text / video + 分发表 + main

# 这个文件钉三件事

## 1. `_truncate` 只许有一份 —— 因为已经飘过一次了

5/21 抽 parse_file_audio.py 时是**照抄**了一份 PREVIEW_MAX_CHARS + _truncate
过去的。三个月后的 8/15 两份已经不一样:

    parse_file.py       "[... preview 截到 {limit} 字, 完整数据用 execute_code 读]"
    parse_file_audio.py "... [Audio transcript truncated at {limit} chars]"

一中一英。不报错, 只是员工看到的提示忽中忽英, 而且没人知道哪份算数。

8/15 抽 PDF / Office 的时候如果继续照抄, 就是第四份第五份。所以立了
parse_file_common.py, 并用下面这条钉住"别再抄"。

**audio 那份是已知例外**: 它的文案确实不一样, 看着是有意的。改用户可见的
文字不是重构该干的事, 所以留着, 但在白名单里显式列出来 —— 例外要是显式的,
不能是"忘了"。

## 2. 子模块不许反向 import parse_file

parse_file.py 是**当脚本跑**的 —— Rust 侧 file_parse.rs 用 subprocess 调
`python3 .../parse_file.py <path>`, 这时它的模块名是 `__main__`。子模块里再
`import parse_file` 会把同一份源码**再执行一遍**, 得到第二个 module 对象:
两份 PARSERS、两份常量, 各自自洽, 测试看不出来。

## 3. PARSERS 分发表不能漏格式

这张表是"哪些后缀能解析"的唯一真源。搬走 parser 的时候如果 import 漏一个,
表里那一项就是 NameError —— 但**只在员工真传那种文件时才炸**, 不是启动时。
所以这里对着一张显式的后缀名单核。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR))

import parse_file as pf  # noqa: E402

#: 拆分**前** PARSERS 支持的全部后缀 (23 个), 从拆分前那一版代码里读出来的。
#:
#: ⚠ 第一版这张表是我照着源码"看着敲"的, 漏了 .aac / .ogg / .webm 三个音频
#:   格式。测试当场红了。跟同一天在 identity users.py 上栽的是同一下 ——
#:   这种表只能从代码里取, 不能凭眼睛过一遍就敲。
EXPECTED_EXTS = {
    # 文档
    ".pdf", ".xlsx", ".xlsm", ".xls", ".docx", ".pptx",
    # 纯文本类
    ".csv", ".json", ".txt", ".md", ".markdown", ".log",
    # 音频 (BL-I4)
    ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg",
    # 视频 (BL-I3.1) —— .webm 走视频抽音轨
    ".mp4", ".mov", ".m4v", ".mkv", ".webm",
}

#: 拆出来的兄弟模块 (不含 audio —— 它是 5/21 那次拆的, 规矩不一样, 见下)
_SIBLINGS = ["parse_file_common.py", "parse_file_office.py", "parse_file_pdf.py"]


def test_PARSERS_覆盖的后缀没变():
    got = set(pf.PARSERS)
    assert got == EXPECTED_EXTS, (
        f"PARSERS 的后缀集合变了。\n"
        f"  多了: {sorted(got - EXPECTED_EXTS)}\n"
        f"  少了: {sorted(EXPECTED_EXTS - got)}\n"
        "少了 = 员工传这种文件会被当成不支持; 多了 = 请顺手更新这张名单。"
    )


def test_PARSERS_里的函数都真的能拿到():
    """搬 parser 的时候 import 漏一个, 表里那项就是坏的 —— 但只在员工真传那种
    文件时才炸, 不是启动时。这条把它提前到测试期。"""
    for ext, (kind, fn) in pf.PARSERS.items():
        assert callable(fn), f"{ext} → {kind} 的 parser 不可调用: {fn!r}"
        assert fn.__module__ in (
            "parse_file", "parse_file_office", "parse_file_pdf", "parse_file_audio"
        ), f"{ext} 的 parser 来自意外的模块 {fn.__module__}"


def test_全文抽取器也都在():
    for ext, fn in pf._FULL_TEXT_EXTRACTORS.items():
        assert callable(fn), f"{ext} 的全文抽取器不可调用: {fn!r}"


# ── _truncate 只许一份 ──────────────────────────────────────


def _defines_truncate() -> list[str]:
    out = []
    for p in sorted(_DIR.glob("parse_file*.py")):
        if p.name.startswith("test_"):
            continue
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if getattr(n, "name", None) == "_truncate":
                out.append(p.name)
    return out


def test_truncate_只有common和audio两份():
    """★★★ 防再抄第四份。

    audio 那份是**已知例外** (文案不同, 见模块 docstring)。除它以外,
    谁都该从 parse_file_common import。
    """
    ALLOWED = {"parse_file_common.py", "parse_file_audio.py"}
    got = set(_defines_truncate())
    extra = got - ALLOWED
    assert not extra, (
        f"这些文件又自己定义了一份 _truncate: {sorted(extra)}\n"
        "从 parse_file_common import。5/21 抄过一次, 三个月后两份的截断提示"
        "就一中一英飘开了 —— 别再来一次。"
    )
    assert "parse_file_common.py" in got, "parse_file_common.py 里没有 _truncate?"


def test_common只依赖标准库():
    """基础层反过来依赖谁都会成环。"""
    tree = ast.parse((_DIR / "parse_file_common.py").read_text(encoding="utf-8"))
    bad = [
        ast.unparse(n) for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        and "parse_file" in ast.unparse(n)
    ]
    assert not bad, f"parse_file_common 依赖了别的 parse_file 模块: {bad}"


@pytest.mark.parametrize("fname", _SIBLINGS)
def test_子模块不许反向import_parse_file(fname: str):
    """parse_file.py 当脚本跑时模块名是 __main__, 再 import 一次会拿到
    第二个 module 对象 —— 两份 PARSERS 两份常量, 各自自洽。"""
    tree = ast.parse((_DIR / fname).read_text(encoding="utf-8"))
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            bad += [a.name for a in n.names if a.name == "parse_file"]
        elif isinstance(n, ast.ImportFrom) and n.module == "parse_file":
            bad.append(f"from parse_file import {', '.join(a.name for a in n.names)}")
    assert not bad, (
        f"{fname} 反向 import 了 parse_file: {bad}\n"
        "要共用的东西沉到 parse_file_common.py。"
    )


@pytest.mark.parametrize("fname", ["parse_file.py"] + _SIBLINGS)
def test_都在红线以下(fname: str):
    n = len((_DIR / fname).read_text(encoding="utf-8").splitlines())
    assert n < 800, f"{fname} {n} 行, 过了 CLAUDE.md §1 的 800 红线"


# ── 真跑一遍 (Rust 侧就是这么调的) ─────────────────────────


def test_脚本模式能从别的cwd跑起来(tmp_path):
    """★★ Rust 侧 file_parse.rs 是 subprocess 调 `python3 <abs>/parse_file.py <file>`。

    兄弟模块能被 import 靠的是 Python 把**脚本所在目录**放进 sys.path[0],
    跟 cwd 无关。这条从一个完全无关的 cwd 跑, 钉住这个前提。

    (拆分前是单文件, 没有这个前提; 拆分后有了, 所以要有人守。)
    """
    f = tmp_path / "t.csv"
    f.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(_DIR / "parse_file.py"), str(f)],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert r.returncode == 0, f"退出码 {r.returncode}, stderr={r.stderr[:400]}"
    import json
    d = json.loads(r.stdout)
    assert d["kind"] == "csv" and d["ext"] == ".csv"
    assert "a" in d["preview_text"]
