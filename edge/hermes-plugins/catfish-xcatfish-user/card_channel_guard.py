"""对话卡片只给声明过能画的客户端 (11/15, tool-bridge a2ui_card.py, 入口是 catfish_form 的 card 动作)。

照 A2UI 上游: 客户端每轮声明自己能画哪些组件目录 (rendererCapabilities, 形状 {"1.0": {"supportedCatalogIds": [...]}}),
没声明就只回文字。鲶鱼的 Companion 主对话把它放在请求头 X-A2UI-Renderer-Capabilities 里, 插件中间件读出来
登记到会话上 (plugin_ctx.CV_A2UI_CAPS → session_registry.register_a2ui); 小鲶调 catfish_form card 之前查这里:

- 声明过、而且目录对得上 → 放行
- 没声明 (微信、定时任务、子任务、早安页、还没升级的旧版 Companion) → 拦下, 让小鲶改用文字问
  —— 旧版 Companion 画不了卡, 不拦的话员工只看到一句「填一下这张卡」却没有卡; 中央 / 电脑端升级有先后, 这种错位一定会有

评估时网关环境里设 CATFISH_A2UI_ASSUME_CAPABLE=1: 命令行发起的会话 (hermes chat --source tool, 带不了请求头) 当作能画;
微信 / 定时任务照样不放。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

FORM_TOOL = "catfish_form"
#: 上游 v1.0 基础目录 (裁剪过, id 不变) —— 跟 tool-bridge a2ui_card.py 用的、Companion CARD_CATALOG_ID 一致
CATALOG_ID = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
_EVAL_SOURCES = ("cli", "tool", "oneshot")


def _hermes_home() -> Path:
    """跟 hermes_token_renewal._hermes_home 同一套 (Windows 上不是 ~/.hermes)。"""
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    try:
        from hermes_constants import get_hermes_home  # noqa: PLC0415

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hermes"
    return Path.home() / ".hermes"  # windows-parity: 上面已按 LOCALAPPDATA 走 Windows, 这里只剩 mac/Linux


def session_platform(session_id: str, home: Path | None = None) -> str:
    """state.db 里这个会话的来源 (只给评估开关用); 查不到返回空。"""
    if not session_id:
        return ""
    try:
        conn = sqlite3.connect(f"file:{(home or _hermes_home()) / 'state.db'}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT source FROM sessions WHERE id = ?", (session_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row and row[0] else ""


def is_card_call(tool_name: str, args: Any) -> bool:
    if not (tool_name == FORM_TOOL or tool_name.endswith(f"__{FORM_TOOL}")):
        return False
    return isinstance(args, dict) and str(args.get("action") or "") == "card"


def can_render(caps_json: str | None) -> bool:
    """声明里 v1.0 支持的目录有没有我们这份。"""
    if not caps_json:
        return False
    try:
        caps = json.loads(caps_json)
    except (TypeError, ValueError):
        return False
    ids = ((caps or {}).get("1.0") or {}).get("supportedCatalogIds") if isinstance(caps, dict) else None
    return isinstance(ids, list) and CATALOG_ID in ids


def decide(tool_name: str, args: Any, caps_json: str | None, platform: str = "") -> dict[str, str] | None:
    """pre_tool_call 的判断: None = 放行, dict = 拦下 (message 给模型看)。"""
    if not is_card_call(tool_name, args):
        return None
    if can_render(caps_json):
        return None
    if os.environ.get("CATFISH_A2UI_ASSUME_CAPABLE") == "1" and platform in _EVAL_SOURCES:
        return None
    return {
        "action": "block",
        "message": ("这个对话的客户端画不了卡片 (只有新版鲶鱼电脑端的对话里能画)。"
                    "不要再用 catfish_form 的 card, 改用文字一项一项问员工。"),
    }
