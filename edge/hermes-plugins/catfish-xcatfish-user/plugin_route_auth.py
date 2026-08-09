"""catfish 自加端点的鉴权 —— 只有一份 (8/9).

P44 (进度探针) 和 P47 (审批建议) 都往 hermes 的 aiohttp app 上挂路由。鉴权走
hermes 自己的 `APIServerAdapter._check_auth` (P18 / P26 / P30 那几个 handler
也是各自调它), adapter 从 P7 stash 的 `request.app["_catfish_apiserver_adapter"]`
拿。

**抽出来是因为 fail-closed 这条不该有第二份实现。** 拿不到 adapter 时必须拒,
不能放行 —— 一旦某处写成"取不到就跳过鉴权", 那个口子会一直开着而且没人发现。
一份实现 + 一组测试, 比两处各自小心可靠。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("catfish.xcatfish_user.route_auth")

#: 鉴权组件没就位时的原因码 (封闭词表, 会显示到 Companion UI)。
REASON_AUTH_UNAVAILABLE = "auth_unavailable"


def check_auth(request: Any) -> Any | None:
    """通过返 None; 不通过返一个可以直接 return 的 aiohttp Response。

    **fail closed**: P7 stash 没就位 / hermes 改了 `_check_auth` 方法名 ——
    都返 503 拒掉, 不放行。
    """
    from aiohttp import web as _w  # noqa: PLC0415

    adapter = request.app.get("_catfish_apiserver_adapter")
    if adapter is None or not hasattr(adapter, "_check_auth"):
        logger.warning(
            "catfish 端点鉴权拿不到 adapter (P7 stash 未就位?), 拒绝该请求 —— "
            "宁可 503 也不裸奔"
        )
        return _w.json_response(
            {"ok": False, "reason": REASON_AUTH_UNAVAILABLE},
            status=503,
        )
    try:
        return adapter._check_auth(request)
    except Exception as e:  # noqa: BLE001
        # 鉴权本身抛异常同样按拒处理 —— 异常不是"通过"
        logger.warning("_check_auth 抛异常, 按拒处理: %s", e)
        return _w.json_response(
            {"ok": False, "reason": REASON_AUTH_UNAVAILABLE},
            status=503,
        )
