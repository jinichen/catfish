"""catfish-memory 插件的共享 helper —— 现在主要是**门面**。

8/15 第二次拆: 949 → 约 400。第一次是同一天早些时候的 2522 → 949
(prompts / fm / wiki / llm / merge 五个模块)。

# 这个文件为什么还在

外部有 58 个名字写的是 `from catfish_memory_helpers import ...` ——
catfish_memory.py 五十来个、wiki_health.py 五个、tests/ 二十来个。
所以它保留成一个门面: 自己只留几个还没归类的 helper, 其余全部 re-export。

# 分层 (8/15 第二次拆之后)

    catfish_memory_base.py          logger / 预算表 / 常量 / 安全读文件
                                    ← **只依赖标准库, 谁都不 import**
    catfish_memory_buffer.py        会话 buffer + state 落盘 (零仓内依赖)
    catfish_memory_wiki_state.py    wiki 摄取状态
    catfish_memory_distill_state.py 蒸馏 cooldown
    catfish_memory_gateway.py       网关地址 + dev token

上面五个都**不 import 本文件**, 所以它们跟下面那个老环没关系。

# ⚠ 下面那个 re-export 块的顺序不能动

prompts / fm / wiki / llm / merge 这五个老模块**反过来** import 本文件的
logger。它们的 re-export 必须留在文件**末尾** —— 本模块执行到那里时, 上面
的符号都已经定义好了, 子模块的回指才拿得到。

8/15 第二次拆时特意把新的五个模块放在**顶部**, 而不是塞进末尾那个块:
它们不参与那个环, 混在一起会让"哪些必须在末尾"这件事看不出来。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ── 8/15 第二次拆出去的五个模块 (它们不 import 本文件, 无环) ──────────

# base 层: logger / 常量 / 安全读文件
try:
    from .catfish_memory_base import (  # noqa: F401
        _BUDGETS,
        _DEFAULT_CATFISH_HOME,
        _DEFAULT_GATEWAY_URL,
        _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS,
        _DEFAULT_TURNS_BETWEEN_SUMMARY,
        _DISTILL_CHUNK_CHARS,
        _DISTILL_COOLDOWN_SECONDS,
        _GENERATION_HTTP_TIMEOUT,
        _LLM_HTTP_TIMEOUT,
        _MAX_MESSAGES_PER_SUMMARY,
        _SUMMARIZE_DEDUP_SECONDS,
        _catfish_home,
        _read_jsonl_tail,
        _read_text_safe,
        logger,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_base import (  # noqa: F401
        _BUDGETS,
        _DEFAULT_CATFISH_HOME,
        _DEFAULT_GATEWAY_URL,
        _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS,
        _DEFAULT_TURNS_BETWEEN_SUMMARY,
        _DISTILL_CHUNK_CHARS,
        _DISTILL_COOLDOWN_SECONDS,
        _GENERATION_HTTP_TIMEOUT,
        _LLM_HTTP_TIMEOUT,
        _MAX_MESSAGES_PER_SUMMARY,
        _SUMMARIZE_DEDUP_SECONDS,
        _catfish_home,
        _read_jsonl_tail,
        _read_text_safe,
        logger,
    )

# 会话 buffer + state 落盘
try:
    from .catfish_memory_buffer import (  # noqa: F401
        _BUFFER_FILENAME,
        _STATE_FILENAME,
        _append_to_buffer,
        _buffer_file_path,
        _clear_buffer,
        _read_buffer,
        _read_state,
        _state_file_path,
        _write_state,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_buffer import (  # noqa: F401
        _BUFFER_FILENAME,
        _STATE_FILENAME,
        _append_to_buffer,
        _buffer_file_path,
        _clear_buffer,
        _read_buffer,
        _read_state,
        _state_file_path,
        _write_state,
    )

# wiki 摄取状态 (防重复吃)
try:
    from .catfish_memory_wiki_state import (  # noqa: F401
        _list_pending_queries,
        _list_pending_sources,
        _mark_wiki_queries_ingested,
        _mark_wiki_sources_ingested,
        _read_queries_concat,
        _read_sources_concat,
        _read_wiki_ingested_state,
        _wiki_ingested_state_path,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_wiki_state import (  # noqa: F401
        _list_pending_queries,
        _list_pending_sources,
        _mark_wiki_queries_ingested,
        _mark_wiki_sources_ingested,
        _read_queries_concat,
        _read_sources_concat,
        _read_wiki_ingested_state,
        _wiki_ingested_state_path,
    )

# 蒸馏 24h cooldown
try:
    from .catfish_memory_distill_state import (  # noqa: F401
        _mark_distill_run,
        _should_run_distill,
        _write_distilled,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_distill_state import (  # noqa: F401
        _mark_distill_run,
        _should_run_distill,
        _write_distilled,
    )

# 网关地址 + dev token
try:
    from .catfish_memory_gateway import (  # noqa: F401
        _GATEWAY_URL_DEPRECATION_LOGGED,
        _PLUGIN_CONFIG_FILENAME,
        _TOKEN_MISSING_MSG,
        _gateway_dev_token,
        _gateway_url,
        _load_plugin_config,
        _log_token_missing,
        _plugin_config_path,
        _read_hermes_env_key,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_gateway import (  # noqa: F401
        _GATEWAY_URL_DEPRECATION_LOGGED,
        _PLUGIN_CONFIG_FILENAME,
        _TOKEN_MISSING_MSG,
        _gateway_dev_token,
        _gateway_url,
        _load_plugin_config,
        _log_token_missing,
        _plugin_config_path,
        _read_hermes_env_key,
    )



#: 总结 LLM prompt — 跟老 gateway 风格一致, 让员工 journal 风格连续.


def _wiki_enabled() -> bool:
    """P1.1 wiki two-step 开关 — 优先 yaml, env 兜底, 默认 off (LLM 调用贵).

    P3.5.12 (6/16 鸿波): 加 yaml 守门, 优先级 yaml > env > default False.

    真因: 6/16 早 entities/concepts 真被自动写入 19+34 条, 鸿波反馈"对话自动入
    知识库会很乱". audit 证实: 当时 shell `CATFISH_WIKI_ENABLE=1` 被 hermes
    继承 → 此函数返 True → wiki two-step 跑. 老逻辑只读 env 不可靠 — env
    可能从任何 init script / launchd plist 传, 难根治.

    新逻辑: yaml `wiki.auto_ingest` 优先 (永久声明性配置, 跟 env 解耦).
    yaml 配 false → env 设 =1 也不动. yaml 不配 → fallback env (向后兼容).
    yaml + env 都没配 → default False.

    yaml 配法: ~/.catfish/memory_plugin.yaml 加段:
        wiki:
          auto_ingest: false   # 永远不自动入库, 员工 ChatBubble "💾 存 wiki" 手动入
    """
    # 优先级 1: yaml `wiki.auto_ingest` (单一权威配置)
    try:
        cfg = _load_plugin_config()
        if isinstance(cfg, dict):
            wiki_cfg = cfg.get("wiki", {})
            if isinstance(wiki_cfg, dict):
                ai = wiki_cfg.get("auto_ingest")
                if isinstance(ai, bool):
                    return ai
    except Exception:  # noqa: BLE001 - 配置读失败回退 env, 不挂 plugin
        pass

    # 优先级 2: CATFISH_WIKI_ENABLE env (向后兼容, 历史路径)
    val = os.environ.get("CATFISH_WIKI_ENABLE", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _extract_message_pairs(messages: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """从 hermes message list 抽 (role, content) 对.

    跳过 system / tool / 空 content. 限 _MAX_MESSAGES_PER_SUMMARY 条 (尾部).
    """
    pairs: List[Tuple[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        pairs.append((role, content))
    if len(pairs) > _MAX_MESSAGES_PER_SUMMARY:
        pairs = pairs[-_MAX_MESSAGES_PER_SUMMARY:]
    return pairs


def _format_journal_entry(session_id: str, summary: str) -> str:
    """格式 journal entry. BL-CATFISH-WIKI-MODE P0.3 (6/3): 改用 Karpathy LLM Wiki
    log.md 风格 `## [YYYY-MM-DD HH:MM] kind | title`, 一行可 grep 解析.
    grep '^## \\[' employee_journal.md | tail -5 拉最近 5 条."""
    date_str = time.strftime("%Y-%m-%d %H:%M")
    # 8/4: 写**完整** session_id, 不再截成后 6 位。
    #
    # 老写法 `session_id[-6:]` 把溯源链掐断了: wiki 条目的 sources 只记到日期,
    # journal 只留 6 位后缀 —— 员工看到一条可疑的断言, 回溯不到说这句话的那次
    # 对话。而原始对话其实完整躺在 ~/.hermes/state.db (实测 5329 会话 /
    # 50794 条消息), 信息没丢, 只是指针被截断了。
    #
    # 不可验证 = 不可修正。知识库是四次 LLM 转写的产物 (summarize → analysis →
    # generation → merge), 失真是压缩的物理必然, 消除不了; 但"看到错的能查证"
    # 是可以做到的, 而且只差这一个字段。
    #
    # 存量条目靠后缀 LIKE 匹配仍能溯源 (session id 形如 20260803_195928_f39669,
    # 后 6 位同一天内基本唯一), 见 tool-bridge/wiki_trace.py。新条目从此精确。
    sid = session_id.strip() or "unknown"
    return f"## [{date_str}] session | {sid}\n\n{summary.strip()}\n"


def _append_journal(catfish_home: Path, entry: str) -> None:
    """追加一段 entry 到 catfish_home/employee_journal.md. 自动建父目录."""
    path = catfish_home / "employee_journal.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = entry.strip() + "\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(body)


def _read_picker_state_model(catfish_home: Path) -> str:
    """P3.5.2 (6/16 鸿波): 读 ~/.catfish/picker_state.json 拿 companion chat picker 当前 model.

    ⚠⚠ 【原则, 8/4 鸿波明确拍板】蒸馏必须用**员工自己选的那个模型**, 不许换。

    8/4 讨论知识库准确性时提过一个"优化": 蒸馏一天只跑几次, 换个更强的模型成本
    可忽略, 质量还更好 —— 加个 distill_model 独立配置就行。鸿波否掉了, 明确说
    这是原则问题。

    别再提这个"优化"。员工选了哪个模型, 知识库就该是那个模型的产物 —— 换成别的
    模型偷偷生成, 员工看到的东西就不再是他能预期的东西了。质量差是可以接受的,
    不透明不行。

    (下面的注释是原始设计说明, 保留。)

    companion chat.ts 每次 send 前 fire-and-forget 写这个文件, atomic write.
    plugin sync_turn 触发时读, 让 summary model 自动跟随 picker (而不是 yaml 静态).

    设计 (方案 B, 6/16 鸿波拍): hermes MemoryProvider.sync_turn 签名没 picker 入参,
    plugin 拿不到 picker 状态. 文件中转是绕过 hermes API 限制的最简方案.

    优先级 (caller _get_summarize_model): picker_state.json > yaml > env > 空.

    Args:
        catfish_home: ~/.catfish 目录

    Returns:
        picker model 字符串. 文件不存在 / parse 错 / chat_model 字段缺 → 空字符串.
        Caller 看到空就走 fallback (yaml/env).
    """
    path = catfish_home / "picker_state.json"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            model = data.get("chat_model", "")
            if isinstance(model, str) and model.strip():
                return model.strip()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug(
            "catfish-memory: read picker_state.json 失败 (fallback yaml/env): %s", e,
        )
    return ""


def _read_full_journal(catfish_home: Path) -> str:
    """全文读 employee_journal.md 给蒸馏用 (不走 5KB inject 截断). 没文件 → 空."""
    path = catfish_home / "employee_journal.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ─────────────────────────────────────────────────────────────────────────
# 8/15 拆分: 2522 行 → 796 + 5 个模块。下面按**依赖顺序**把它们的符号
# re-export 回本模块, 因为外部调用方全都认 catfish_memory_helpers 这个名字:
#
#   · catfish_memory.py  `from .catfish_memory_helpers import ...` 50 个符号
#   · wiki_health.py     `from catfish_memory_helpers import ...` 5 个符号
#   · tests/            `from catfish_memory_helpers import ...` 二十来个
#
# 顺序要紧 (prompts → fm → wiki → llm → merge): 后面的模块 import 前面的,
# 而它们又都回指本模块。本模块执行到这里时, 上面那些 main 层符号 (logger /
# _gateway_url / 各种常量) 已经定义好了, 所以子模块的回指拿得到 ——
# 这也是这个 re-export 块**必须放在文件末尾**的原因。
#
# import 形式: 相对优先、绝对兜底。理由见任一子模块的文件头注释,
# 以及 tests/test_loader_fidelity.py。
# ─────────────────────────────────────────────────────────────────────────

try:
    from .catfish_memory_prompts import (  # noqa: F401
        _SUMMARIZE_PROMPT,
        _DISTILL_PROMPT,
        _ANALYSIS_PROMPT,
        _GENERATION_PROMPT_TEMPLATE,
        _build_generation_prompt,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_prompts import (  # noqa: F401
        _SUMMARIZE_PROMPT,
        _DISTILL_PROMPT,
        _ANALYSIS_PROMPT,
        _GENERATION_PROMPT_TEMPLATE,
        _build_generation_prompt,
    )

try:
    from .catfish_memory_fm import (  # noqa: F401
        _WIKI_PATH_PATTERN,
        _FILE_SENTINEL,
        _parse_generation_output,
        _FM_LIST_FIELDS_UNION,
        _FM_LIST_RE,
        _FM_SCALAR_RE,
        _split_frontmatter_body,
        _split_top_level,
        _REL_NAME_RE,
        _rel_item_name,
        _parse_frontmatter_lists,
        _parse_frontmatter_scalar,
        _merge_wiki_file,
        _FM_KEY_LINE,
        _FM_FENCE_LINE,
        _ensure_frontmatter_fence,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_fm import (  # noqa: F401
        _WIKI_PATH_PATTERN,
        _FILE_SENTINEL,
        _parse_generation_output,
        _FM_LIST_FIELDS_UNION,
        _FM_LIST_RE,
        _FM_SCALAR_RE,
        _split_frontmatter_body,
        _split_top_level,
        _REL_NAME_RE,
        _rel_item_name,
        _parse_frontmatter_lists,
        _parse_frontmatter_scalar,
        _merge_wiki_file,
        _FM_KEY_LINE,
        _FM_FENCE_LINE,
        _ensure_frontmatter_fence,
    )

try:
    from .catfish_memory_wiki import (  # noqa: F401
        # ⚠ 这三个是 `_TYPE_ALIASES, _ENTITY_TYPES, _CONCEPT_TYPES = _load_type_vocab()`
        # 元组解包出来的。第一版生成 re-export 时漏了 —— 计算器只认 ast.Assign
        # 里 targets 是 ast.Name 的情况, Tuple 目标整个跳过。判据比真事窄,
        # 靠 test_ontology.py::test_vocab_comes_from_shared_contract 抓出来的。
        _CONCEPT_TYPES,
        _ENTITY_TYPES,
        _TYPE_ALIASES,
        _CONCLUSION_WORDS,
        _scan_conclusion_words,
        _normalize_slug_for_dedup,
        _read_title_of,
        _title_of_content,
        _redirect_to_existing_equivalent,
        _load_type_vocab,
        _canon_subtype,
        _SUBTYPE_LINE,
        _normalize_types,
        _check_dangling_related,
        _classify_ontology_status,
        _upsert_ontology_status,
        _AUTHORED_BY_EMPLOYEE,
        _LLM_APPENDIX_HEAD,
        _is_employee_authored,
        _append_as_appendix,
        _write_wiki_files,
        report_ontology_gaps,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_wiki import (  # noqa: F401
        _CONCEPT_TYPES,
        _ENTITY_TYPES,
        _TYPE_ALIASES,
        _CONCLUSION_WORDS,
        _scan_conclusion_words,
        _normalize_slug_for_dedup,
        _read_title_of,
        _title_of_content,
        _redirect_to_existing_equivalent,
        _load_type_vocab,
        _canon_subtype,
        _SUBTYPE_LINE,
        _normalize_types,
        _check_dangling_related,
        _classify_ontology_status,
        _upsert_ontology_status,
        _AUTHORED_BY_EMPLOYEE,
        _LLM_APPENDIX_HEAD,
        _is_employee_authored,
        _append_as_appendix,
        _write_wiki_files,
        report_ontology_gaps,
    )

try:
    from .catfish_memory_llm import (  # noqa: F401
        _call_summarize_llm,
        _call_distill_llm,
        _call_analysis_llm,
        _existing_wiki_index,
        _call_generation_llm,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_llm import (  # noqa: F401
        _call_summarize_llm,
        _call_distill_llm,
        _call_analysis_llm,
        _existing_wiki_index,
        _call_generation_llm,
    )

try:
    from .catfish_memory_merge import (  # noqa: F401
        _MERGE_PROMPT_TEMPLATE,
        _build_merge_prompt,
        _call_merge_llm,
        _MERGE_MIN_BODY_RATIO,
        _accept_llm_merge,
        merge_files_with_llm,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_merge import (  # noqa: F401
        _MERGE_PROMPT_TEMPLATE,
        _build_merge_prompt,
        _call_merge_llm,
        _MERGE_MIN_BODY_RATIO,
        _accept_llm_merge,
        merge_files_with_llm,
    )
