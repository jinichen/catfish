"""P60 微信里找专家 (10/18, docs/EXPERT-PACK-PLAN.md 第三阶段)。

员工在微信里跟小鲶说「找资质专员」→ 这个聊天之后的消息交给资质专员 (它的 hermes profile) 回;
说「回小鲶」→ 回到主小鲶; 说「找谁」→ 列出能找的专家。跟工作台的「找谁」一个意思, 只是换了个入口。

# 怎么做的

两处:

1. **包 `WeixinAdapter.handle_message`**: 只在**主 Bot** (不是专家自己绑的微信号) 上, 只认整句就是命令的消息 ——
   「找<名字>」里的名字必须真是一个已装、在跑的专家 (或小鲶), 否则原样交给 agent ("找一下上周的合同" 不会被吃掉)。
   命中就写 `~/.catfish/wechat_expert_routes.json` 的 `{chat_id: profile}`、回一句确认, 这条消息不进 agent。
2. **包 `GatewayRunner._profile_name_for_source`**: 原逻辑 (静态路由) 没给出结果、消息来自主 Bot 的微信时,
   查上面那个文件; 指向的 profile 必须在网关托管的集合里 (专家删了 / 停了就当没有, 回到主小鲶, 不报错拒收)。
   路由文件每条消息读一次 (几十字节), 改了不用重启网关。

# 为什么还要 Companion 写占位静态路由

hermes 只让「有一条静态路由经主 Bot 指向它」的 profile 借主 Bot 的微信发回复 (`authz_mixin._is_shared_bot_satellite`)。
Companion 给每个「找谁」专家写一条 chat_id 永远匹配不上的路由 (`catfish-wechat-expert:<profile>`), 只为满足这一条;
它本身不改派任何消息。见 companion-app `expert_bots_gateway.rs` 的 reconcile_wechat_expert_routes。

# 不做的

- 不按人 / 群在后台自动分派: 只有员工自己说「找 X」才切 (跟「主小鲶只推荐、不偷偷转交」同一个原则)。
- 专家自己绑了独立微信号的 (secondary bot), 消息本来就归它, 这里不管。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("catfish.xcatfish_user.plugin")

ROUTES_FILE = "wechat_expert_routes.json"
EXPERT_PREFIX = "expert-"
_FIND = re.compile(r"^\s*(?:找|切到|切换到|换成)\s*(.{1,20}?)\s*[。.!！]?\s*$")
_BACK = {"小鲶", "小鲶鱼", "鲶鱼", "主小鲶"}
_WHO = {"找谁", "有哪些专家", "专家列表"}
_BACK_CMDS = {"回小鲶", "回到小鲶", "回主小鲶", "退出专家"}


def _catfish_home() -> Path:
    env = os.environ.get("CATFISH_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".catfish"


def _routes_path() -> Path:
    return _catfish_home() / ROUTES_FILE


def read_routes() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_routes_path().read_text(encoding="utf-8"))
        routes = data.get("routes") if isinstance(data, dict) else None
        return routes if isinstance(routes, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_routes(routes: dict[str, dict[str, Any]]) -> None:
    p = _routes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "routes": routes}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def set_route(chat_id: str, profile: Optional[str], name: str = "") -> None:
    routes = read_routes()
    if profile:
        routes[chat_id] = {"profile": profile, "name": name, "at": int(time.time())}
    else:
        routes.pop(chat_id, None)
    _write_routes(routes)


def installed_experts() -> list[dict[str, str]]:
    """本机专家定义 (`~/.catfish/experts/<id>/expert.yaml`), 只要启用的。"""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return []
    out = []
    for p in sorted((_catfish_home() / "experts").glob("*/expert.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001  — 一份坏定义不能让微信整个不回
            continue
        if d.get("enabled", True) is False or not d.get("id") or not d.get("name"):
            continue
        out.append({"profile": EXPERT_PREFIX + str(d["id"]), "name": str(d["name"]),
                    "description": str(d.get("description") or "")})
    return out


def _norm(s: str) -> str:
    return re.sub(r"[\s「」『』\"'“”‘’]", "", s or "")


def match_expert(query: str, experts: list[dict[str, str]]) -> list[dict[str, str]]:
    q = _norm(query)
    if not q:
        return []
    exact = [e for e in experts if _norm(e["name"]) == q]
    if exact:
        return exact
    return [e for e in experts if q in _norm(e["name"]) or (_norm(e["name"]) in q and len(_norm(e["name"])) >= 2)]


def _served_profiles(runner: Any) -> set[str]:
    try:
        from gateway.run import _multiplex_profile_homes  # noqa: PLC0415
        return {name for name, _home in _multiplex_profile_homes(getattr(runner, "config", None))}
    except Exception:  # noqa: BLE001
        return set()


def handle_command(text: str, chat_id: str, served: set[str]) -> Optional[str]:
    """是命令就处理并返回要回的话; 不是 (或名字对不上任何专家) 返回 None, 消息照常交给 agent。"""
    t = (text or "").strip()
    if not t or len(t) > 24:
        return None
    experts = [e for e in installed_experts() if e["profile"] in served]
    cur = read_routes().get(chat_id)
    if t in _BACK_CMDS:
        if not cur:
            return "现在就是小鲶在回你。"
        set_route(chat_id, None)
        return f"好, 已经从「{cur.get('name') or cur.get('profile')}」切回小鲶。"
    if t in _WHO:
        if not experts:
            return None
        lines = [f"· {e['name']}" + (f": {e['description']}" if e["description"] else "") for e in experts]
        now = f"现在是「{cur.get('name')}」在回你。" if cur else "现在是小鲶在回你。"
        return "能找的专家:\n" + "\n".join(lines) + f"\n\n说「找 + 名字」切过去, 说「回小鲶」切回来。{now}"
    m = _FIND.match(t)
    if not m:
        return None
    q = m.group(1)
    if _norm(q) in _BACK:
        if not cur:
            return "现在就是小鲶在回你。"
        set_route(chat_id, None)
        return f"好, 已经从「{cur.get('name') or cur.get('profile')}」切回小鲶。"
    hits = match_expert(q, experts)
    if not hits:
        return None
    if len(hits) > 1:
        return "你说的是哪一个? " + "、".join(f"「{e['name']}」" for e in hits) + " (说全名)"
    e = hits[0]
    if cur and cur.get("profile") == e["profile"]:
        return f"现在就是「{e['name']}」在回你。说「回小鲶」切回来。"
    set_route(chat_id, e["profile"], e["name"])
    return f"好, 这个对话之后交给「{e['name']}」。说「回小鲶」切回来。"


# ── patch ────────────────────────────────────────────────────────

def wrap_profile_name_for_source(orig):
    def _profile_name_for_source(self, source, adapter_profile=None):
        prof = orig(self, source) if adapter_profile is None else orig(self, source, adapter_profile=adapter_profile)
        if prof is not None:
            return prof
        try:
            if getattr(getattr(source, "platform", None), "value", None) != "weixin":
                return None
            if not getattr(getattr(self, "config", None), "multiplex_profiles", False):
                return None
            owner = adapter_profile
            if owner is None and callable(getattr(source, "_transport_adapter_ref", None)):
                t = self._transport_owner(source) if callable(getattr(self, "_transport_owner", None)) else None
                if isinstance(t, tuple):
                    owner = t[1]
            if owner is not None:
                return None  # 专家自己绑的微信号, 不归这里管
            target = (read_routes().get(str(getattr(source, "chat_id", "") or "")) or {}).get("profile")
            if target and target in _served_profiles(self):
                return target
        except Exception:  # noqa: BLE001  — 找专家出错一律回到主小鲶, 不能让消息丢
            logger.warning("P60 查微信专家路由失败, 交给主小鲶", exc_info=True)
        return None

    _profile_name_for_source._catfish_p60 = True  # type: ignore[attr-defined]
    return _profile_name_for_source


def wrap_handle_message(orig):
    async def handle_message(self, event):
        try:
            if getattr(self, "_owner_profile", None) is None and getattr(event, "text", None):
                source = event.source
                runner = getattr(self, "gateway_runner", None)
                reply = handle_command(event.text, str(source.chat_id), _served_profiles(runner)) if runner else None
                if reply is not None:
                    await self.send(source.chat_id, reply)
                    logger.info("P60 微信找专家命令: %s", event.text[:24])
                    return None
        except Exception:  # noqa: BLE001
            logger.warning("P60 处理微信找专家命令失败, 照常交给 agent", exc_info=True)
        return await orig(self, event)

    handle_message._catfish_p60 = True  # type: ignore[attr-defined]
    return handle_message


def _patch_p60_wechat_expert_routing() -> None:
    from gateway import run as _run  # noqa: PLC0415
    from gateway.platforms import weixin as _wx  # noqa: PLC0415

    runner = getattr(_run, "GatewayRunner", None)
    orig_route = getattr(runner, "_profile_name_for_source", None) if runner else None
    cls = getattr(_wx, "WeixinAdapter", None)
    orig_handle = getattr(cls, "handle_message", None) if cls else None
    if orig_route is None or orig_handle is None:
        logger.warning("P60: GatewayRunner._profile_name_for_source / WeixinAdapter.handle_message 不在 (hermes 改了?), skip")
        return
    if not getattr(orig_route, "_catfish_p60", False):
        runner._profile_name_for_source = wrap_profile_name_for_source(orig_route)
    if not getattr(orig_handle, "_catfish_p60", False):
        cls.handle_message = wrap_handle_message(orig_handle)
    logger.info("P60 微信里找专家 (找X / 回小鲶 / 找谁) patched")
