"""四个 LLM 调用 (summarize / distill / analysis / generation) —— 拆出 (8/15)。

依赖 prompts (模板) 和 helpers 的 gateway token / 超时常量 / logger。

httpx 是函数体内懒 import, 跟着函数一起搬过来了 —— plugin 装的时候 hermes
全套依赖已经在, 但顶层 import 会让没装 httpx 的环境连模块都加载不了。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 本模块有两种加载方式, import 形式必须两种都活:
#   · hermes 进程内 —— plugins/memory/__init__.py 用 spec_from_file_location
#     + submodule_search_locations 加载, 是真包, **相对 import 才 work**
#     (它全程不碰 sys.path, 裸绝对 import 找不到兄弟模块)
#   · wiki_health.py 独立脚本 —— 自己 sys.path.insert, 此时没有父包,
#     相对 import 反过来会炸
# 见 tests/test_loader_fidelity.py, 那里每种方式各起一个干净子进程验。
try:
    from .catfish_memory_helpers import _DISTILL_CHUNK_CHARS, _DISTILL_CONCURRENCY, _DISTILL_HTTP_TIMEOUT, _DISTILL_RETRIES, _GENERATION_HTTP_TIMEOUT, _LLM_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, _log_token_missing, logger  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import _DISTILL_CHUNK_CHARS, _DISTILL_CONCURRENCY, _DISTILL_HTTP_TIMEOUT, _DISTILL_RETRIES, _GENERATION_HTTP_TIMEOUT, _LLM_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, _log_token_missing, logger  # noqa: F401

# 8/15 晚: with_source 直接从 catfish_memory_gateway 拿, **不走 helpers**。
#
# 走 helpers 会撞循环: helpers 在文件末尾回指本模块, 所以本模块被加载时
# helpers 还只初始化了一半 —— 它顶部 re-export 过的名字 (_gateway_url 等) 拿得到,
# 后加的拿不到, 报 "cannot import name from partially initialized module"。
# 第一版就是这么写的, 当场炸了。
#
# gateway 模块是 base 侧, 不 import 本文件, 直接拿没有环。
try:
    from .catfish_memory_gateway import with_source
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_gateway import with_source
try:
    from .catfish_memory_prompts import _ANALYSIS_PROMPT, _DISTILL_PROMPT, _RECONCILE_PROMPT, _SUMMARIZE_PROMPT, _build_generation_prompt  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_prompts import _ANALYSIS_PROMPT, _DISTILL_PROMPT, _RECONCILE_PROMPT, _SUMMARIZE_PROMPT, _build_generation_prompt  # noqa: F401
try:
    from .catfish_memory_distill_reconcile import assemble, chunk_date_range, merge_segments_by_date, split_journal_entries, status_digest
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_distill_reconcile import assemble, chunk_date_range, merge_segments_by_date, split_journal_entries, status_digest


async def _call_summarize_llm(
    pairs: List[Tuple[str, str]], model: str,
) -> Optional[str]:
    """调 gateway loopback /v1/chat/completions 总结一次. 失败返 None.

    跟老 session_summarizer._summarize_with_llm 行为等价:
      - X-Catfish-Skip-Identity: 防 gateway 给这次内部调用又注入 SOUL/journal
      - X-Catfish-Internal: 跳 quota check
      - Authorization Bearer <dev_token>
      - temperature 0.3, max_tokens 600 (短总结)
    """
    if not pairs:
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("summarize (sync_turn 会话总结)")
        return None

    # 拼上下文
    context_lines = [f"[{role}]: {content[:500]}" for role, content in pairs]
    user_prompt = _SUMMARIZE_PROMPT + "\n\n会话历史:\n\n" + "\n\n".join(context_lines)

    try:
        import httpx  # 懒 import, plugin 装时已经依赖 hermes 全套
    except ImportError:
        logger.warning("catfish-memory: httpx 不可用, summarize skip")
        return None

    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                with_source(_gateway_url(), "plugin:memory-summarize"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "temperature": 0.3,
                    "max_tokens": 600,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory summarize: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            text = text.strip()
            return text or None
    except Exception as e:  # noqa: BLE001
        logger.warning("catfish-memory summarize 异常: %s", e)
        return None


async def _post_distill(client, headers: dict, body: dict, what: str) -> Optional[str]:
    """一次蒸馏类请求, 失败 (超时 / 非 200 / 异常) 重试 _DISTILL_RETRIES 次, 全败返 None。

    10/7 之前失败只 logger.debug 一行就 continue —— 四分之三的段这样没了, 日志里
    一个 warning 都没有。现在每次失败 warning, 最终放弃也 warning。
    """
    for attempt in range(1 + _DISTILL_RETRIES):
        try:
            resp = await client.post(
                with_source(_gateway_url(), "plugin:memory-distill"),
                headers=headers, json=body,
            )
            if resp.status_code == 200:
                text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                return text.strip() or None
            logger.warning("catfish-memory %s HTTP %d (第 %d 次)", what, resp.status_code, attempt + 1)
        except Exception as e:  # noqa: BLE001
            logger.warning("catfish-memory %s 异常 [%s] (第 %d 次): %s", what, type(e).__name__, attempt + 1, e)
    logger.warning("catfish-memory %s 放弃 (重试 %d 次仍失败)", what, _DISTILL_RETRIES)
    return None


async def _call_distill_llm(
    journal_text: str,
    model: str,
    progress_cb=None,
    *,
    prior_segments: Optional[List[Tuple[str, str]]] = None,
    report: Optional[dict] = None,
) -> Optional[str]:
    """调 gateway 蒸馏 journal. 失败返 None.

      - 按 journal 条目边界切 ≤ _DISTILL_CHUNK_CHARS 的段 (10/7: 不再按字符硬切)
      - 每段走 gateway 抽人/项目/偏好/决策/任务状态, 失败重试 _DISTILL_RETRIES 次
      - 全部失败且无 prior_segments 返 None, 否则合并"当前状态"后 assemble 返

    10/7 增量蒸馏:
      prior_segments: 上次 distilled_facts.md 里还原出来的旧段 (旧→新), 本次只蒸
        journal_text (= journal 里上次游标之后的新增), 两者拼起来再合并当前状态。
      report: 调用方给个 dict, 回填 chunks_total / chunks_failed / failed_ranges /
        consumed_chars / reconciled。失败段不影响其它段: 它们在 journal_text 里的
        字符区间记在 failed_ranges, distill_run 存成缺口下次单独补。

    P3.5.1.1 (6/15 鸿波 Dream Engine): progress_cb(done_idx, total) — Dream Engine
      UI 进度条用. 每 chunk 跑前调一次, 全部跑完再调一次 (done=total). 抛错被吞.
    """
    rep = report if report is not None else {}
    rep.update({"chunks_total": 0, "chunks_failed": 0, "consumed_chars": 0, "reconciled": False})
    prior = list(prior_segments or [])
    if not journal_text.strip() and not prior:
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("distill (Dream Engine 长期记忆蒸馏)")
        return None
    try:
        import httpx
    except ImportError:
        return None

    chunks = split_journal_entries(journal_text, _DISTILL_CHUNK_CHARS)
    total = len(chunks)
    rep["chunks_total"] = total

    def _notify(done: int) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(done, total)
        except Exception as e:  # noqa: BLE001
            logger.debug("distill progress_cb 抛错 (吞掉, 不影响 distill): %s", e)

    # 9/24: (日期范围, 蒸馏文本), 旧→新。最后合并出"当前状态"并按新在前输出, 见
    # catfish_memory_distill_reconcile.py (CMMI-5 拿证后又被当成进行中的根因)
    results: List[Tuple[str, str]] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Catfish-Skip-Identity": "true",
        "X-Catfish-Internal": "true",
        "Content-Type": "application/json",
    }
    # 10/7 晚实测: qwen-flash 单段 70–180s, 网关上游超时 180s → 前 8 段 3 段两次都 504。
    # 串行 + "第一段失败后全丢"会让一整轮 2 小时只剩 1 段。改成:
    #   · _DISTILL_CONCURRENCY 路并发 (结果按 idx 归位, 顺序不变)
    #   · 第一轮跑完, 失败的段**再补一轮** (离散超时大多是上游抖动, 隔几分钟就过)
    #   · 之后才按"连续成功前缀"推游标
    outputs: Dict[int, Optional[str]] = {}
    done_count = 0
    sem = asyncio.Semaphore(_DISTILL_CONCURRENCY)

    async def _one(client, idx: int, chunk: str, label: str) -> None:
        nonlocal done_count
        async with sem:
            text = await _post_distill(client, headers, {
                "model": model,
                "messages": [{"role": "user", "content": _DISTILL_PROMPT + "\n\n" + chunk}],
                "temperature": 0.2,
                "max_tokens": 1000,
                "stream": False,
            }, f"distill 第 {idx + 1}/{total} 段{label}")
            outputs[idx] = text
            done_count += 1
            _notify(min(done_count, total))

    async with httpx.AsyncClient(timeout=_DISTILL_HTTP_TIMEOUT) as client:
        _notify(0)
        await asyncio.gather(*(_one(client, i, c, "") for i, c in enumerate(chunks)))
        retry_idx = [i for i in range(total) if outputs.get(i) is None]
        if retry_idx:
            logger.warning("catfish-memory distill 第一轮 %d/%d 段失败, 补跑一轮", len(retry_idx), total)
            done_count = total - len(retry_idx)
            await asyncio.gather(*(_one(client, i, chunks[i], " (补跑)") for i in retry_idx))

        # 10/7 晚第二版: 成功段**全部保留**, 失败段只记下它在 journal_text 里的字符区间
        # (failed_ranges), 由 distill_run 存成"缺口"下次单独补。第一版"第一段失败后
        # 全丢"在补跑后仍有段失败时会把 2.5 小时 157 段的成果扔掉, 只留 11 段。
        failed = 0
        failed_ranges: List[Tuple[int, int]] = []
        pos = 0
        for idx, chunk in enumerate(chunks):
            text = outputs.get(idx)
            if text is None:
                failed += 1
                failed_ranges.append((pos, pos + len(chunk)))
            else:
                results.append((chunk_date_range(chunk), text))
            pos += len(chunk)
        rep["chunks_failed"] = failed
        rep["failed_ranges"] = failed_ranges
        rep["consumed_chars"] = len(journal_text)

        # 补缺口蒸出来的段要按日期插回原位 (prior 是旧→新, 缺口段可能落在中间)
        segments = merge_segments_by_date(prior, results)
        current: Optional[str] = None
        if segments:
            # 10/7 深夜: 12000 字的摘要照样撞网关 180s 超时 (qwen-flash 实测 6K token
            # prompt + 1500 token 输出 > 180s)。合并失败就把输入砍半再试, 最多砍到 1/4:
            # 摘要新在前, 砍掉的是最旧的段, "当前状态"要的本来就是最近的说法。
            digest = status_digest(segments)
            for frac in (1.0, 0.5, 0.25):
                part = digest[: max(1, int(len(digest) * frac))]
                current = await _post_distill(client, headers, {
                    "model": model,
                    "messages": [{"role": "user", "content": _RECONCILE_PROMPT + "\n\n" + part}],
                    "temperature": 0.1,
                    "max_tokens": 1500,
                    "stream": False,
                }, f"当前状态合并 (输入 {len(part)} 字)")
                if current is not None:
                    break
            if current is None:
                logger.warning("catfish-memory 当前状态合并失败, 用确定性兜底 (头部会标明)")
            rep["reconciled"] = current is not None

    _notify(total)  # 跑完通知一次

    if not segments:
        return None
    return assemble(segments, current, chunks_total=total, chunks_failed=failed)


# ── BL-CATFISH-WIKI-MODE P1.1 wiki two-step ─────────────────────

async def _call_analysis_llm(
    journal_text: str, model: str,
) -> Optional[str]:
    """Step 1 Analysis: 结构化抽 entities/concepts/decisions/contradictions.

    输入 = 全 journal_text (≤ _DISTILL_CHUNK_CHARS 真 chunk).
    输出 = markdown 结构化 (4 个 ## 段) 或 None (失败).

    比 _call_distill_llm 真区别: 输出结构化 (parseable), 不是 bullet list.
    """
    if not journal_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("wiki analysis (Step 1 实体抽取)")
        return None
    try:
        import httpx
    except ImportError:
        return None

    # 单 chunk 走 (journal 真大时切前 _DISTILL_CHUNK_CHARS — 最新优先 tail).
    chunk = journal_text[-_DISTILL_CHUNK_CHARS:] if len(journal_text) > _DISTILL_CHUNK_CHARS else journal_text

    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                with_source(_gateway_url(), "plugin:memory-wiki-analysis"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": _ANALYSIS_PROMPT + "\n\n" + chunk}],
                    "temperature": 0.2,
                    "max_tokens": 2500,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory analysis: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            # P1.1.1 debug (6/4): 写 raw Step 1 output 到 file. 跑通后删.
            try:
                _debug_dump = Path.home() / ".catfish" / "last_wiki_analysis.txt"
                _debug_dump.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            return text.strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "catfish-memory analysis 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None


def _existing_wiki_index(catfish_home: Path, limit: int = 400) -> str:
    r"""列现有 entity/concept 的 `slug ← title`, 拼成给 Generation LLM 的清单。

    8/4: prompt 里要求"同一个东西必须复用已有 slug", 那就得**真的把清单给它** ——
    在此之前 _call_generation_llm 只喂 analysis, LLM 完全不知道已有什么, 每次
    从零造 slug, 于是同一实体攒出一堆拼音变体 (实测 21 组 / 50 个文件)。

    写盘那道 _redirect_to_existing_equivalent 是确定性兜底 (只认规范化等价);
    这份清单是让 LLM 一开始就少造变体, 两者互补, 都不能省:
      · 清单降低发生率, 但 LLM 不保证遵守
      · 兜底保证同名变体一定合并, 但拦不住"中电福富" vs "中电福富信息科技有限公司"
        这种语义重复 —— 那个只有 LLM 看见清单才可能避免
    """
    lines = []
    for sub, kind in (("wiki/entities", "entity"), ("wiki/concepts", "concept")):
        d = catfish_home / sub
        if not d.is_dir():
            continue
        try:
            for p in sorted(d.iterdir()):
                if p.suffix != ".md":
                    continue
                try:
                    head = p.read_text(encoding="utf-8", errors="replace")[:400]
                except OSError:
                    continue
                title = ""
                for line in head.splitlines():
                    if line.strip().startswith("title:"):
                        title = line.split("title:", 1)[1].strip()
                        break
                lines.append(f"{kind}/{p.stem} ← {title or p.stem}")
        except OSError:
            continue
    if not lines:
        return ""
    truncated = len(lines) > limit
    body = "\n".join(lines[:limit])
    return (
        "## 现有条目 (同一个东西必须复用这里的 slug, 不要造新变体)\n\n"
        + body
        + ("\n… (还有更多, 已截断)" if truncated else "")
    )


async def _call_generation_llm(
    analysis: str, model: str, catfish_home: Optional[Path] = None,
) -> Optional[str]:
    """Step 2 Generation: 把 analysis 转 wiki pages (---FILE: sentinel).

    输入 = Step 1 真 analysis text (~2000 字).
    输出 = ---FILE: <path>--- 切分真 multi-file markdown 或 None.
    """
    if not analysis.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("wiki generation (Step 2 条目生成)")
        return None
    try:
        import httpx
    except ImportError:
        return None
    try:
        # P1.1.1 fix (6/4): generation 单独 180s timeout — 4096 tokens 生 LLM
        # 60s 不够 (12:55 ReadTimeout 实测).
        async with httpx.AsyncClient(timeout=_GENERATION_HTTP_TIMEOUT) as client:
            resp = await client.post(
                with_source(_gateway_url(), "plugin:memory-wiki-generation"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user",
                         "content": _build_generation_prompt()
                         + (("\n\n" + _existing_wiki_index(catfish_home)) if catfish_home else "")
                         + "\n\n## Analysis\n\n" + analysis}
                    ],
                    "temperature": 0.3,
                    # P1.1.1 fix (6/4): 7000 → 4096 兼容 deepseek-flash /
                    # catfish-private-main 真 output cap. Generation 真
                    # ~3 concept + ~5 entity 估 3500 tokens 够.
                    "max_tokens": 4096,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory generation: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            # P1.1.1 debug (6/4): 写 raw LLM output 到 file 看 sentinel 不符原因.
            # 跑通后删 (BL-CATFISH-WIKI-MODE P1.1.2 cleanup).
            try:
                _debug_dump = Path.home() / ".catfish" / "last_wiki_generation.txt"
                _debug_dump.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            return text.strip() or None
    except Exception as e:  # noqa: BLE001
        # P1.1.1 fix (6/4): str(e) 空时 type(e).__name__ + repr 真 hint —
        # 之前 'generation 异常: ' 空 message 直接看不出真什么 error.
        logger.warning(
            "catfish-memory generation 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None
