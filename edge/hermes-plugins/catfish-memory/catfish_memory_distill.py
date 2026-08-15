"""后台总结 / 蒸馏 —— 从 catfish_memory.py 拆出 (8/15 第 2 趟)。

sync_turn 的节流判定、后台 daemon 线程、跟 gateway 的双写去重、
以及 dream_cli.py 的入口 run_distill_for_dream_engine。

⚠ run_distill_for_dream_engine 是 dream_cli.py 用 `mem_mod.xxx` 取的
(dream_cli.py:113), 所以 catfish_memory 必须继续 re-export 它 —— 那条路径是
员工点"蒸馏"按钮时 Rust spawn 的子进程, 断了不会有任何测试变红。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import asyncio
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 相对优先绝对兜底 —— 见 tests/test_loader_fidelity.py
try:
    from .catfish_memory_helpers import (  # noqa: F401
        _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS,
        _DEFAULT_TURNS_BETWEEN_SUMMARY,
        _DISTILL_CHUNK_CHARS,
        _append_journal,
        _append_to_buffer,
        _call_analysis_llm,
        _call_distill_llm,
        _call_generation_llm,
        _call_summarize_llm,
        _catfish_home,
        _clear_buffer,
        _format_journal_entry,
        _list_pending_queries,
        _list_pending_sources,
        _load_plugin_config,
        _mark_distill_run,
        _mark_wiki_queries_ingested,
        _mark_wiki_sources_ingested,
        _parse_generation_output,
        _read_buffer,
        _read_full_journal,
        _read_picker_state_model,
        _read_queries_concat,
        _read_sources_concat,
        _read_state,
        _should_run_distill,
        _wiki_enabled,
        _write_distilled,
        _write_state,
        _write_wiki_files,
        merge_files_with_llm,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import (  # noqa: F401
        _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS,
        _DEFAULT_TURNS_BETWEEN_SUMMARY,
        _DISTILL_CHUNK_CHARS,
        _append_journal,
        _append_to_buffer,
        _call_analysis_llm,
        _call_distill_llm,
        _call_generation_llm,
        _call_summarize_llm,
        _catfish_home,
        _clear_buffer,
        _format_journal_entry,
        _list_pending_queries,
        _list_pending_sources,
        _load_plugin_config,
        _mark_distill_run,
        _mark_wiki_queries_ingested,
        _mark_wiki_sources_ingested,
        _parse_generation_output,
        _read_buffer,
        _read_full_journal,
        _read_picker_state_model,
        _read_queries_concat,
        _read_sources_concat,
        _read_state,
        _should_run_distill,
        _wiki_enabled,
        _write_distilled,
        _write_state,
        _write_wiki_files,
        merge_files_with_llm,
    )

# 跟 catfish_memory.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.memory.plugin")


async def run_distill_for_dream_engine(
    model: str,
    *,
    force: bool = True,
    progress_cb=None,
    catfish_home_override: Optional[Path] = None,
) -> Dict[str, Any]:
    """Dream Engine 入口: 立即蒸馏 employee_journal.md → distilled_facts.md.

    Args:
        model: companion picker 当前选的 model (e.g. catfish-public-deepseek-flash).
               必传, 跟 instance _get_summarize_model() (yaml/env) 隔离.
        force: True 跳 24h cooldown (员工主动触发场景默认 True).
               False 时 cooldown 内返 {ok: False, reason: 'cooldown'}.
        progress_cb: 可选 (done_idx, total) -> None. 透传给 _call_distill_llm.
        catfish_home_override: 测试用 — 覆盖 ~/.catfish.

    Returns:
        {
          "ok": bool,
          "reason": str,            # 失败原因 (cooldown / empty_journal / llm_fail / model_empty)
          "chunks_total": int,      # 切了几段
          "bytes_written": int,     # 写了多少字节 distilled_facts.md
          "model": str,             # 实际用的 model
          "took_seconds": float,
        }
    """
    started = time.time()

    if not model or not model.strip():
        return {
            "ok": False, "reason": "model_empty",
            "chunks_total": 0, "bytes_written": 0,
            "model": "", "took_seconds": 0.0,
        }
    model = model.strip()

    home = catfish_home_override or _catfish_home()

    # cooldown check (force=True 时跳)
    if not force and not _should_run_distill(home):
        return {
            "ok": False, "reason": "cooldown",
            "chunks_total": 0, "bytes_written": 0,
            "model": model, "took_seconds": time.time() - started,
        }

    # 读 journal 全文
    journal_text = _read_full_journal(home)
    if not journal_text.strip():
        return {
            "ok": False, "reason": "empty_journal",
            "chunks_total": 0, "bytes_written": 0,
            "model": model, "took_seconds": time.time() - started,
        }

    # 估 chunk 数, 给 progress_cb 早期反馈 (不调 _call_distill_llm 之前)
    estimated_chunks = max(1, (len(journal_text) + _DISTILL_CHUNK_CHARS - 1) // _DISTILL_CHUNK_CHARS)
    if progress_cb is not None:
        try:
            progress_cb(0, estimated_chunks)
        except Exception:  # noqa: BLE001
            pass

    distilled = await _call_distill_llm(
        journal_text, model, progress_cb=progress_cb,
    )

    if not distilled:
        return {
            "ok": False, "reason": "llm_fail",
            "chunks_total": estimated_chunks, "bytes_written": 0,
            "model": model, "took_seconds": time.time() - started,
        }

    _write_distilled(home, distilled)
    # 写 cooldown state — 让 plugin auto path 24h 内自然 skip (零冲突核心)
    _mark_distill_run(home)

    return {
        "ok": True, "reason": "",
        "chunks_total": estimated_chunks,
        "bytes_written": len(distilled.encode("utf-8")),
        "model": model,
        "took_seconds": time.time() - started,
    }


class _DistillMixin:
    """见模块 docstring。"""

    async def _summarize_and_distill_async(
        self,
        session_id: str,
        message_pairs: List[Tuple[str, str]],
        model: str,
        catfish_home: Path,
    ) -> None:
        """后台 thread 主体: 总结 → 写 journal → 看条件蒸馏 → 写 distilled.

        失败静默 (catfish 边缘文件丢一次不致命, 下次 session 还会触发).
        """
        try:
            # 1. 总结
            summary = await _call_summarize_llm(message_pairs, model)
            if not summary:
                # BL-CATFISH-MEMORY-SUMMARIZE-UPSTREAM-502 (6/4 凌晨):
                # 之前 silent drop → catfish gateway 100% 502 时 journal 全丢.
                # 改 raw fallback: 保 data 不丢. LLM 总结异步补 (下次 distill 跑).
                summary = self._build_raw_journal_fallback(message_pairs)
                logger.info(
                    "catfish-memory bg session=%s: LLM 总结返空 → "
                    "改写 raw fallback (%d pairs 保留)",
                    session_id, len(message_pairs),
                )

            # 2. 写 journal
            entry = _format_journal_entry(session_id, summary)
            _append_journal(catfish_home, entry)
            logger.info(
                "catfish-memory bg session=%s: ✓ 写 journal %d 字节",
                session_id, len(entry.encode("utf-8")),
            )

            # 3. 24h 间隔满 OR queries / sources 有未 ingest file → 跑 distill
            #    P1.2.3 (6/4): queries 触发也跑 — 绕 24h cooldown.
            #    P16 (6/5): sources 触发也跑 — 对话上传文件即时入库.
            cooldown_passed = _should_run_distill(catfish_home)
            # 8/3: sources 跟 queries 分家 —— 它们本来就是两件相反的事。
            #
            # 6/16 鸿波"对话自动入知识库会很乱" → 加 _wiki_enabled() 守门, 默认关。
            # 要关的是「**聊天内容自动**变成 entity」。但同一个门也罩住了
            # wiki/raw/sources/ —— 而那个目录里的东西是 catfish_wiki_ingest 放的,
            # 那个工具的第一句描述就是「**员工显式**要求把 chat 附件存到知识库时
            # 才调」。
            #
            # 于是 auto_ingest: false 的机器上, 员工明说"存进知识库", 文件写进
            # raw/sources/ 之后**没有任何东西会读它** —— 不是 24 小时后, 是永远。
            # ingested_state 永不更新, sources 无限堆积, 全程零报错。
            # 8/3 那份对标矩阵反复"看不到", 根因在这。
            #
            # 现在: queries (聊天派生) 仍受 auto_ingest 管; sources (员工显式)
            # 不受。下面 combined_input 那里会保证关着 auto_ingest 时**不把
            # journal 喂进去** —— 否则等于从后门把 6/16 禁掉的事又打开了。
            pending_queries = _list_pending_queries(catfish_home) if _wiki_enabled() else []
            pending_sources = _list_pending_sources(catfish_home)
            if not cooldown_passed and not pending_queries and not pending_sources:
                return
            journal_text = _read_full_journal(catfish_home)
            if not journal_text and not pending_queries and not pending_sources:
                return

            # 3a. legacy single-step distill (cooldown 满才跑, queries 触发不重复跑)
            if cooldown_passed and journal_text:
                distilled = await _call_distill_llm(journal_text, model)
                if distilled:
                    _write_distilled(catfish_home, distilled)
                    _mark_distill_run(catfish_home)
                    logger.info(
                        "catfish-memory bg session=%s: ✓ 蒸馏 %d 字节 (distilled_facts.md)",
                        session_id, len(distilled.encode("utf-8")),
                    )

            # 3b. BL-CATFISH-WIKI-MODE P1.1 (6/4): wiki two-step ingest
            #     CATFISH_WIKI_ENABLE=1 默认 off (LLM 调用贵, 24h 1 次).
            #     Step 1 Analysis → Step 2 Generation → parse + 写文件 + journal
            #     P1.2.3 (6/4): queries 真有未 ingest file → 合并真 Analysis input
            # 8/3: auto_ingest 关着, 但有员工显式入库的 source → 也要跑。
            explicit_only = not _wiki_enabled()
            if _wiki_enabled() or pending_sources:
                try:
                    # P1.2.3 / P16 (6/5): 合并 journal + queries + sources 真 Analysis input
                    #
                    # 8/3 explicit_only: auto_ingest 关着时**只喂 sources**, 不喂
                    # journal 也不喂 queries。这一条是拆开两个开关的前提 —— 如果
                    # 顺手把 journal 一起喂进去, 聊天内容照样会被蒸出 entity,
                    # 等于绕过 6/16 的决定, 那才是真的坏事。
                    combined_input = "" if explicit_only else journal_text
                    if pending_queries:
                        queries_text = _read_queries_concat(pending_queries)
                        if queries_text:
                            combined_input += (
                                "\n\n## Recent chat queries (P1.2)\n\n" + queries_text
                            )
                            logger.info(
                                "catfish-memory bg session=%s: P1.2.3 queries 触发, "
                                "%d files merged into Analysis input",
                                session_id, len(pending_queries),
                            )
                    if pending_sources:
                        sources_text = _read_sources_concat(pending_sources)
                        if sources_text:
                            combined_input += (
                                "\n\n## Uploaded raw sources (P16)\n\n" + sources_text
                            )
                            logger.info(
                                "catfish-memory bg session=%s: P16 sources 触发, "
                                "%d files merged into Analysis input",
                                session_id, len(pending_sources),
                            )
                    analysis = await _call_analysis_llm(combined_input, model)
                    if not analysis:
                        logger.info(
                            "catfish-memory bg session=%s: wiki Step 1 analysis 返空 (skip)",
                            session_id,
                        )
                    else:
                        generation = await _call_generation_llm(analysis, model, catfish_home)
                        if not generation:
                            logger.info(
                                "catfish-memory bg session=%s: wiki Step 2 generation 返空 (skip)",
                                session_id,
                            )
                        else:
                            files = _parse_generation_output(generation)
                            # P19 (6/5 鸿波): 同名 entity/concept 让 LLM 合并叙述
                            # (不是 P18 regex 留底). LLM merge 失败的 path 落 P18
                            # regex 安全网, 不丢数据.
                            llm_merged_paths: set = set()
                            if files:
                                try:
                                    merged_files, llm_merged_paths, n_llm_fail = await merge_files_with_llm(
                                        catfish_home, files, model,
                                    )
                                    files = merged_files
                                    if llm_merged_paths or n_llm_fail:
                                        logger.info(
                                            "catfish-memory bg session=%s: P19 LLM merge "
                                            "%d ok / %d fail (fail → P18 regex 安全网)",
                                            session_id, len(llm_merged_paths), n_llm_fail,
                                        )
                                except Exception as e:  # noqa: BLE001
                                    logger.warning(
                                        "catfish-memory P19 merge_files_with_llm 异常 (fallback P18): %s",
                                        e,
                                    )
                            n_e, n_c = _write_wiki_files(catfish_home, files, skip_merge_paths=llm_merged_paths)
                            if n_e + n_c > 0:
                                # journal 加 distill entry — Karpathy log.md 风格
                                ts_short = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
                                distill_entry = (
                                    f"\n## [{ts_short}] distill | "
                                    f"{n_e} entities, {n_c} concepts\n\n"
                                    f"wiki/entities/ + wiki/concepts/ 已更新 "
                                    f"({len(files)} pages, ~{len(generation)//1024}KB).\n"
                                )
                                _append_journal(catfish_home, distill_entry)
                                logger.info(
                                    "catfish-memory bg session=%s: ✓ wiki ship %d entities + %d concepts",
                                    session_id, n_e, n_c,
                                )
                                # P1.2.3: mark queries 已 ingest, 下次不重复
                                if pending_queries:
                                    _mark_wiki_queries_ingested(
                                        catfish_home,
                                        [p.name for p in pending_queries],
                                    )
                                    logger.info(
                                        "catfish-memory bg session=%s: ✓ marked %d queries ingested",
                                        session_id, len(pending_queries),
                                    )
                                # P16 (6/5): mark sources 已 ingest, 下次不重复
                                if pending_sources:
                                    _mark_wiki_sources_ingested(
                                        catfish_home,
                                        [p.name for p in pending_sources],
                                    )
                                    logger.info(
                                        "catfish-memory bg session=%s: ✓ marked %d sources ingested",
                                        session_id, len(pending_sources),
                                    )
                            else:
                                logger.info(
                                    "catfish-memory bg session=%s: wiki parse 0 file (LLM 输出不符 sentinel)",
                                    session_id,
                                )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "catfish-memory bg session=%s: wiki two-step 异常 (静默): %s",
                        session_id, e,
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "catfish-memory bg session=%s 异常 (静默): %s", session_id, e,
            )

    def _sync_turn_impl(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str,
    ) -> None:
        # yaml/env 配置 — yaml 优先, env 兜底, default fallback
        if not self._is_summarize_enabled():
            return
        model = self._get_summarize_model()
        if not model:
            return

        # 过滤非 str / 空 content
        if not isinstance(user_content, str) or not isinstance(assistant_content, str):
            return
        if not user_content.strip() or not assistant_content.strip():
            return

        # Update self._session_id 跟最新
        if session_id:
            self._session_id = session_id

        home = self._catfish_home_cached or _catfish_home()

        # 累积到文件 buffer (跨 instance 持久化 — 因为 hermes api_server 每 chat 新 instance)
        _append_to_buffer(home, "user", user_content, session_id)
        new_entry_count = _append_to_buffer(home, "assistant", assistant_content, session_id)
        n_pairs = new_entry_count // 2

        # 读 state (last_summary_ts)
        state = _read_state(home)
        last_ts = float(state.get("last_summary_ts", 0))

        # 节流判断
        n_threshold = self._get_n_turns_threshold()
        min_interval = self._get_min_interval_seconds()
        now = time.time()
        elapsed = (now - last_ts) if last_ts > 0 else float("inf")

        triggered_by_n = n_pairs >= n_threshold
        # 时间节流: 必须 last_ts 真有值 (>0) 且超 min_interval 且至少 1 pair
        triggered_by_time = (
            last_ts > 0 and elapsed >= min_interval and n_pairs >= 1
        )

        # P16 (6/5 鸿波): 上传文件后立即触发 — wiki/raw/sources/ 或 wiki/queries/
        # `pending` → 不等 5 pair 不等 30 分钟, 当前轮就 trigger bg distill.
        # 体感: 拖个 PDF 进 chat + 一句"记一下" → 几十秒后就能在知识体系看到新 entity.
        # 触发 LLM 调用一次 (Analysis+Generation), 走 fallback chain.
        triggered_by_pending_ingest = False
        try:
            # 8/3: 跟下面 pending 列表同一个拆分 —— 员工显式入库的 source 要能
            # 触发即时蒸馏, 不受 auto_ingest 管; queries (聊天派生) 仍受管。
            # 不改这里的话, 开了下面那道门也只能等下一次 cooldown, 员工存完还是
            # 看不到东西。
            if n_pairs >= 1:
                if _list_pending_sources(home):
                    triggered_by_pending_ingest = True
                elif _wiki_enabled() and _list_pending_queries(home):
                    triggered_by_pending_ingest = True
        except Exception:  # noqa: BLE001
            pass

        if not (triggered_by_n or triggered_by_time or triggered_by_pending_ingest):
            return

        # Trigger: snapshot buffer + 清 file + update state
        snapshot_pairs = _read_buffer(home)
        _clear_buffer(home)
        _write_state(home, {"last_summary_ts": now})

        # 双写期幂等 — gateway 刚写过 file mtime 比 plugin 上次 trigger 还新, skip
        if self._journal_written_externally(last_ts):
            logger.info(
                "catfish-memory sync_turn: external journal write detected, skip dup"
            )
            return

        self._spawn_summarize_thread(snapshot_pairs, model)

    # ── 写路径: sync_turn + 节流 (BL-MEMORY-SYNC-TURN-REFACTOR, 5/20) ───
    #
    # 替代 gateway 旧 session_summarizer + memory_distill module.
    #
    # # 为啥不是 on_session_end (Step D 失败教训, 2026-05-20 早 6h debug)
    #
    # hermes 的 memory_provider.on_session_end 只在 **真 session boundary** 触发:
    #   - CLI atexit / `/reset` / `/new` / context compression / gateway session expiry
    #   - 详见 run_agent.py:16078-16091 注释 "Memory provider on_session_end NOT called
    #     per turn — would kill provider before second message"
    #
    # Companion 切 session 在 hermes 这边是 "SSE disconnected; interrupted agent task",
    # 不走 session_end path → 我们的 on_session_end 实现**永远不会** trigger →
    # journal 永远不被写. 5/20 早实测验证了这点 (catfish-debt-audit 2026-05-19).
    #
    # # 正确机制: sync_turn + 节流
    #
    # sync_turn 是 hermes per-turn hook (每轮 user+assistant 完成调一次).
    # 但每轮都跑 LLM summary 太贵 (烧 token), 用节流:
    #   - 节流 A: 累积 N 轮 (default 5, env CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS)
    #   - 节流 B: 距上次 summary >= X 秒 (default 1800=30min, env CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS)
    #   - 任一满足触发. 触发后 reset counter + buffer.
    #
    # # 兜底: on_session_end 仍保留作 force-flush
    #
    # session 真结束时 (CLI exit / reset), 把没满 N 轮的剩余 buffer 也 summarize 一次,
    # 不丢最后一段对话.
    #
    # # 设计原则
    #   - **fire-and-forget**: hermes 同步调 sync_turn, 我们 spawn daemon thread 跑 async
    #     LLM 调用, 不阻塞 hermes 主流程
    #   - **双写期幂等**: deploy 时 gateway 旧 caller 还在跑, journal mtime <
    #     SUMMARIZE_DEDUP_SECONDS 视为 gateway 刚写过, plugin skip 防 dup
    #   - **不抛**: 全 try/except, 任何错都不能让 hermes 挂
    #   - **线程安全**: _turn_buffer / _turns_since_last_summary / _last_summary_ts
    #     的访问全部走 _buffer_lock 保护

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        """hermes per-turn hook — 累积 + 节流 trigger summary (fire-and-forget)."""
        # --- BL-MEMORY-SYNC-TURN-REFACTOR Day 2 trace (5/20): 验证 hermes 调到 + 节流状态
        try:
            _home = self._catfish_home_cached or _catfish_home()
            _file_pairs = len(_read_buffer(_home))
            _file_state = _read_state(_home)
            with open(os.path.expanduser("~/.catfish/_sync_turn_trace.log"), "a") as _tf:
                _tf.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"sync_turn: inst={id(self):x} "
                    f"file_pairs_before={_file_pairs} "
                    f"last_summary_ts={_file_state.get('last_summary_ts', 0)} "
                    f"u_len={len(user_content or '')} "
                    f"a_len={len(assistant_content or '')} "
                    f"sid={session_id[:12]}\n"
                )
        except Exception:
            pass
        # --- END trace
        try:
            self._sync_turn_impl(user_content, assistant_content, session_id)
        except Exception as e:  # noqa: BLE001 - 全 catch, 不能让 hermes 挂
            logger.warning("catfish-memory sync_turn 失败 (静默): %s", e)
        # BL-SESSION-META-PLUGIN-TAKEOVER (5/26): 每轮 tick 时间感. 老逻辑 gateway
        # session_meta.tick() 在 chat 完成后写, SaaS 化后 gateway 不能写员工本机.
        # plugin 跑员工 mac 写自己 fs 合规. 这里跟 sync_turn buffer 累积同 hook,
        # 不再依赖 gateway 触发. 实际 chat 走 catfish-public 同款规则 (内部调用 + service
        # token 不算"员工跟我聊", 这两个走 gateway 路径不会触发 sync_turn 所以天然 skip).
        try:
            self._tick_session_meta()
        except Exception as e:  # noqa: BLE001
            logger.warning("catfish-memory tick_session_meta 失败 (静默): %s", e)

    def _get_summarize_model(self) -> str:
        """LLM model. 优先级 picker_state.json > role_resolver("summarize") > yaml > env. 没设返空字符串.

        P3.5.2 (6/16 鸿波): 加 picker_state.json 最高优先级 — companion chat.ts 每次 send
        前 fire-and-forget 写 ~/.catfish/picker_state.json 含当前 picker model. 这让 plugin
        sync_turn 自动跟随 picker, 解决方案 D 的 split 问题 (员工切 picker 后 summary 模型
        立即同步, 不再 yaml 静态).

        P3.5.29 Phase 7 (6/17 鸿波): 加 role_resolver("summarize") second tier —
        客户改 roles.yaml `summarize: customer-x-long-ctx` → plugin sync_turn
        自动跟着走, 不需改 catfish-memory yaml. picker 优先
        (员工临时切), role 默认 (客户部署值), yaml/env 老兜底.

        真因 audit: hermes MemoryProvider.sync_turn 签名是
        `(user_content, assistant_content, session_id)`, 没 client request header 入参.
        plugin 直接拿不到 picker. 文件中转是 hermes API 限制下的最简解法.

        文件不存在 / parse 错 / chat_model 缺 → fallback role_resolver → fallback yaml → fallback env.
        兼容老路径 (yaml/env 仍可 override role_resolver).
        """
        home = self._catfish_home_cached or _catfish_home()

        # P3.5.2: picker_state.json 最高优先级 (chat.ts 每次 send 写)
        picker_model = _read_picker_state_model(home)
        if picker_model:
            return picker_model

        # P3.5.29 Phase 7: role_resolver("summarize") second tier.
        # fail-silent: gateway 挂 / httpx 没装 / 网络抖 → 返 None → 走 yaml.
        try:
            from . import role_resolver  # noqa: PLC0415
            role_model = role_resolver.resolve("summarize")
            if role_model:
                return role_model
        except Exception:  # noqa: BLE001
            # import 失败 (旧 plugin tree, role_resolver.py 没装) — silent fallback.
            pass

        # Fallback: yaml > env (老逻辑保留, 兼容客户已有 catfish-memory yaml override)
        cfg = _load_plugin_config(home)
        yaml_val = cfg.get("summarize", {}).get("model") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, str) and yaml_val.strip():
            return yaml_val.strip()
        return os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MODEL", "").strip()

    def _journal_written_externally(self, our_last_ts: float) -> bool:
        """journal 文件 mtime 显著新于 plugin 自己上次 summary 时间 → 是别人 (gateway) 写的.

        BL-MEMORY-SYNC-TURN-REFACTOR (5/20): 替代老 _journal_recently_written.
        老逻辑用 file mtime vs absolute (5min) 判断"刚写过", 但 plugin 自己写完
        journal 后下次触发时也撞这条, **被自己 skip**. test_sync_turn_buffer_resets
        测试暴露了这个 bug.

        新逻辑: file mtime > our_last_ts + 5s buffer → 真有别人 (gateway 老 caller)
        在 plugin 上次 trigger 之后写了 journal, 双写期防 dup.

        5s buffer 避开:
          - macOS HFS+/APFS timestamp resolution (默认 1s 但可能不准)
          - lock 释放 → spawn thread 跑 LLM 调用 → 写 file 这段 race window
        """
        # our_last_ts <= 0 表示 plugin 没"上次 summary" — 不能判断别人写过. 第一次
        # trigger 时这种情况, journal 即使有 (gateway 时代留下的) 也不该 skip plugin.
        if our_last_ts <= 0:
            return False
        path = (self._catfish_home_cached or _catfish_home()) / "employee_journal.md"
        try:
            if not path.exists():
                return False
            mtime = path.stat().st_mtime
            return mtime > our_last_ts + 5.0
        except OSError:
            return False

    def _build_raw_journal_fallback(
        self, pairs: List[Tuple[str, str]]
    ) -> str:
        """LLM 总结失败时, 保留最近 N pairs raw 作 journal 内容, 避免 silent drop.

        BL-CATFISH-MEMORY-SUMMARIZE-UPSTREAM-502 (6/4 凌晨 ship, BACKLOG 9c15942):
        catfish gateway 上游 100% 502 → 之前 silent drop → journal 全丢.
        改 raw fallback: 保 data 不丢, 符合 catfish 价值观 "中央不存 = 中央不管"
        (raw 留, LLM 总结异步补).

        每 pair 截 200 chars + 最多 keep 5 pairs, 避免 entry 过长.
        """
        keep_last_n = min(5, len(pairs))
        if keep_last_n == 0:
            return "[LLM 总结失败 (上游 502 等), 0 pairs 保留]"
        lines = [
            f"[LLM 总结失败 (上游 502 等), raw {keep_last_n} pairs 保留 — "
            "可下次 sync_turn 重新蒸馏]",
            "",
        ]
        for u, a in pairs[-keep_last_n:]:
            u_short = (u or "").strip().replace("\n", " ")[:200]
            a_short = (a or "").strip().replace("\n", " ")[:200]
            lines.append(f"- 鸿波: {u_short}")
            lines.append(f"- 小鲶: {a_short}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _spawn_summarize_thread(
        self,
        pairs: List[Tuple[str, str]],
        model: str,
    ) -> None:
        """spawn 后台 daemon thread 跑 async LLM summary + 写文件. fire-and-forget."""
        if not pairs:
            return
        sid = self._session_id
        catfish_home = self._catfish_home_cached or _catfish_home()
        thread = threading.Thread(
            target=lambda: asyncio.run(
                self._summarize_and_distill_async(sid, pairs, model, catfish_home)
            ),
            name=f"catfish-memory-summarize-{sid[:8]}" if sid else "catfish-memory-summarize",
            daemon=True,
        )
        thread.start()
        logger.info(
            "catfish-memory sync_turn trigger: session=%s pairs=%d (n_turns=%d / threshold=%d)",
            sid, len(pairs), len(pairs) // 2,
            self._get_n_turns_threshold(),
        )

    def _force_flush(self) -> None:
        """触发 buffer 里剩余对话的一次 summary (即使没满 N 轮)."""
        if not self._is_summarize_enabled():
            return
        model = self._get_summarize_model()
        if not model:
            return

        home = self._catfish_home_cached or _catfish_home()
        snapshot_pairs = _read_buffer(home)
        if not snapshot_pairs:
            return

        # 读 state 拿 last_ts (给 mtime check 用), 然后 clear + update
        state = _read_state(home)
        last_ts = float(state.get("last_summary_ts", 0))
        _clear_buffer(home)
        _write_state(home, {"last_summary_ts": time.time()})

        if self._journal_written_externally(last_ts):
            logger.info(
                "catfish-memory force_flush: external journal write detected, skip dup"
            )
            return

        self._spawn_summarize_thread(snapshot_pairs, model)

    def _get_min_interval_seconds(self) -> int:
        """节流 B 阈值 (秒). 优先 yaml > env > default 1800."""
        cfg = _load_plugin_config(self._catfish_home_cached or _catfish_home())
        yaml_val = cfg.get("summarize", {}).get("min_interval_seconds") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, int):
            return max(0, yaml_val)
        raw = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "").strip()
        if raw:
            try:
                return max(0, int(raw))
            except ValueError:
                pass
        return _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS

    def _get_n_turns_threshold(self) -> int:
        """节流 A 阈值 (每 N 轮). 优先 yaml > env > default 5."""
        # yaml 优先
        cfg = _load_plugin_config(self._catfish_home_cached or _catfish_home())
        yaml_val = cfg.get("summarize", {}).get("every_n_turns") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, int):
            return max(1, yaml_val)
        # env 兜底
        raw = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
        return _DEFAULT_TURNS_BETWEEN_SUMMARY

    def _is_summarize_enabled(self) -> bool:
        """启用 plugin summary 写路径. 优先 yaml.enabled, env=0 关掉."""
        cfg = _load_plugin_config(self._catfish_home_cached or _catfish_home())
        yaml_val = cfg.get("enabled") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, bool):
            return yaml_val
        # env 兜底: =0 关掉, 否则启用 (默认 enabled)
        return os.environ.get("CATFISH_PLUGIN_SUMMARIZE", "1") != "0"

