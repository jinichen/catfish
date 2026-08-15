#!/usr/bin/env python3
"""文件解析 helper — preview-only mode (五一 sprint 5/5 重构).

# 设计 (5/5 鸿波报"截断了不能用" 后大改)

旧设计 (50K 字符截断后塞 prompt) 不 scalable: 任何上限对真实业务数据都不够.
12 个月 Excel 一截就只看到前 3 个月.

**新设计**: parse_file 只输出 preview (~5K 字), 原文件永远保留, LLM 调 execute_code
跑 pandas / openpyxl / pypdfium2 读完整数据.

输出 schema:
    {
        "filename": "<原文件名>",
        "ext": ".xlsx",
        "kind": "excel" | "pdf" | "word" | "csv" | "text",
        "preview_text": "<sheet 列表 / 列头 / 前 N 行 / 总行数 / ...>",
        "preview_chars": 4523,                        # 给 UI chip 显示用
        "meta": {                                     # 结构化, LLM 拼 execute_code 用
            "sheets": ["1月", "2月", ...],            # excel 才有
            "row_counts": {"1月": 31, "2月": 28},     # excel 才有
            "page_count": 12,                         # pdf 才有
            "total_rows": 365,                        # csv 才有
            ...
        },
        "kept_path_hint": "<员工的 absolute file path, Rust 端会替换>"
    }
    # 错误: {"error": "..."}

行为约定:
- Excel: 每 sheet 列头 + 前 20 行 + 总行数 (跨 sheet 统计)
- PDF: 前 5 页全文 + 总页数
- Word: 前 30 段
- CSV: 列头 + 前 30 行 + 总行数
- TXT/MD/LOG: 前 5K 字 + 总字数

支持格式 (跟原来一致):
- .pdf   pypdfium2
- .xlsx / .xls  openpyxl
- .docx  python-docx
- .csv   csv 标准库
- .txt / .md / .markdown / .log  直接读 utf-8
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

# Preview 上限 (字符数). 全格式默认 5000, 给 LLM 看个梗概就够, 不要塞数据.
# ── 共用常量 + _truncate (8/15 搬去 parse_file_common.py) ──────
#
# 抽出去的直接原因: 5/21 拆 parse_file_audio.py 时是**照抄**了一份过去,
# 三个月后两份 _truncate 的截断提示已经一中一英各飘一边。这次要再抽 PDF /
# Office 出去, 不立个共用层就是第四份第五份。详见 parse_file_common.py。
#
# 这里 re-export 是为了 `pf._truncate` / `pf.PREVIEW_ROWS` 这些老写法不破。
# 全是不可变常量和纯函数, 快照语义安全。
from parse_file_common import (  # noqa: F401
    PREVIEW_MAX_CHARS,
    PREVIEW_PAGES,
    PREVIEW_PARAS,
    PREVIEW_ROWS,
    _truncate,
)

# ── 各格式 parser (8/15 按依赖的第三方库分了两个模块) ──────────
#
#   parse_file_office.py  Excel / xls / pptx / docx —— 都靠一个经常没装的
#                         第三方库, 形状一样 (函数体内 import + ImportError
#                         转成人话)
#   parse_file_pdf.py     PDF preview + anchor 表格识别 (那段启发式 129 行,
#                         只有 parse_pdf_preview 一个调用者)
#
# json / csv / text / video 留在本文件 —— 只用标准库, 加起来不到 120 行。
from parse_file_office import (  # noqa: F401
    _parse_xls_preview,
    parse_docx_preview,
    parse_excel_preview,
    parse_pptx_preview,
)
from parse_file_pdf import (  # noqa: F401
    ANCHOR_PATTERNS,
    MIN_ANCHORS,
    SUB_PATTERNS,
    _detect_anchor_table,
    parse_pdf_preview,
)




# ============================================================
# JSON — P3.3.21 (6/11): 比纯 text 多 schema 嗅探
# ============================================================

def parse_json_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """JSON: 嗅 top-level type (dict/list) + 前 N 项预览."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"JSON 解析失败: {e}")

    parts: list[str] = []
    meta: dict[str, Any] = {}
    if isinstance(data, dict):
        keys = list(data.keys())
        meta["top_type"] = "object"
        meta["top_keys"] = keys[:50]
        meta["top_key_count"] = len(keys)
        parts.append(f"## JSON object  ({len(keys)} 个 top-level key)")
        parts.append(f"keys: {', '.join(repr(k) for k in keys[:30])}")
        if len(keys) > 30:
            parts.append(f"  (... 还有 {len(keys) - 30} 个 key)")
        parts.append("")
        # 整体 dump (截 ~5K 字)
        parts.append(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, list):
        meta["top_type"] = "array"
        meta["top_length"] = len(data)
        parts.append(f"## JSON array  (共 {len(data)} 项)")
        max_items = min(PREVIEW_ROWS, len(data))
        for i in range(max_items):
            parts.append(f"### [{i}]")
            parts.append(json.dumps(data[i], ensure_ascii=False, indent=2))
        if len(data) > max_items:
            parts.append(f"  (... 还有 {len(data) - max_items} 项未显示, 用 execute_code 读完整)")
    else:
        meta["top_type"] = type(data).__name__
        parts.append(f"## JSON ({type(data).__name__})")
        parts.append(repr(data))

    text = _truncate("\n".join(parts))
    return text, meta






