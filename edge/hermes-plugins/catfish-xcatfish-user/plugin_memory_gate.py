"""P42 (8/8): 后台调用不进记忆 —— 员工邮件正文不该落进个人知识库。

# 病

鸿波 8/8 查 `~/.catfish/employee_journal.md` (1.3 MB / 2856 条), 里面有大量邮件
正文。拿今天早上收到的邮件的特征词去搜: `CIC资质认证` 223 次、`superlinear`
91 次、`安全生产专栏` 79 次。典型条目长这样:

    ## [2026-08-07 18:59] session | api-7b41e48b195a8034
    [LLM 总结失败 (上游 502 等), raw 2 pairs 保留]
    - 小鲶: 1. 主题: 转发: 关于CIC资质认证需要财务负责人提供的相关材料
            | 发件人: 任何晓 <ffrenhx@chinatelecom.cn>
    - 小鲶: ["中"]

这不是员工在跟小鲶聊邮件 —— 这是 **email_scheduler 的后台评级调用**
(assistant 那句 `["中"]` 就是紧急度)。它走 Companion → hermes 8642 → 网关
(6/29 定的单路径), 于是经过 agent loop, 于是触发 `MemoryManager.sync_all` →
catfish-memory 的 `sync_turn` → 写 journal。

而 journal 又是自动建 wiki 页的输入。所以链条是:

    收到一封邮件 → 后台评级 → 邮件标题/发件人进 journal → 蒸成 wiki 条目

鸿波定的原则: **员工邮件正文进个人知识库不合理**。

顺带说明为什么正文会**原样**落盘: `catfish_memory.py:2192` 在摘要 LLM 返空时
走 `_build_raw_journal_fallback`, 写的是未经摘要的原始对话。设计意图是"保 data
不丢", 但失败时落盘的内容反而比正常情况更全。

# 为什么挡在这一层

三个位置都试过, 只有这里成立:

1. **改路由让评级不走 hermes** —— 6/29 鸿波明确定过"数据流应该是 companion →
   hermes → gateway, 不是双路径"。不能推翻。
2. **在 catfish-memory 的 sync_turn 里按来源过滤** —— 走不通。`sync_all` 内部
   把活儿丢进后台线程才调 `sync_turn`, 请求级的 ContextVar 传不过去。
3. **patch `MemoryManager.sync_all`** —— `run_agent.py:4124` 调它时**还在请求
   上下文里**, `CV_CF_SOURCE` 读得到。就是这里。

# 判据: 有 source 就跳

查了一遍谁会设 `X-Catfish-Source` / `?catfish_source=`:

    companion-advisor / companion-advisor-transform / companion-briefing-card
    companion-email-draft / companion-email-scheduler / companion-phishing-scan
    companion-profile / companion-wiki-suggest

**全是后台或内部调用**。员工在工作台聊天、微信发消息, 都不设这个值。
所以"source 非空 = 不是员工在跟小鲶聊天"这条判据是准的, 而且不用维护一张
会过期的名单。

**这条判据的失效方式要说清楚**: 将来新增一个后台调用而**忘了设 source**,
它仍会进记忆。反过来 (员工聊天被误判成后台) 不会发生 —— 那需要有人主动
给聊天路径加 source。也就是说这道闸的失败方向是"漏挡", 不是"误挡"。
选它是因为误挡的代价更大: 记忆悄悄不工作, 表现是"用多久都不会更懂你",
现场根本看不出来 (7/29 的 SOP 里就写过这个坑)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.xcatfish_user.plugin")


def _patch_p42_memory_skip_background(cv_cf_source) -> None:
    """wrap MemoryManager.sync_all —— 有 catfish_source 就不记忆。

    Args:
        cv_cf_source: plugin.py 里的 `CV_CF_SOURCE` ContextVar。传进来而不是
            import, 免得跟 plugin.py 形成循环依赖 (它已经 import 本模块)。
    """
    try:
        from agent.memory_manager import MemoryManager
    except Exception as e:  # noqa: BLE001
        logger.warning("P42 skip: import MemoryManager 失败 (%s)", e)
        return

    if getattr(MemoryManager.sync_all, "_catfish_p42", False):
        return  # 幂等: plugin 重载时不重复包

    _orig = MemoryManager.sync_all

    def patched(self, *args, **kwargs):
        try:
            source = (cv_cf_source.get() or "").strip()
        except Exception:  # noqa: BLE001  CV 不该抛; 抛了也不能把记忆搞挂
            source = ""
        if source:
            # debug 而不是 info: 员工机上邮件评级每 30 秒一次, info 会把日志刷爆
            logger.debug("P42: source=%s 是后台调用, 跳过记忆写入", source)
            return None
        return _orig(self, *args, **kwargs)

    patched._catfish_p42 = True  # type: ignore[attr-defined]
    MemoryManager.sync_all = patched  # type: ignore[method-assign]
    logger.info("P42 ✓ MemoryManager.sync_all 已加来源闸 (后台调用不进记忆)")
