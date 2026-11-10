"""本地搜索冒烟 (11/10): 用安装包里的 Python 和 local-search 源码, 真索引一遍 Word / PPT / Excel / 中文图片。

在 Windows 构建里由 smoke-windows-runtime.ps1 调 (packaged hermes venv 的 python)。
  --require-ocr   这台机器装了中文识字 (工作流里装了 Language.OCR zh-CN): 图片里的中文必须认出来
  不带           图片必须被记成 needs_ocr (没法识别时要明说, 不能静默丢掉)

用法: python smoke_local_search.py --source <edge/local-search/src> [--require-ocr]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"LOCAL SEARCH SMOKE FAILED: {msg}", flush=True)
    sys.exit(1)


def _cjk_font() -> str | None:
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simhei.ttf",
              "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
              "/System/Library/Fonts/Hiragino Sans GB.ttc"):
        if os.path.exists(p):
            return p
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--require-ocr", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, a.source)

    work = Path(tempfile.mkdtemp(prefix="catfish-ls-smoke-"))
    root = work / "docs"
    root.mkdir()

    import docx
    import openpyxl
    import pptx
    from pptx.util import Inches
    from PIL import Image, ImageDraw, ImageFont

    d = docx.Document()
    d.add_paragraph("任命书 WORDSMOKE")
    d.save(str(root / "任命书.docx"))
    prs = pptx.Presentation()
    g = prs.slides.add_slide(prs.slide_layouts[6]).shapes.add_group_shape()
    g.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = "资质汇报 PPTSMOKE"
    prs.save(str(root / "汇报.pptx"))
    wb = openpyxl.Workbook()
    wb.active.append(["台账 XLSXSMOKE"])
    wb.save(str(root / "台账.xlsx"))
    font = _cjk_font()
    if font is None:
        print("WARNING: 这台机器没有中文字体, 造不了测试图片 —— 识字这一项没测", flush=True)
    else:
        img = Image.new("RGB", (1000, 220), "white")
        ImageDraw.Draw(img).text((30, 70), "营业执照副本 统一社会信用代码", fill="black",
                                 font=ImageFont.truetype(font, 56))
        img.save(root / "执照.png")

    from catfish_search import indexer
    from catfish_search.config import SearchConfig

    indexer.DB_FILE = work / "search.db"
    cfg = SearchConfig(include=[root], exclude=[], file_types={".docx", ".pptx", ".xlsx", ".png"},
                       max_file_size_mb=20)
    stats = indexer.run_index(cfg)
    print("stats:", {k: v for k, v in stats.items() if k != "per_root"}, flush=True)

    c = sqlite3.connect(indexer.DB_FILE)
    docs = {Path(p).name: t for p, t in c.execute("SELECT path, content FROM documents")}
    skipped = {Path(p).name: r for p, r in c.execute("SELECT path, reason FROM skipped_files")}
    c.close()
    for name, marker in (("任命书.docx", "WORDSMOKE"), ("汇报.pptx", "PPTSMOKE"), ("台账.xlsx", "XLSXSMOKE")):
        if marker not in docs.get(name, ""):
            _fail(f"{name} 没进索引 (docs={list(docs)}, skipped={skipped})")
    print("documents ok: docx / pptx (组合里的字) / xlsx", flush=True)

    text = docs.get("执照.png")
    if font is None:
        print("local search smoke passed (documents only)", flush=True)
        return
    if a.require_ocr:
        if not text or "营业执照" not in text:
            _fail(f"装了中文识字, 图片却没认出来: text={text!r} skipped={skipped}")
        print(f"ocr ok: {text!r}", flush=True)
    else:
        if text is not None or skipped.get("执照.png") != "needs_ocr":
            _fail(f"没装中文识字时图片应记成 needs_ocr: text={text!r} skipped={skipped}")
        print("ocr unavailable reported as needs_ocr", flush=True)
    print("local search smoke passed", flush=True)


if __name__ == "__main__":
    main()
