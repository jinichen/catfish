"""BL-ADVISOR-IO (5/21 Phase 7 第 3 步): 6 个 advisor tool 共用的 IO helper.

设计稿 §6 + §4. 跟 Companion 的 drafts.rs / decisions.rs 一致:
  - 草稿: ~/.catfish/outputs/<YYYY-MM-DD>/<filename>.md
  - 决策: ~/.catfish/decisions.jsonl

抽出来防 6 个 tool 各自写一遍路径逻辑 + safe_filename 检查.

跟 5/17 BL-CENTRAL-EDGE 一致: 全部读写员工本机 ~/.catfish/, 不出端.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.advisor_io")


def catfish_dir() -> Path:
    """`~/.catfish/`, 不存在自动建."""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(catfish_home).expanduser() if catfish_home else Path.home() / ".catfish"
    base.mkdir(parents=True, exist_ok=True)
    return base


def today_outputs_dir() -> Path:
    """`~/.catfish/outputs/<YYYY-MM-DD>/`, 自动建."""
    date = datetime.now().strftime("%Y-%m-%d")
    d = catfish_dir() / "outputs" / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_filename(filename: str) -> str:
    """Path-traversal 检查. 跟 drafts.rs::safe_filename 同等."""
    if not filename or len(filename) > 200:
        raise ValueError(f"filename 长度非法: {len(filename)}")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(f"filename 含非法字符: {filename}")
    return filename


def save_draft(filename: str, content: str) -> str:
    """写草稿到 outputs/<today>/<filename>. 原子写 (tmp + rename). 返绝对路径."""
    fname = safe_filename(filename)
    target = today_outputs_dir() / fname
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    logger.info("[advisor_io] save_draft → %s (%d bytes)", target, len(content))
    return str(target)


def decisions_path() -> Path:
    return catfish_dir() / "decisions.jsonl"


def load_decisions() -> list[dict[str, Any]]:
    """读全部决策记录. 坏行跳过, 不挂."""
    path = decisions_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as e:
        logger.warning("[advisor_io] load_decisions failed: %s", e)
    return out
