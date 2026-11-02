"""5 步 catfish_outgoing_user resolve 链, 复用.

resolution order:
  (a) agent._catfish_outgoing_user attribute (api_server _create_agent set 的)
  (b) gateway.pairing.PairingStore.get_email(platform, user_id) — admin 用
      `hermes pairing approve` 绑定的 openid → email
  (c) self._user_id 已经是 email 形态 (CLI 直传 email) → 用之
  (d) 合成 <user_id>@im.<platform>: 满足 catfish gateway _EMAIL_SHAPE_RE,
      落 platform-only 命名空间隔离虚拟员工.
      例: WeChat openid o9cq807y → o9cq807y@im.weixin
  (e) env CATFISH_DEFAULT_USER 兜底 (CLI / Companion 无 platform user 场景)

返回 "" 表示完全没 user, 调用方应该 pop default_headers["X-Catfish-User"]
而不是设空串 (空串会被 catfish gateway 当 service-token-only 拒).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.resolver")


def _hermes_home() -> str:
    """hermes 数据目录: HERMES_HOME > hermes_constants > 平台默认 (同 plugin_wechat_qr._hermes_home)。"""
    import sys

    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return os.path.expanduser(env)
    try:
        from hermes_constants import get_hermes_home  # noqa: PLC0415

        return str(get_hermes_home())
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")  # windows-parity: Windows 上面那行已经返回了


def _env_file_value(key: str) -> str:
    """10/30: (e) 兜底也读 ~/.hermes/.env。

    Companion 把登录员工写进那里的 CATFISH_DEFAULT_USER (hermes_jwt_sync); hermes 的
    load_env 只返回 dict 不写 os.environ (见 memory_enforce._read_hermes_env_key), 光看
    os.environ 拿不到。没有这一步, hermes 自己的定时任务 / 技能整理在门户里全挂
    client:hermes-cli 名下。
    """
    try:
        with open(os.path.join(_hermes_home(), ".env"), encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def bound_email(platform: str, uid: str) -> str:
    """(b) Companion「微信绑定」写进 <platform>-approved.json 的 catfish_email。

    11/03: 原来调 PairingStore.get_email —— 现在的 hermes 没有这个方法 (早先改过的 hermes 才有),
    异常被吞掉, 往下走到 (c): 微信的 openid 本身就长 `o9cq…@im.wechat` 这样带 @ 和点,
    被当成 email 直接用了。门户配额日志里微信那几行就全是 openid, 绑了员工也没换过来。
    现在直接读 approved 表 (id 比对沿用 hermes 的别名规则)。
    """
    try:
        from gateway import pairing as _pairing  # noqa: PLC0415

        store = _pairing.PairingStore()
        get_email = getattr(store, "get_email", None)
        if callable(get_email):
            got = (get_email(platform, uid) or "").strip()
            if got:
                return got
        approved = store._load_approved(platform)
        for key in _pairing._matching_ids(platform, approved, uid):
            info = approved.get(key)
            email = (info.get("catfish_email") or "").strip() if isinstance(info, dict) else ""
            if email:
                return email
    except Exception as e:  # noqa: BLE001
        logger.debug("pairing lookup failed: %s", e)
    return ""


def resolve_for_agent(agent: Any) -> str:
    """Resolve catfish_outgoing_user for an AIAgent instance.

    Safe to call without holding agent state lock — only reads attributes.
    Never raises (all exceptions swallowed and logged at debug).
    """
    try:
        # (a) attribute set 的最优先
        attr = (getattr(agent, "_catfish_outgoing_user", "") or "").strip()
        if attr:
            return attr

        uid = (getattr(agent, "_user_id", "") or "").strip()
        platform = (getattr(agent, "platform", "") or "").lower().strip()

        if uid and platform and platform not in ("", "cli", "api_server"):
            # (b) pairing email bind
            bound = bound_email(platform, uid)
            if bound:
                return bound

            # (c) self._user_id 已经是 email
            if "@" in uid and "." in uid:
                return uid

            # (d) synthesize <user_id>@im.<platform>
            return f"{uid}@im.{platform}"

        # (e) env fallback
        env_user = os.environ.get("CATFISH_DEFAULT_USER", "").strip() or _env_file_value(
            "CATFISH_DEFAULT_USER"
        )
        if env_user:
            return env_user
    except Exception as e:
        logger.debug("resolve_for_agent failed: %s", e)

    return ""
