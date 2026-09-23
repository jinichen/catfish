"""角色 → 模型: **从模型配置算出来的**, 不是配置 (9/23 重做).

# 这个模块里没有任何状态

`GET /v1/roles` 是边缘端 (tool-bridge / catfish-memory / xcatfish-user) 跟中央
之间的词汇表: 它们问"chat_default 是谁", 不问模型名。这个词汇表保留, 但答案
不再来自 roles.yaml —— 来自模型页上的两个「默认」徽章:

    chat_default   默认对话模型  (Config.default_model)
    embedding      默认向量模型  (Config.default_embedding_model)
    vision         默认对话模型不能看图时, 网关 multimodal_guard 会换成的那个
    summarize      = chat_default (老 catfish-memory 插件还会问这个 key)

# 为什么不再是配置

9/23 逐个追了 roles.yaml 七个角色在代码里的消费者:

    rate_fast / advisor_call2      零处。rate_fast 是 8/9 鸿波亲手砍的
                                   (email_scheduler.rs: "模型只能来自 picker")
    public_flash                   一句超额文案里的字
    vision                         tool-bridge 两处; 网关自己不用 —— 它见图就按
                                   supports_vision 自动换, 跟角色无关
    summarize                      catfish-memory 里 picker 之后的第二跳 ——
                                   正是 8/9 砍掉的那种"第二个来源"
    chat_default                   跟模型页的「默认」徽章是**两份真相**,
                                   截图里两边已经不一致
    embedding                      唯一真的需要一个指定的

七个里只有 embedding 需要"指定", 而它跟 chat 一样, 用模型上的 default 标志
就够了 (一个 mode 一个默认)。于是 roles.yaml、库表、角色页全部不需要。

# 消费方

    misc_routes   /v1/roles、/v1/embeddings 没带 model 时
    llm_params    hermes 发 `catfish-auto` (微信那条路, 没 picker) 时
    quota         超额文案里"换 X 试试"的 X
    catalog       选择器初始值 —— 直接用 default_model(), 不经这里
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class Role(str, Enum):
    CHAT_DEFAULT = "chat_default"
    EMBEDDING = "embedding"
    VISION = "vision"
    SUMMARIZE = "summarize"


def _cfg():
    from .config import get_config  # noqa: PLC0415  (延迟 import, 防启动期循环)

    return get_config()


def resolve_or_none(role: Role | str) -> str | None:
    """角色 → 模型名. 推不出来 (没有对应 mode 的模型 / 向量模型多个都没挂默认) → None."""
    role_str = role.value if isinstance(role, Role) else str(role)
    try:
        cfg = _cfg()
    except Exception:  # noqa: BLE001  — 配置都读不出来时上层自有报错
        return None
    if role_str in ("chat_default", "summarize"):
        m = cfg.default_model()
        return m.name if m else None
    if role_str == "embedding":
        m = cfg.default_embedding_model()
        return m.name if m else None
    if role_str == "vision":
        chat = cfg.default_model()
        if chat is None:
            return None
        if chat.supports_vision:
            return chat.name
        from .multimodal_guard import pick_vision_alternative  # noqa: PLC0415

        alt = pick_vision_alternative(cfg, chat)
        return alt.name if alt else None
    return None


def to_public_dict() -> dict[str, Any]:
    """GET /v1/roles. 客户端只读 `roles`; 推不出来的 key 不出现 (老客户端会走自己的兜底)."""
    roles = {r.value: resolve_or_none(r) for r in Role}
    return {
        "roles": {k: v for k, v in roles.items() if v},
        "source": "derived",  # 从模型页的「默认」徽章算的, 没有第二份配置
    }
