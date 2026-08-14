"""catfish_wiki_ingest —— 员工显式入库 wiki。

BL-TOOL-SPLIT 8/15: 从 catfish_tools.py 抽出来 (1009 行超限)。纯搬迁,
逻辑一行未改。整块只对外暴露 wiki_ingest 一个函数。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

# ============================================================
# P3.5.177 (7/6 鸿波军规审判): catfish_wiki_ingest — 员工显式入库 wiki
#
# 严格 root cause (P3.5.175 audit): 老 P16 (6/5) 严格 fire-and-forget auto-ingest
# → 员工每次 send 严格自动写 wiki/raw/sources/ → 严格 sync_turn 3b 严格自动抽
# entity/concept. 员工 7/6 反馈"不是所有文档都要进知识库".
#
# fix: 严格删 ChatInput.tsx:200-205 auto-ingest for-loop + 加本 tool + SOUL
# guidance. LLM 严格识别员工"入库/存 wiki/记住这个文档" 语义 → 严格调本 tool.
#
# 严格 handler 逻辑镜像 wiki_write.rs:602 wiki_ingest_source Rust command:
#   1. 严格 sidecar 优先 (binary xlsx/pdf 严格拿不到原文, sidecar 已 parse)
#   2. 严格 fallback 读 kept_path (小 text file)
#   3. 严格 slugify filename + ts prefix 严格文件名
#   4. 严格 frontmatter (type/filename/kind/uploaded/kept_path/bytes/source)
#   5. 严格写到 ~/.catfish/wiki/raw/sources/<ts>-<slug>.md
#   6. 严格返 rel_path + 说明"sync_turn 3b 严格 24h 内抽 entity/concept"
# ============================================================
def wiki_ingest(args: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 显式调用: 员工要求存到 wiki 知识库时才调."""
    kept_path = (args.get("kept_path") or "").strip()
    parsed_text_path = (args.get("parsed_text_path") or "").strip()
    filename = (args.get("filename") or "").strip()
    reason = (args.get("reason") or "").strip()

    if not kept_path:
        return {"type": "error", "error": "kept_path 必填 (从 chat attachment.keptPath 拿)"}
    if not filename:
        return {"type": "error", "error": "filename 必填 (从 chat attachment.name 拿)"}

    # 严格 sidecar 优先 (binary file: xlsx/pdf/word 严格拿不到原文)
    full_text = ""
    src_for_read = ""
    if parsed_text_path:
        try:
            p = Path(parsed_text_path)
            if p.is_file():
                full_text = p.read_text(encoding="utf-8", errors="replace")
                src_for_read = str(p)
        except OSError:
            pass

    # 严格 fallback: 读 kept_path (小 text file: csv/txt/md)
    if not full_text.strip():
        try:
            p = Path(kept_path)
            if p.is_file():
                full_text = p.read_text(encoding="utf-8", errors="replace")
                src_for_read = str(p)
        except (OSError, UnicodeDecodeError):
            pass

    if not full_text.strip():
        return {
            "type": "error",
            "error": (
                f"严格拿不到文本内容 — kept_path={kept_path} parsed_text_path={parsed_text_path} "
                "严格都读不出. 员工可能上传 binary file 但 parse_file_from_b64 严格失败, "
                "或严格文件已被清理. 员工重新上传附件, 再让我入库."
            ),
        }

    # 严格 catfish home + sources dir
    catfish_home = Path.home() / ".catfish"
    sources_dir = catfish_home / "wiki" / "raw" / "sources"
    try:
        sources_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"type": "error", "error": f"建 sources 目录失败: {e}"}

    # 严格 slug + ts (类 wiki_write.rs slugify 逻辑, 但 Python 简化版)
    ts = int(time.time())
    stem = Path(filename).stem or "upload"
    # 严格 slug: 保留 unicode word chars + '-' + '_', 其他严格替 '-'
    slug_chars = []
    for c in stem[:60]:
        if c.isalnum() or c in ("-", "_"):
            slug_chars.append(c)
        elif c.isspace():
            slug_chars.append("-")
    slug = "".join(slug_chars).strip("-").strip("_") or "upload"
    target_name = f"{ts}-{slug}.md"
    target_path = sources_dir / target_name

    # 严格 frontmatter + body (镜像 wiki_write.rs:648-661 严格 Rust format)
    today = time.strftime("%Y-%m-%d")
    bytes_n = len(full_text.encode("utf-8"))
    safe_filename = filename.replace("\n", " ").replace("\r", " ")
    safe_kept = kept_path.replace("\n", " ").replace("\r", " ")
    # 严格猜 kind (类 wiki_write.rs kind 参数): 严格 by extension
    ext = Path(filename).suffix.lower().lstrip(".")
    kind_map = {
        "pdf": "pdf",
        "docx": "word", "doc": "word",
        "xlsx": "excel", "xlsm": "excel", "xls": "excel",
        "csv": "csv",
        "txt": "text", "md": "text", "markdown": "text",
    }
    kind = kind_map.get(ext, "text")

    content = (
        "---\n"
        "type: source\n"
        f"filename: {safe_filename}\n"
        f"kind: {kind}\n"
        f"uploaded: {today}\n"
        f"kept_path: {safe_kept}\n"
        f"bytes: {bytes_n}\n"
        "source: upload\n"
    )
    if reason:
        # 严格 reason 严格员工可读, 严格 yaml-safe (转义引号)
        safe_reason = reason.replace('"', "'").replace("\n", " ")[:500]
        content += f'reason: "{safe_reason}"\n'
    content += (
        "---\n\n"
        f"# Upload: {safe_filename}\n\n"
        f"{full_text}\n"
    )

    try:
        target_path.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"type": "error", "error": f"写 {target_name} 失败: {e}"}

    return {
        "type": "success",
        "message": (
            f"✓ 已入库 wiki 知识库 pipeline: wiki/raw/sources/{target_name}.\n"
            "sync_turn 3b 严格后台会扫这个文件 → LLM 严格抽 entity/concept → "
            "wiki/entities/ + wiki/concepts/ (通常 24h 内, 或员工继续 chat 立即触发)."
        ),
        "rel_path": f"wiki/raw/sources/{target_name}",
        "bytes": bytes_n,
    }
