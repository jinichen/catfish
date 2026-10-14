"""P58 (10/14, hermes v0.22): 小鲶中文语言包 —— 微信 / 网关回给员工的固定提示用鲶鱼的说法。

# 为什么换成语言包

v0.22 起网关的固定提示 (审批卡、/stop /reset 回执、忙碌提示 ...) 全走 ``t("gateway.…")``,
按 ``display.language`` (Companion 启动时写 zh, 见 services/hermes_language.rs) 取 hermes 自带
``locales/zh.yaml``。自带的中文有三类员工看不懂 / 不该看到的:

  · 品牌: 「Hermes」「☤」(113 处) —— 员工认识的是小鲶
  · 「代理」: agent 直译, 「代理正在恢复…」「已将原因转达给代理」, 员工以为是网络代理
  · 审批几句: 「智能拒绝——所有者对单次操作的覆盖」「config.yaml 中的 approvals.timeout」

原来靠 P28 在微信出站时按字面量逐条替换 (_P28_REPLACEMENTS), hermes 一改原文就漏 ——
v0.22 一次改了 8 条。语言包是 hermes 给插件的正规入口 (``agent.i18n_layers.register_pack``,
跟 ``PluginContext.register_locale`` 同一层, 优先级最高): 这里**从自带 zh 目录派生**, 上游新加
的提示自动跟着换, 不用再追原文。P28 留作兜底 (没走 t() 的英文硬编码)。

# 只碰 zh

员工机 display.language 是 zh。别的语言不注册, 开发机切英文看到的还是上游原文。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("catfish.xcatfish_user.plugin")

LANG = "zh"
SOURCE = "plugin:catfish-xcatfish-user"

#: 这些 key 里的「代理」是网络代理 / Docker 出站代理, 不换
_PROXY_KEYS = ("gateway.proxy.", "slash.egress.")
#: 只在这几段换「代理」: 员工在聊天里看得到的。cli / tips 是开发者在终端里看的, 不动
_AGENT_WORD_SECTIONS = ("gateway.", "platform.", "slash.")

_BRAND = [
    (re.compile(r"Hermes Agent"), "小鲶"),
    (re.compile(r"Hermes 代理"), "小鲶"),
    (re.compile(r"Hermes"), "小鲶"),
    (re.compile(r"☤\s*"), ""),
]
_AGENT_WORD = [
    (re.compile(r"子代理"), "子任务"),
    (re.compile(r"活跃的代理"), "在干活的小鲶"),
    (re.compile(r"活跃代理"), "在干活的小鲶"),
    (re.compile(r"代理"), "小鲶"),
]

#: 逐条写死的几句 —— 派生出来仍然别扭、或者要跟 P51 / P52 的「回 1 / 2」说法对齐的
OVERRIDES: dict[str, str] = {
    "gateway.exec_approval.header": "小鲶要做一步需要你同意的操作",
    "gateway.exec_approval.reason_label": "为什么要问你",
    "gateway.exec_approval.smart_deny_heading": "⚠️ **AI 判断这一步风险较大, 要你亲自决定 (只管这一次):**",
    "gateway.exec_approval.smart_deny_line": "AI 判断这一步风险较大: 你同意的话只放行这一次。",
    "gateway.exec_approval.deadline": "{window}内没回复就不执行。",
    "gateway.exec_approval.timed_out_notice": "⌛ 等了 {window} 没收到回复, 这一步没有执行。还需要的话跟小鲶说一声再做。",
    "gateway.approval_expired": "⚠️ 这条审批已经过期了, 需要的话让小鲶重新做一次。",
}


def build_pack(bundled: dict[str, str]) -> dict[str, str]:
    """从 hermes 自带 zh 目录派生出要覆盖的条目 (只返回有变化的)。"""
    pack: dict[str, str] = {}
    for key, text in bundled.items():
        if not isinstance(text, str):
            continue
        out = text
        for pat, rep in _BRAND:
            out = pat.sub(rep, out)
        if key.startswith(_AGENT_WORD_SECTIONS) and not key.startswith(_PROXY_KEYS):
            for pat, rep in _AGENT_WORD:
                out = pat.sub(rep, out)
        if out != text:
            pack[key] = out
    for key, text in OVERRIDES.items():
        if key in bundled:  # 上游删了的 key 不凭空加
            pack[key] = text
    return pack


def _patch_p58_locale_zh() -> None:
    try:
        from agent import i18n, i18n_layers
    except ImportError as e:
        logger.warning("P58: agent.i18n 不在 (hermes < 0.22?), skip: %s", e)
        return
    if any(getattr(entry, "source", None) == SOURCE and entry.lang == LANG
           for entry in getattr(i18n_layers, "_packs", [])):
        return  # 插件重载
    bundled = i18n._load_bundled(LANG)
    if not bundled:
        logger.warning("P58: hermes 自带的 zh 语言包读不到, skip")
        return
    missing = [k for k in OVERRIDES if k not in bundled]
    if missing:
        # 上游改了 key: 那几句会回到自带中文 (不会变英文), 记一笔下次补
        logger.warning("P58: 自带 zh 目录里没有这些 key (上游改名?): %s", missing)
    pack = build_pack(bundled)
    i18n_layers.register_pack(LANG, i18n_layers.CORE_SURFACE, pack, source=SOURCE)
    logger.info("P58 小鲶中文语言包已注册 (%d 条: 去 Hermes/☤、「代理」→小鲶、审批说法)", len(pack))
