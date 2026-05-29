"""Session-id → catfish_outgoing_user 跨线程注册表.

# 为什么需要这个

auto-title / compression / background_review 等 auxiliary task 在主对话回完后
另起线程跑, 走 gateway/run.py 手搓的 main_runtime dict (不调
agent._current_main_runtime()). 这条路径拿不到 agent 引用, 也就拿不到 platform
user.

但 session_id 在 main_runtime 调用前就已经分配, auxiliary task 启动时也带着同一
个 session_id (auto_title_session 第二个参数). 我们用 session_id 当 key 在主对话
agent 还活着时注册 catfish_outgoing_user, auxiliary task 启动时 lookup, 拿到补回
main_runtime["catfish_outgoing_user"].

# 线程安全

threading.Lock 保护. lookup / register / unregister 全锁内.

# 生命周期

主 agent 销毁前应该 unregister 防累积 (但容量上限 1024 兜底).
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger("catfish.xcatfish_user.session_registry")

_MAX_ENTRIES = 1024  # 兜底防泄漏
_lock = threading.Lock()
_session_to_cf_user: "OrderedDict[str, str]" = OrderedDict()


def register(session_id: str, cf_user: str) -> None:
    """注册 session_id 对应的 catfish_outgoing_user."""
    if not session_id or not cf_user:
        return
    with _lock:
        # 已存在就 move_to_end (LRU)
        if session_id in _session_to_cf_user:
            _session_to_cf_user.move_to_end(session_id)
            _session_to_cf_user[session_id] = cf_user
            return
        _session_to_cf_user[session_id] = cf_user
        # 容量上限驱逐最老的
        while len(_session_to_cf_user) > _MAX_ENTRIES:
            evicted_id, _ = _session_to_cf_user.popitem(last=False)
            logger.debug("evicted oldest session %s (cap=%d)", evicted_id, _MAX_ENTRIES)


def lookup(session_id: str) -> Optional[str]:
    """查 session_id 对应的 catfish_outgoing_user. 没有返回 None."""
    if not session_id:
        return None
    with _lock:
        return _session_to_cf_user.get(session_id)


def unregister(session_id: str) -> None:
    """主 agent 销毁时调用. 不强制, 容量上限会兜底."""
    if not session_id:
        return
    with _lock:
        _session_to_cf_user.pop(session_id, None)


def size() -> int:
    """监控用."""
    with _lock:
        return len(_session_to_cf_user)
