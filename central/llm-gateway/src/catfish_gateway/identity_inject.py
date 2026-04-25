"""SOUL + Memory 自动注入 —— 让 Companion / 第三方 client 零配置获得鲶鱼人格。

设计哲学（与 network.py / report_upstream_reachability 一致）：
    "复杂度集中在 gateway 一层，客户端零负担"

读取顺序:
    1. ~/.hermes/SOUL.md             鲶鱼人格定义（员工已通过 catfish/edge/identity 装好）
    2. ~/.hermes/USER.md             用户级长期 memory (Hermes 自带写入)
    3. ~/.hermes/memories/*.md       Hermes 写入的 per-topic memory（如果有）

注入策略:
    - 客户端发的 messages 已含 system → **不动**（尊重 Hermes 等已自注入的 client）
    - 没 system → 把 SOUL + memory 拼成一个 system message 插到首位
    - HTTP header `X-Catfish-Skip-Identity: true` → 强制不注入（客户端 opt-out）

文件 IO 用 mtime 做缓存，SOUL.md 改了下次请求自动用新版（不需要重启 gateway）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.identity")


def _hermes_home() -> Path:
    """允许 env 覆盖（用于测试 / 容器化部署 / 多账号）。"""
    if env := os.environ.get("HERMES_HOME"):
        return Path(env).expanduser()
    return Path.home() / ".hermes"


# ─────────────────────────────────────────────
# 文件缓存
# ─────────────────────────────────────────────


class _FileCache:
    """简易 mtime-based 文件内容缓存。

    每次 read() 检查 mtime，没变就返回缓存；变了重读。
    SOUL.md 改了不需要重启 gateway，下次请求自动用新版。
    """

    def __init__(self) -> None:
        # path → (mtime, content)
        self._store: dict[str, tuple[float, str]] = {}

    def read(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            mtime = path.stat().st_mtime
            cached = self._store.get(str(path))
            if cached and cached[0] == mtime:
                return cached[1]
            content = path.read_text(encoding="utf-8")
            self._store[str(path)] = (mtime, content)
            return content
        except OSError as e:
            logger.warning("identity 读 %s 失败: %s", path, e)
            return ""

    def clear(self) -> None:
        """单测用。"""
        self._store.clear()


_cache = _FileCache()


# ─────────────────────────────────────────────
# 内容拼装
# ─────────────────────────────────────────────


def _read_soul() -> str:
    return _cache.read(_hermes_home() / "SOUL.md")


def _read_user_memory() -> str:
    return _cache.read(_hermes_home() / "USER.md")


def _read_memory_dir() -> str:
    """读 ~/.hermes/memories/ 下所有 .md 文件,按文件名排序拼接。"""
    mem_dir = _hermes_home() / "memories"
    if not mem_dir.is_dir():
        return ""
    parts = []
    for md_file in sorted(mem_dir.glob("*.md")):
        content = _cache.read(md_file).strip()
        if content:
            parts.append(f"## {md_file.stem}\n\n{content}")
    return "\n\n".join(parts)


def build_identity_content() -> str:
    """读 SOUL + USER memory + memories/, 拼成单个 system message 内容字符串。

    返回空字符串说明无任何身份内容（员工还没装 SOUL.md / Hermes 还没写过 memory）。
    """
    parts: list[str] = []

    soul = _read_soul().strip()
    if soul:
        parts.append(f"# Identity (SOUL)\n\n{soul}")

    memory_blocks: list[str] = []
    user_mem = _read_user_memory().strip()
    if user_mem:
        memory_blocks.append(user_mem)
    dir_mem = _read_memory_dir().strip()
    if dir_mem:
        memory_blocks.append(dir_mem)

    if memory_blocks:
        parts.append("# User Memory\n\n" + "\n\n".join(memory_blocks))

    return "\n\n---\n\n".join(parts)


# ─────────────────────────────────────────────
# 注入逻辑
# ─────────────────────────────────────────────


def has_system_message(messages: list[dict[str, Any]]) -> bool:
    """messages 里是否已含 role=system 的消息。"""
    return any(m.get("role") == "system" for m in messages or [])


def inject_identity_if_needed(
    messages: list[dict[str, Any]],
    *,
    skip: bool = False,
) -> list[dict[str, Any]]:
    """如果 messages 里没 system message 且 skip=False, 在前面插 SOUL+memory 作为 system。

    skip:
        通常由 HTTP header `X-Catfish-Skip-Identity: true` 触发。
        Hermes 这种已自注入 system 的客户端理论上不需要 skip
        (它发的 messages 第一条就是 system, 我们检测到就不动了)。
        skip 留给"故意不要 catfish 人格"的边缘场景。
    """
    if skip:
        return messages or []

    if has_system_message(messages):
        # 客户端有自己的 system prompt, 尊重它的意图
        return messages

    content = build_identity_content()
    if not content:
        # 员工还没装 SOUL.md / 没 memory, 静默跳过
        return messages or []

    return [{"role": "system", "content": content}] + (messages or [])


def header_skips_identity(headers) -> bool:
    """从 FastAPI request.headers 判断客户端要不要注入。

    支持多种大小写:
        X-Catfish-Skip-Identity / x-catfish-skip-identity / 等
    值: true / 1 / yes 都算 skip
    """
    val = headers.get("x-catfish-skip-identity", "")
    return val.lower() in ("true", "1", "yes")
