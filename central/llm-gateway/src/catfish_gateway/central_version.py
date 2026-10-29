"""中央端版本号 (10/30 鸿波「页面上无法确定中央端的版本号」)。

版本号由 scripts/central_version.sh 按 日期 + 六个镜像源码的内容哈希 算出, 写进 central/VERSION
和两份 docker-compose (镜像 tag + 网关的 CATFISH_CENTRAL_VERSION 环境变量)。六个镜像同一个号。

取值顺序:
  1. env CATFISH_CENTRAL_VERSION —— docker 部署 (compose 里写死, 跟镜像 tag 同一个号)
  2. 源码树里的 central/VERSION —— 开发机直接从源码跑网关时 (launchd), 读的是当前检出的版本文件
  3. 都没有 → "unknown"
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_FORMAT = re.compile(r"^\d{8}-[0-9a-f]{8}$")
# catfish_gateway → src → llm-gateway → central
_SOURCE_FILE = Path(__file__).resolve().parents[3] / "VERSION"


def central_version() -> dict[str, str]:
    env = os.environ.get("CATFISH_CENTRAL_VERSION", "").strip()
    if env:
        return {"version": env, "source": "env"}
    try:
        text = _SOURCE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if _FORMAT.match(text):
        return {"version": text, "source": "source-tree"}
    return {"version": "unknown", "source": "none"}