# ============================================================
# CSV
# ============================================================

def parse_csv_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """CSV 列头 + 前 N 行 + 总行数."""
    # 先 count rows (一次过, 不读 cell)
    with path.open(encoding="utf-8-sig", newline="") as f:
        total_rows = sum(1 for _ in f)

    parts: list[str] = [f"## CSV · 共 {total_rows} 行"]
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i >= PREVIEW_ROWS + 1:
                break
            parts.append("\t".join(row))

    if total_rows > PREVIEW_ROWS + 1:
        parts.append(
            f"\n[... 还有 {total_rows - PREVIEW_ROWS - 1} 行未显示, "
            "用 execute_code + pandas 读完整]"
        )

    text = _truncate("\n".join(parts))
    meta: dict[str, Any] = {
        "total_rows": total_rows,
    }
    return text, meta


# ============================================================
# Audio (mp3/wav/m4a/flac) — BL-I4 (5/8 ship)
# 5/21 拆: audio 解析 (whisper.cpp) 抽到 parse_file_audio.py
from parse_file_audio import (  # noqa: F401
    _find_executable,
    _whisper_model_path,
    _transcribe_audio_to_text,
    parse_audio_preview,
)


# ============================================================
# Video (mp4/mov/m4v/mkv) — BL-I3.1 (5/8 ship, 跟 BL-I4 共享)
# ============================================================
#
# 用例: 会议录像 → 抽音轨 → 转写 → 提取要点 / 待办 / 关键决议.
# 央企痛点: 会议爆炸多 (周会 / 月度复盘 / 立项 / 评审 / 党建), 录了几乎不看回放.
#
# 实现: ffmpeg -vn 抽音轨 → 复用 _transcribe_audio_to_text.
# 不做 vision 帧抽取 (BL-I3.2 推后 — vision 调用费 + "全本地"故事冲突).


def parse_video_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """视频文件 (mp4/mov/m4v/mkv) 抽音轨转写 preview.

    BL-I3.1 (5/8): 跟 BL-I4 共用 _transcribe_audio_to_text (内部已经有 -vn 跳视频流).
    用例: 会议录像 → 关键决议 / 待办.
    """
    transcript, raw_meta = _transcribe_audio_to_text(path, lang="zh")

    if not transcript:
        return (
            f"## 视频 · {raw_meta.get('duration_sec', 0)} 秒\n"
            "(没识别出文字, 视频可能没音轨 / 静音 / 全是音乐无对话)",
            {**raw_meta, "video_no_speech": True},
        )

    parts = [
        f"## 视频音轨转写 · {raw_meta.get('duration_sec', 0)} 秒 · "
        f"语言 {raw_meta.get('language', 'zh')} · {raw_meta.get('transcript_chars', 0)} 字",
        "",
        "(注: 只抽了音轨转文本, 视觉帧 / 字幕 / 演示文稿没抽 — "
        "如要分析画面内容请说明)",
        "",
        _truncate(transcript),
    ]
    if len(transcript) > PREVIEW_MAX_CHARS:
        parts.append(
            f"\n[... 全文 {len(transcript)} 字, 用 BM25 检索相关段, "
            "或 execute_code 读 keptPath 完整文件]"
        )
    return "\n".join(parts), raw_meta


