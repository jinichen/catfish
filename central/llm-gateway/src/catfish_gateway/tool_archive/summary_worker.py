"""BL-Q3-ARCHIVE — 后台 haiku 摘要 worker (5/11).

asyncio task, 每 5s 扫一次 summary IS NULL 的 archive, 调 gateway loopback
chat /v1/chat/completions (use_case='tool_summarizer', private 优先).

成功 → update_summary(ref, summary, model).
失败 → update_summary(ref, error=...) — 不再扫这条 (避免 retry 雪崩).

启动: app.py lifespan 里 `asyncio.create_task(start_summary_worker_loop())`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from . import db, features, prompts

logger = logging.getLogger("catfish.gateway.tool_archive.summary_worker")

#: 扫表间隔 (秒)
SCAN_INTERVAL_SEC = 5.0

#: 单批最多处理多少条 (避免一次拉太多)
BATCH_SIZE = 5

#: 摘要 LLM 超时
SUMMARY_TIMEOUT_SEC = 30.0

#: 摘要输出 token 上限 (~120 字)
SUMMARY_MAX_TOKENS = 200


async def _summarize_one(
    content: str,
    tool_name: str | None,
    origin_model: str | None = None,
) -> tuple[str | None, str | None]:
    """返 (summary, model_name). 失败时 summary=None.

    走 gateway loopback chat (跟 session_summarizer 同款).

    模型选择顺序 (BL-Q3-ARCHIVE fix2, 5/11):
      1. origin_model (chat 用啥 summary 也用啥, 私有部署 token 不要钱)
      2. catalog 里 tool_summarizer tag 模型 (有人标注的话)
      3. 兜底 — 任意 chat + private 优先
    """
    if not features.summary_enabled():
        return None, None  # 摘要全局关 — caller 也不会调到这里

    import httpx  # 懒 import

    from ..auth.dev_token import ensure_internal_dev_token  # noqa: PLC0415
    from ..config import load_config  # noqa: PLC0415

    try:
        config = load_config()
    except Exception as e:  # noqa: BLE001
        logger.warning("summary_worker load_config 失败: %s", e)
        return None, None

    # BL-INTERNAL-MODEL-FOLLOW-USER (5/17): 严格 origin_model 同款, 不 fallback.
    # 老 5/11 BL-Q3-ARCHIVE fix2 是 'origin_model 顶首位 + tag/兜底 fallback chain',
    # 现在改严格. 没 origin_model / 不在 catalog / 不可达 → 跳过 (caller 标 error
    # 防再扫到), 不切别的 model.
    if not origin_model:
        logger.info("summary_worker: 没 origin_model, 跳过 (员工同款规则)")
        return None, None

    origin_obj = next(
        (m for m in config.models
         if m.name == origin_model and m.mode == "chat" and m.upstream.is_available),
        None,
    )
    if origin_obj is None:
        logger.info(
            "summary_worker: origin_model=%s 不在 catalog / 不可达, 跳过 (不 fallback)",
            origin_model,
        )
        return None, None

    port = os.environ.get("PORT", "8999")
    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_INTERNAL_URL",
        f"http://127.0.0.1:{port}/v1/chat/completions",
    )
    dev_token = ensure_internal_dev_token()

    user_prompt = prompts.build_summary_user_prompt(content, tool_name)

    try:
        async with httpx.AsyncClient(timeout=SUMMARY_TIMEOUT_SEC) as client:
            resp = await client.post(
                gateway_url,
                headers={
                    "Authorization": f"Bearer {dev_token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": origin_obj.name,
                    "messages": [
                        {"role": "system", "content": prompts.SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": SUMMARY_MAX_TOKENS,
                    "stream": False,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                text = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    or ""
                )
                text = text.strip()
                if len(text) > 240:
                    text = text[:240] + "…"
                if text:
                    return text, origin_obj.name
            logger.info(
                "summary_worker: %s 返 %d, 跳过 (员工同款规则, 不 fallback)",
                origin_obj.name, resp.status_code,
            )
    except Exception as e:  # noqa: BLE001
        logger.info("summary_worker: %s 异常 (%s), 跳过", origin_obj.name, type(e).__name__)
    return None, None


async def _process_one(row: dict) -> None:
    """处理一条 archive — 调 LLM 摘要, 写回 db.

    失败也写 error (跳过下次扫到).
    """
    ref = row["ref"]
    content = row["content"]
    tool_name = row.get("tool_name")
    origin_model = row.get("origin_model")  # BL-Q3-ARCHIVE fix2
    t0 = time.time()
    try:
        summary, model = await _summarize_one(content, tool_name, origin_model)
        if summary:
            db.update_summary(ref, summary=summary, model=model)
            logger.info(
                "summary_worker ref=%s ok model=%s len=%d took=%.1fs",
                ref, model, len(summary), time.time() - t0,
            )
        else:
            db.update_summary(
                ref, summary=None, model=None,
                error="LLM 摘要失败或全候选不可用",
            )
            logger.info("summary_worker ref=%s 摘要不可用, 标错跳过", ref)
    except Exception as e:  # noqa: BLE001
        db.update_summary(
            ref, summary=None, model=None,
            error=f"{type(e).__name__}: {str(e)[:180]}",
        )
        logger.warning("summary_worker ref=%s 异常: %s", ref, e)


async def summary_worker_loop() -> None:
    """主循环: 每 5s 扫一次, 处理一批."""
    logger.info("BL-Q3-ARCHIVE summary_worker 启动 (interval=%.1fs batch=%d)",
                SCAN_INTERVAL_SEC, BATCH_SIZE)
    while True:
        try:
            if not features.summary_enabled():
                await asyncio.sleep(SCAN_INTERVAL_SEC * 2)
                continue
            rows = db.pick_unsummarized(limit=BATCH_SIZE)
            if rows:
                # 串行处理, 不并发. 避免一次烧太多 quota.
                for r in rows:
                    await _process_one(r)
        except asyncio.CancelledError:
            logger.info("summary_worker_loop cancelled")
            return
        except Exception as e:  # noqa: BLE001
            logger.error("summary_worker_loop 异常 (5s 后重试): %s", e)
        await asyncio.sleep(SCAN_INTERVAL_SEC)


def start_summary_worker() -> asyncio.Task | None:
    """gateway app.py lifespan 调. 返 task 供 shutdown 时 cancel."""
    if not features.summary_enabled():
        logger.info(
            "BL-Q3-ARCHIVE summary_worker 跳过启动 (CATFISH_TOOL_ARCHIVE_SUMMARY=off)"
        )
        return None
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(summary_worker_loop(), name="tool_archive_summary_worker")
    except RuntimeError:
        logger.warning("start_summary_worker: 没 event loop, 跳过 (单测场景 OK)")
        return None


__all__ = [
    "SCAN_INTERVAL_SEC",
    "BATCH_SIZE",
    "summary_worker_loop",
    "start_summary_worker",
]
