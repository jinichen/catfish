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


def _read_soul_customer() -> str:
    """5/13 拆分: 读客户特定 SOUL (~/.hermes/SOUL_<CUSTOMER>.md).

    `CATFISH_CUSTOMER` env 决定挑哪份 (默认 'ffcs' 兼容现有部署).
    业务环境特定段 (内网域名 / 系统简称 / 公文称谓) 应该写在这, 不污染通用 SOUL.

    没配 / 文件不存在 → 返空, build_identity_content 静默跳过.
    """
    import os  # noqa: PLC0415
    customer = (os.environ.get("CATFISH_CUSTOMER") or "ffcs").strip()
    if not customer:
        return ""
    # 大写文件名: SOUL_FFCS.md (跟 install.sh ${CUSTOMER^^} 对齐)
    return _cache.read(_hermes_home() / f"SOUL_{customer.upper()}.md")


# ─── BL-SOUL-SCENARIO P2 (5/13 鸿波"完成 SOUL 优化 P2"): 按场景注入 ──────────
#
# SOUL.md 把高频踩坑细则拆出场景子文件, gateway 按当前 chat 的 tool 候选决定
# 注入哪些, 不再永远全量灌. 简单 chat (不调浏览器/不写代码) 节省 ~3K token.
#
# 触发规则: tool 名 ↔ scenario file
#   tool 名前缀 / 名字 → SOUL_<NAME>.md
SCENARIO_RULES: list[tuple[tuple[str, ...], str, str]] = [
    # (tool_name 触发模式, scenario 标签, 文件名)
    # 模式匹配: 元组里任一字符串是 tool_name 的子串就触发
    (("catfish_browser_", "browser_"), "BROWSER", "SOUL_BROWSER.md"),
    (("execute_code", "python_exec", "shell_exec"), "EXECUTE_CODE",
     "SOUL_EXECUTE_CODE.md"),
]


def _detect_scenarios(tools: list[dict[str, Any]] | None) -> list[tuple[str, str]]:
    """从 OpenAI tools 列表 (chat_completions body['tools']) 抽 tool_name 集合,
    按 SCENARIO_RULES 决定要注入哪些场景子文件.

    返 [(scenario_label, filename), ...] 顺序跟 SCENARIO_RULES 一致.

    tools 为 None / 空 → 返 []. tool 命中多种 scenario → 各 scenario 独立返一次.
    """
    if not tools or not isinstance(tools, list):
        return []
    tool_names: set[str] = set()
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        name = fn.get("name", "")
        if isinstance(name, str) and name:
            tool_names.add(name)
    out: list[tuple[str, str]] = []
    for patterns, label, filename in SCENARIO_RULES:
        # 任一 tool_name 含 patterns 任一子串 → 触发
        if any(any(p in n for p in patterns) for n in tool_names):
            out.append((label, filename))
    return out


def _read_scenario(filename: str) -> str:
    """读 ~/.hermes/<filename>, 失败返空 (静默)."""
    return _cache.read(_hermes_home() / filename)


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


def build_identity_content(tools: list[dict[str, Any]] | None = None) -> str:
    """读 SOUL + 按 tools 决定的场景段 + USER memory + memories/, 拼成单个 system
    message 内容字符串。

    tools (5/13 BL-SOUL-SCENARIO P2): chat_completions body['tools'], 用来决定
    要不要注入 SOUL_BROWSER.md / SOUL_EXECUTE_CODE.md 等场景段. None / 空 → 不注入
    场景段 (简单 chat 不调工具时省 token).

    返回空字符串说明无任何身份内容（员工还没装 SOUL.md / Hermes 还没写过 memory）。
    """
    parts: list[str] = []

    soul = _read_soul().strip()
    if soul:
        parts.append(f"# Identity (SOUL)\n\n{soul}")

    # 5/13 拆分: 客户特定段紧跟 CORE SOUL, 让 LLM 看到 "通用 + 客户" 一气呵成
    soul_cust = _read_soul_customer().strip()
    if soul_cust:
        import os  # noqa: PLC0415
        cust_label = (os.environ.get("CATFISH_CUSTOMER") or "ffcs").upper()
        parts.append(f"# Identity (SOUL_{cust_label} — 客户业务环境)\n\n{soul_cust}")

    # BL-SOUL-SCENARIO P2 (5/13): 按 tool 候选注入场景段
    for label, filename in _detect_scenarios(tools):
        content = _read_scenario(filename).strip()
        if content:
            parts.append(f"# Identity (SOUL_{label} — 场景纪律)\n\n{content}")

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
    agent_name: str = "",
    agent_personality: str = "",
    tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """如果 messages 里没 system message 且 skip=False, 在前面插 SOUL+memory 作为 system。

    skip:
        通常由 HTTP header `X-Catfish-Skip-Identity: true` 触发。
        Hermes 这种已自注入 system 的客户端理论上不需要 skip
        (它发的 messages 第一条就是 system, 我们检测到就不动了)。
        skip 留给"故意不要 catfish 人格"的边缘场景。

    agent_name / agent_personality (BL-E11 五一 sprint 5/3):
        员工自定义"小鲶"叫啥 + 人设. 默认空 = 用 SOUL 默认 (小鲶 + 温柔同事).
        非空时在 SOUL 前插 personalization preamble, 优先级高于 SOUL 默认人格.
        头通过 X-Catfish-Agent-Name / X-Catfish-Agent-Personality 由 Companion 传.
    """
    if skip:
        return messages or []

    if has_system_message(messages):
        # 客户端有自己的 system prompt, 尊重它的意图
        return messages

    content = build_identity_content(tools=tools)
    if not content:
        # 员工还没装 SOUL.md / 没 memory, 静默跳过
        return messages or []

    # BL-E11: 如果员工改名 / 改人设, 拼 preamble 在 SOUL 前面
    preamble = build_personalization_preamble(agent_name, agent_personality)
    if preamble:
        content = preamble + "\n\n---\n\n" + content

    return [{"role": "system", "content": content}] + (messages or [])