# ============================================================
# Text (txt/md/log)
# ============================================================

def parse_text_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """纯文本前 N 字 + 总字数."""
    full = path.read_text(encoding="utf-8", errors="replace")
    total = len(full)
    if total <= PREVIEW_MAX_CHARS:
        return full, {"total_chars": total}

    head = full[:PREVIEW_MAX_CHARS]
    text = (
        f"## 文本 · 共 {total} 字 (显示前 {PREVIEW_MAX_CHARS} 字)\n\n"
        f"{head}\n"
        f"\n[... 还有 {total - PREVIEW_MAX_CHARS} 字未显示, "
        "用 execute_code 读完整 (open + read)]"
    )
    return text, {"total_chars": total}


# ============================================================
# BL-L26 (5/7): 抽全文 (供 BM25 sidecar 用)
# ============================================================
#
# preview 是给 LLM 一眼看梗概用的, 大文件 (>=50KB) BM25 检索需要全文.
# 这里独立一套 extract_full_text 函数, 不动现有 parser, 也不让 preview 路径
# 因 BM25 而变.
#
# 哪些格式做 sidecar:
#   PDF / Word / Text (md/log/markdown/txt) — 纯文本, BM25 直接打分有效
#   CSV / Excel — 表格数据, BM25 不太搭 (LLM 应走 pandas.read_csv 路径); 跳过.
#
# 为啥不直接复用 parser:
#   1. parser 早就 _truncate 过, 拿不回全文
#   2. preview 含 markup ("## Sheet: xxx"), 索引这些 markup 没意义
#   3. BL-L26 是新特性, 隔离实现 / 测试容易

# 触发 sidecar 的最小字节数 (utf-8 bytes, 不是 char)
SIDECAR_MIN_BYTES = 50 * 1024


def extract_full_text_pdf(path: Path) -> str:
    """跨页全文."""
    import pypdfium2 as pdfium  # noqa: PLC0415
    pdf = pdfium.PdfDocument(str(path))
    pieces: list[str] = []
    for i in range(len(pdf)):
        page = pdf[i]
        textpage = page.get_textpage()
        chunk = textpage.get_text_range() or ""
        if chunk:
            pieces.append(chunk)
    return "\n\n".join(pieces)


def extract_full_text_docx(path: Path) -> str:
    """全段 + 表格扁平化."""
    from docx import Document  # noqa: PLC0415
    doc = Document(str(path))
    pieces: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:
        for row in t.rows:
            cells = [c.text for c in row.cells if c.text.strip()]
            if cells:
                pieces.append(" | ".join(cells))
    return "\n\n".join(pieces)


def extract_full_text_plain(path: Path) -> str:
    """txt / md / markdown / log: 全文读出."""
    return path.read_text(encoding="utf-8", errors="replace")


def extract_full_text_audio(path: Path) -> str:
    """BL-I4 全文抽 — 复用 _transcribe_audio_to_text. 已在 parser 跑过, 这里再跑一次.
    优化空间: parser 时可缓存到 sidecar (但当前架构 parser 不知道 BM25 阈值).
    """
    transcript, _meta = _transcribe_audio_to_text(path, lang="zh")
    return transcript


_FULL_TEXT_EXTRACTORS = {
    "pdf": extract_full_text_pdf,
    "word": extract_full_text_docx,
    "text": extract_full_text_plain,
    # BL-I4 / BL-I3.1 (5/8): audio + video 全文走 transcript.
    # 大会议录音 (1 小时 ≈ 50KB+ 转写) 自动走 BL-L26 BM25 路径.
    "audio": extract_full_text_audio,
    "video": extract_full_text_audio,  # 视频 -vn 抽音轨, 全文等同 audio
}


def maybe_write_sidecar(path: Path, kind: str) -> str | None:
    """如果 (1) kind 支持全文抽取 (2) 全文 utf-8 字节数 ≥ SIDECAR_MIN_BYTES,
    把全文写到 `<path>.parsed.txt`, 返 sidecar 路径; 否则返 None.

    异常: 抽全文失败 (PDF 损坏 / 编码错) 不抛, 静默返 None — 大文件 fallback 走 preview.
    """
    extractor = _FULL_TEXT_EXTRACTORS.get(kind)
    if extractor is None:
        return None
    try:
        text = extractor(path)
    except Exception as e:
        # 抽全文失败 — preview 路径正常, 只是没 BM25 加成
        print(f"[parse_file] 抽全文失败 (BM25 sidecar 跳过): {e}", file=sys.stderr)
        return None
    if not text:
        return None
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) < SIDECAR_MIN_BYTES:
        return None
    sidecar = path.with_suffix(path.suffix + ".parsed.txt")
    try:
        sidecar.write_bytes(encoded)
        return str(sidecar)
    except Exception as e:
        print(f"[parse_file] 写 sidecar 失败: {e}", file=sys.stderr)
        return None


