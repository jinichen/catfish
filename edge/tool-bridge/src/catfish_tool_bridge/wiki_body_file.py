"""知识库条目正文从本机文件读 (10/23)。

鸿波 10/23: 让鲶鱼把一份 27 页规划的全文转录存进知识库。转录早已落盘
(~/.catfish/outputs/…/全文转录.md, 41KB), 可建条目时模型还得把这 4 万字原样写进
catfish_wiki_create 的 body —— 一条工具调用几万 token:
  · Mac 上 JSON 里冒出未转义的引号, 重发几次把对话撑爆;
  · Windows 上写到一半撞输出上限被截, 续写又从头写 → 「kept its response going」。
所以 body_file / content_file: 给路径, 这里读。模型只需要写一行路径。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

#: 文本类才读; 这个大小以上不当正文 (该走 catfish_wiki_ingest 归档原件)
_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".csv", ".json", ".yaml", ".yml", ".html", ".htm"}
_MAX_BYTES = 2 * 1024 * 1024


def read_body_file(raw: str) -> tuple[str | None, str | None]:
    """返回 (内容, 错误)。只读当前用户目录 / 临时目录下的文本文件。"""
    path = Path(str(raw).strip()).expanduser()
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, f"body_file 不存在: {raw}"
    roots = [Path.home().resolve(), Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()]
    if not any(path == r or r in path.parents for r in roots):
        return None, f"body_file 只能在用户目录或临时目录下: {path}"
    if not path.is_file():
        return None, f"body_file 不是文件: {path}"
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return None, f"body_file 只收文本文件 (md / txt 等), 收到 {path.suffix or '无扩展名'}; 原件归档用 catfish_wiki_ingest"
    if path.stat().st_size > _MAX_BYTES:
        return None, f"body_file 超过 2MB ({path.stat().st_size // 1024} KB), 先摘要或拆成几个条目"
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return None, f"body_file 不是 UTF-8 文本: {path}"
