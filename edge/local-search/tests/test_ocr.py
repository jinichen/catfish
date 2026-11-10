"""11/10: 图片 / 扫描版 PDF 本机识字后进索引。

用一个假的识字程序 (跟 catfish-ocr --stdin 同样的行协议) 跑通流程, Linux CI 上也能跑;
macOS 上另外拿真的 catfish-ocr 认一张中文图。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

from catfish_search import config as cfgmod
from catfish_search import indexer, ocr
from catfish_search.config import SearchConfig

FAKE = textwrap.dedent('''
    import json, sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    for line in sys.stdin:
        p = line.strip()
        if not p:
            continue
        if mode == "no_zh":
            print(json.dumps({"path": p, "ok": False, "error": "no_zh_ocr"}), flush=True)
        elif mode.startswith("empty:"):
            with open(mode[6:], "a") as f:
                f.write(p + "\\n")
            print(json.dumps({"path": p, "ok": True, "text": ""}), flush=True)
        else:
            print(json.dumps({"path": p, "ok": True, "text": "识别出的文字 OCRTEXT 第一行"}), flush=True)
''')


@pytest.fixture
def fake_ocr(tmp_path, monkeypatch):
    script = tmp_path / "fake_ocr.py"
    script.write_text(FAKE, encoding="utf-8")

    def use(mode="ok"):
        monkeypatch.setattr(ocr, "_command", lambda: [sys.executable, str(script), mode])
        monkeypatch.setattr(ocr, "_RESIDENT", ocr._Resident())
    use()
    return use


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "DB_FILE", tmp_path / "search.db")
    return tmp_path / "search.db"


def _cfg(root: Path) -> SearchConfig:
    return SearchConfig(include=[root], exclude=[], file_types={".png", ".jpg", ".pdf", ".md"}, max_file_size_mb=20)


def _rows(db, sql, *a):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def _png(p: Path) -> Path:
    # 1x1 的合法 PNG (假识字程序不看内容)
    p.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))
    return p


def _blank_pdf(p: Path, pages: int = 2) -> Path:
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(612, 792)
    doc.save(str(p))
    return p


def test_图片识字后进索引(db, tmp_path, fake_ocr):
    root = tmp_path / "root"
    root.mkdir()
    _png(root / "截图.png")
    stats = indexer.run_index(_cfg(root))
    assert stats["indexed"] == 1
    content = _rows(db, "SELECT content FROM documents")[0][0]
    assert "OCRTEXT" in content


def test_扫描版pdf没字的页识字(db, tmp_path, fake_ocr):
    pytest.importorskip("pypdfium2")
    root = tmp_path / "root"
    root.mkdir()
    _blank_pdf(root / "盖章合同.pdf", pages=3)
    indexer.run_index(_cfg(root))
    content = _rows(db, "SELECT content FROM documents")[0][0]
    assert content.count("OCRTEXT") == 3


def test_本机没法识字_记成needs_ocr_不一张张再试(db, tmp_path, fake_ocr):
    fake_ocr("no_zh")
    root = tmp_path / "root"
    root.mkdir()
    for i in range(3):
        _png(root / f"图{i}.png")
    indexer.run_index(_cfg(root))
    assert _rows(db, "SELECT DISTINCT reason FROM skipped_files") == [("needs_ocr",)]
    assert _rows(db, "SELECT count(*) FROM skipped_files") == [(3,)]
    assert ocr._RESIDENT._dead_reason == "no_zh_ocr"


def test_没字的图片_没改过就不再识别(db, tmp_path, fake_ocr):
    calls = tmp_path / "calls.txt"
    fake_ocr(f"empty:{calls}")
    root = tmp_path / "root"
    root.mkdir()
    img = _png(root / "视频帧.png")
    first = indexer.run_index(_cfg(root))
    second = indexer.run_index(_cfg(root))
    assert first["failed"] == second["failed"] == 1
    assert len(calls.read_text().splitlines()) == 1  # 第二遍没再识别
    import time as _t
    os.utime(img, (_t.time() + 10, _t.time() + 10))  # 改过了 → 重新识别
    indexer.run_index(_cfg(root))
    assert len(calls.read_text().splitlines()) == 2


def test_没有识字程序_也记needs_ocr(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "_command", lambda: None)
    monkeypatch.setattr(ocr, "_RESIDENT", ocr._Resident())
    root = tmp_path / "root"
    root.mkdir()
    _png(root / "a.png")
    indexer.run_index(_cfg(root))
    assert _rows(db, "SELECT reason FROM skipped_files") == [("needs_ocr",)]


def test_识别出的字太少_不算(tmp_path, fake_ocr, monkeypatch):
    assert not ocr.meaningful("1 2")
    assert ocr.meaningful("营业执照副本")


def test_windows_汉字间的空格去掉():
    assert ocr._CJK_GAP.sub("", "北 京 福 富 软 件 Co Ltd 第 一 章") == "北京福富软件 Co Ltd 第一章"


def test_配置里自动带上图片类型_可以关掉(tmp_path, monkeypatch):
    f = tmp_path / "search-scope.yaml"
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", f)
    monkeypatch.setattr(cfgmod, "ensure_config_exists", lambda: None)
    monkeypatch.setattr(cfgmod, "_apply_migrations", lambda: None)
    monkeypatch.setattr(cfgmod, "_prune_empty_legacy_output", lambda: None)
    f.write_text("include: []\nfile_types:\n- .md\n", encoding="utf-8")
    assert {".png", ".jpg", ".heic", ".md"} <= cfgmod.load_config().file_types
    f.write_text("include: []\nocr: false\nfile_types:\n- .md\n", encoding="utf-8")
    assert cfgmod.load_config().file_types == {".md"}


REAL_HELPER = Path(__file__).resolve().parents[2] / "companion-app" / "src-tauri" / "swift" / "catfish-ocr"


@pytest.mark.skipif(sys.platform != "darwin" or not REAL_HELPER.exists(), reason="只在 macOS 上用真的 catfish-ocr 跑")
def test_macos_真识字_认得中文(tmp_path, monkeypatch):
    pil = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw, ImageFont
    font_path = next((p for p in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc")
                      if os.path.exists(p)), None)
    if font_path is None:
        pytest.skip("没有中文字体")
    img = pil.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((30, 60), "营业执照副本 统一社会信用代码", fill="black", font=ImageFont.truetype(font_path, 48))
    p = tmp_path / "zh.png"
    img.save(p)
    monkeypatch.setenv("CATFISH_OCR_BIN", str(REAL_HELPER))
    monkeypatch.setattr(ocr, "_RESIDENT", ocr._Resident())
    text = ocr.ocr_image(p)
    assert "营业执照" in text and "信用代码" in text