# ─────────────────────────────────────────────
# BL-E11 命名权 + 人设 (五一 sprint 5/3)
# ─────────────────────────────────────────────

# 3 档预设人设 — 简单, 不爆改 SOUL.md
_PERSONALITY_PRESETS: dict[str, str] = {
    "gentle": "",  # 默认, 跟 SOUL.md 一致, 不加 preamble
    "direct": (
        "**人设调整: 直爽风格.** 你说话更短, 不绕弯, 不堆套话. "
        "答完该答的就停, 不追问废话. 不用 emoji. "
        "出错直接说哪儿错了 + 怎么改, 不道歉一长串."
    ),
    "roast": (
        "**人设调整: 略带毒舌风格.** 你性子直, 看到员工写得不好/想得不周到时会**轻度吐槽**, "
        "再给建设性建议 (吐槽 1 句 + 建议 2 句的比例). "
        "目标不是伤人是让员工记住要点. 关键边界: 涉及员工敏感话题 (健康/家庭/收入) 不吐槽, 切回温柔模式."
    ),
}


def build_personalization_preamble(agent_name: str, agent_personality: str) -> str:
    """生成"员工偏好覆盖 SOUL 默认"的 preamble.

    agent_name: 员工给"小鲶"起的别名 (e.g. "老李"). 空 / "小鲶" / "Catfish" 都不动.
    agent_personality: gentle (默认, 不加) / direct (直爽) / roast (毒舌).

    都默认值 → 返空字符串, 不插 preamble (省 token + 不污染 prompt).
    """
    name = (agent_name or "").strip()
    pers = (agent_personality or "").strip().lower()

    # 完全默认 → 不动
    is_default_name = name in ("", "小鲶", "Catfish", "catfish")
    is_default_pers = pers in ("", "gentle")
    if is_default_name and is_default_pers:
        return ""

    parts: list[str] = ["# 员工偏好 (优先级最高, 覆盖下面 SOUL 默认人格)"]
    if not is_default_name:
        parts.append(
            f"**员工给你起的名字: {name}** (默认名 '小鲶' 是 brand fallback). "
            f"这位员工偏好叫你 '{name}'. 在对话中你说自己叫 {name}, "
            f"不要再说'我是小鲶' / '我是 Catfish'. 介绍自己用 '我是 {name}, 鲶鱼平台的 AI 副手'."
        )
    if not is_default_pers and pers in _PERSONALITY_PRESETS:
        preset = _PERSONALITY_PRESETS[pers]
        if preset:
            parts.append(preset)
    return "\n\n".join(parts)


def header_skips_identity(headers) -> bool:
    """从 FastAPI request.headers 判断客户端要不要注入。

    支持多种大小写:
        X-Catfish-Skip-Identity / x-catfish-skip-identity / 等
    值: true / 1 / yes 都算 skip
    """
    val = headers.get("x-catfish-skip-identity", "")
    return val.lower() in ("true", "1", "yes")


def header_agent_prefs(headers) -> tuple[str, str]:
    """从 FastAPI request.headers 取员工自定义的 agent name / personality.

    BL-E11. Header:
        X-Catfish-Agent-Name        (e.g. "老李")
        X-Catfish-Agent-Personality (gentle / direct / roast)

    返 (name, personality), 都默认 "" (gateway 会按 SOUL 默认走).
    """
    name = headers.get("x-catfish-agent-name", "").strip()
    personality = headers.get("x-catfish-agent-personality", "").strip().lower()
    # 防员工传进 personality 不在白名单, 静默 fallback gentle
    if personality and personality not in _PERSONALITY_PRESETS:
        personality = "gentle"
    return name, personality
