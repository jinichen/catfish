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
            try:
                from gateway.pairing import PairingStore
                bound = PairingStore().get_email(platform, uid)
                if bound:
                    return bound.strip()
            except Exception as e:
                logger.debug("pairing lookup failed: %s", e)

            # (c) self._user_id 已经是 email
            if "@" in uid and "." in uid:
                return uid

            # (d) synthesize <user_id>@im.<platform>
            return f"{uid}@im.{platform}"

        # (e) env fallback
        env_user = os.environ.get("CATFISH_DEFAULT_USER", "").strip()
        if env_user:
            return env_user
    except Exception as e:
        logger.debug("resolve_for_agent failed: %s", e)

    return ""
