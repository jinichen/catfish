"""四个 LLM 调用 (summarize / distill / analysis / generation) —— 拆出 (8/15)。

依赖 prompts (模板) 和 helpers 的 gateway token / 超时常量 / logger。

httpx 是函数体内懒 import, 跟着函数一起搬过来了 —— plugin 装的时候 hermes
全套依赖已经在, 但顶层 import 会让没装 httpx 的环境连模块都加载不了。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

# 本模块有两种加载方式, import 形式必须两种都活:
#   · hermes 进程内 —— plugins/memory/__init__.py 用 spec_from_file_location
#     + submodule_search_locations 加载, 是真包, **相对 import 才 work**
#     (它全程不碰 sys.path, 裸绝对 import 找不到兄弟模块)
#   · wiki_health.py 独立脚本 —— 自己 sys.path.insert, 此时没有父包,
#     相对 import 反过来会炸
# 见 tests/test_loader_fidelity.py, 那里每种方式各起一个干净子进程验。
try:
    from .catfish_memory_helpers import _DISTILL_CHUNK_CHARS, _GENERATION_HTTP_TIMEOUT, _LLM_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, _log_token_missing, logger  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import _DISTILL_CHUNK_CHARS, _GENERATION_HTTP_TIMEOUT, _LLM_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, _log_token_missing, logger  # noqa: F401

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
    from .catfish_memory_prompts import _ANALYSIS_PROMPT, _DISTILL_PROMPT, _SUMMARIZE_PROMPT, _build_generation_prompt  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_prompts import _ANALYSIS_PROMPT, _DISTILL_PROMPT, _SUMMARIZE_PROMPT, _build_generation_prompt  # noqa: F401


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


async def _call_distill_llm(
    journal_text: str, model: str,
    progress_cb=None,
) -> Optional[str]:
    """调 gateway 蒸馏老 journal. 失败返 None.

    跟 memory_distill.maybe_run_llm_distillation 行为等价 (简化版):
      - 切 _DISTILL_CHUNK_CHARS 大小 chunk
      - 每 chunk 走 gateway 抽人/项目/偏好/决策
      - 全部失败返 None, 部分成功合并返

    P3.5.1.1 (6/15 鸿波 Dream Engine): 加可选 progress_cb(done_idx, total) —
      Dream Engine UI 进度条用. 每 chunk 跑前调一次 (done_idx 从 0 开始 = "马上跑第 1 段"),
      全部跑完再调一次 (done=total). 现有 sync_turn/on_session_end caller 不传 = None,
      不影响行为. progress_cb 抛错被吞 (诊断 UI 挂不该拖累 distill).
    """
    if not journal_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("distill (Dream Engine 长期记忆蒸馏)")
        return None
    try:
        import httpx
    except ImportError:
        return None

    # 简单切 chunk (按字符, 不按 ## 段边界 — POC 阶段够用; 老 gateway 切段边界更精细)
    chunks: List[str] = []
    remaining = journal_text
    while remaining:
        chunks.append(remaining[:_DISTILL_CHUNK_CHARS])
        remaining = remaining[_DISTILL_CHUNK_CHARS:]
    if not chunks:
        return None

    total = len(chunks)

    def _notify(done: int) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(done, total)
        except Exception as e:  # noqa: BLE001
            logger.debug("distill progress_cb 抛错 (吞掉, 不影响 distill): %s", e)

    results: List[str] = []
    async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
        for idx, chunk in enumerate(chunks):
            _notify(idx)
            try:
                resp = await client.post(
                    with_source(_gateway_url(), "plugin:memory-distill"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Catfish-Skip-Identity": "true",
                        "X-Catfish-Internal": "true",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": _DISTILL_PROMPT + "\n\n" + chunk}],
                        "temperature": 0.2,
                        "max_tokens": 1000,
                        "stream": False,
                    },
                )
                if resp.status_code != 200:
                    logger.debug(
                        "catfish-memory distill chunk HTTP %d, skip 这段",
                        resp.status_code,
                    )
                    continue
                data = resp.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                text = text.strip()
                if text:
                    results.append(f"### 蒸馏段 {len(results) + 1}\n\n{text}")
            except Exception as e:  # noqa: BLE001
                logger.debug("catfish-memory distill chunk 异常 (跳过): %s", e)
                continue

    _notify(total)  # 跑完通知一次

    if not results:
        return None
    return "\n\n".join(results)


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