# ============================================================
# 主 dispatcher
# ============================================================

PARSERS = {
    ".pdf": ("pdf", parse_pdf_preview),
    ".xlsx": ("excel", parse_excel_preview),
    ".xlsm": ("excel", parse_excel_preview),  # P3.3.21: macro 启用 xlsx, openpyxl 直接吃
    ".xls": ("excel", parse_excel_preview),   # P3.3.21: 老格式内部 dispatch 到 xlrd
    ".docx": ("word", parse_docx_preview),
    ".pptx": ("ppt", parse_pptx_preview),     # P3.3.21
    ".csv": ("csv", parse_csv_preview),
    ".json": ("json", parse_json_preview),    # P3.3.21
    ".txt": ("text", parse_text_preview),
    ".md": ("text", parse_text_preview),
    ".markdown": ("text", parse_text_preview),
    ".log": ("text", parse_text_preview),
    # BL-I4 (5/8): 音频文件转写
    ".mp3": ("audio", parse_audio_preview),
    ".wav": ("audio", parse_audio_preview),
    ".m4a": ("audio", parse_audio_preview),
    ".flac": ("audio", parse_audio_preview),
    ".aac": ("audio", parse_audio_preview),
    ".ogg": ("audio", parse_audio_preview),
    # BL-I3.1 (5/8): 视频抽音轨转写
    ".mp4": ("video", parse_video_preview),
    ".mov": ("video", parse_video_preview),
    ".m4v": ("video", parse_video_preview),
    ".mkv": ("video", parse_video_preview),
    ".webm": ("video", parse_video_preview),
}

# P3.3.21 (6/11): 老格式没纯 Python 支持的, 给友好错让员工另存为新格式.
#   .ppt / .doc binary 格式纯 Python 解很弱 (antiword/catdoc 是 CLI, libreoffice
#   convert 重量级), 不值得引依赖 — 一行提示比"装 antiword" 友好.
LEGACY_HINTS = {
    ".ppt": "老 .ppt (PowerPoint 97-2003) 不支持. 请 PowerPoint 打开 → 另存为 .pptx",
    ".doc": "老 .doc (Word 97-2003) 不支持. 请 Word 打开 → 另存为 .docx",
    ".rtf": "RTF 不支持. 请另存为 .docx 或 .txt",
}


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: parse_file.py <file>"}, ensure_ascii=False))
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 3
    if not path.is_file():
        print(json.dumps({"error": f"不是文件: {path}"}, ensure_ascii=False))
        return 3

    ext = path.suffix.lower()
    entry = PARSERS.get(ext)
    if entry is None:
        # P3.3.21 (6/11): 老格式 (.ppt/.doc/.rtf) 给具体迁移指引,
        #   比"不支持" 友好得多 — 员工知道下一步该按什么.
        if ext in LEGACY_HINTS:
            print(json.dumps({"error": LEGACY_HINTS[ext]}, ensure_ascii=False))
        else:
            print(json.dumps({
                "error": f"不支持 {ext}. 支持: {', '.join(sorted(PARSERS.keys()))}"
            }, ensure_ascii=False))
        return 4

    kind, parser = entry
    try:
        preview_text, meta = parser(path)
    except Exception as e:
        print(json.dumps(
            {"error": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        ))
        return 5

    result = {
        "filename": path.name,
        "ext": ext,
        "kind": kind,
        "preview_text": preview_text,
        "preview_chars": len(preview_text),
        "meta": meta,
    }

    # BL-L26 (5/7): 大文件 (≥50KB 全文) 写 sidecar 供 BM25 检索. 小文件返 None.
    sidecar_path = maybe_write_sidecar(path, kind)
    if sidecar_path is not None:
        result["parsed_text_path"] = sidecar_path

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
