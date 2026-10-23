"""员工入库的资料 (wiki/raw/sources/) 一份一份、一段一段地读进知识库 (10/23)。

# 病

原来每轮把所有待处理资料拼成一段 (上限 24,000 字), 分析那一步又只取最后 5,000 字
(_DISTILL_CHUNK_CHARS), 然后把**所有**待处理文件都标成「已摄取」。鸿波 10/23 在 Windows 上
一次积压 49 份 (6.9MB): 模型实际看到的不到千分之一, 49 份全被标成读过, 以后再也不碰,
只出了 10 张薄卡; 卡上的来源也挂成本轮全部 49 份 / 聊天日期, 点不回原文件。
Mac 上一份一份入库, 每次待处理的少, 才一直没暴露。

# 做法

  · 一次只处理一份; 大文件切成约 SOURCE_CHUNK_CHARS 字的段, 按顺序每段走一遍
    分析 → 生成 → 合并 (同名条目由 P19 合并补厚, 不会重复)。
  · 读到哪记在 wiki_source_progress.json ({文件名: 已读到的字符位置}), 中途失败 / 超时
    下次从断点接着读; **整份读完才**标进 wiki_ingested_state.json。
  · 来源只记这一份文件 (Provenance 只给它)。
  · 有积压时同一次后台任务里连着往下读, 最多 RUN_TIME_BUDGET 秒; 下次后台任务接着来。
  · 同一时间只有一个后台任务在读 (_busy), 两轮对话挨得近也不会把同一段读两遍。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("catfish.memory.plugin")

#: 每段字数。分析模型一次读得下 (约 7K token), 输出 (2500 token 的分析) 也够写细。
SOURCE_CHUNK_CHARS = 10000
#: 一次后台任务最多连读多久 (秒)
RUN_TIME_BUDGET = 1800
#: 同一段连续这么多次没读成就跳过 (别让一段怪内容把整份卡死)
MAX_CHUNK_FAILS = 3
#: 一份最多读这么多段 (30 万字)。员工本机真文本最大的 2.8 万字; 更大的基本是表格导出,
#: 全读完要上千次模型调用, 前 30 段够提出条目了
MAX_CHUNKS_PER_FILE = 30


def looks_binary(text: str) -> bool:
    """二进制当文字读出来的 (扫描版 pdf 的 JPEG 字节等): 乱码 / 控制字符超过 2%。

    10/23 前 catfish_wiki_ingest 会把读不出文字的 pdf / pptx 原样写进 raw/sources,
    鸿波本机 13 份、Windows 那批大多是这种, 喂给模型只是白烧调用。正常中文文档是 0%。
    跟 tool-bridge doc_text.looks_binary 同一个判据。
    """
    sample = text[:200000]
    if not sample:
        return False
    junk = sum(1 for c in sample if c == "\ufffd" or (ord(c) < 32 and c not in "\n\r\t\f"))
    return junk / len(sample) > 0.02
_PROGRESS_FILE = "wiki_source_progress.json"

_busy = threading.Lock()

#: (分析输入, 本段所属文件) → (新增实体数, 新增概念数); 返回 None = 模型没给出结果, 这段下次重读
PassFn = Callable[[str, Path], Awaitable[Optional[Tuple[int, int]]]]


def _progress_path(home: Path) -> Path:
    return home / _PROGRESS_FILE


def read_progress(home: Path) -> Dict[str, int]:
    try:
        data = json.loads(_progress_path(home).read_text(encoding="utf-8"))
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_progress(home: Path, progress: Dict[str, int]) -> None:
    p = _progress_path(home)
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        logger.warning("写 %s 失败: %s", p.name, e)


def next_chunk(text: str, start: int, size: int = SOURCE_CHUNK_CHARS) -> int:
    """从 start 起切一段, 返回段尾位置。尽量切在换行处 (段尾 20% 范围内找)。"""
    end = min(len(text), start + size)
    if end >= len(text):
        return len(text)
    cut = text.rfind("\n", start + int(size * 0.8), end)
    return cut + 1 if cut > start else end


def _analysis_input(name: str, chunk: str, part: int, parts: int) -> str:
    where = f" (第 {part}/{parts} 段)" if parts > 1 else ""
    return f"## Uploaded raw sources (P16)\n\n### source: {name}{where}\n\n{chunk.strip()}\n"


async def drain_sources(
    home: Path,
    pending: List[Path],
    run_pass: PassFn,
    mark_done: Callable[[Path, List[str]], None],
    time_budget: float = RUN_TIME_BUDGET,
    session_id: str = "",
) -> Dict[str, int]:
    """按顺序一份一份读 pending (最旧先); 返回 {chunks, files_done, entities, concepts}。"""
    stats = {"chunks": 0, "files_done": 0, "entities": 0, "concepts": 0}
    if not pending or not _busy.acquire(blocking=False):
        return stats
    try:
        deadline = time.monotonic() + time_budget
        progress = read_progress(home)
        for path in pending:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                logger.warning("catfish-memory 资料 %s 读不了, 跳过: %s", path.name, e)
                continue
            if looks_binary(text):
                logger.warning(
                    "catfish-memory 资料 %s 不是文字 (多半是扫描件 / 图片被当文字存了), 不读; "
                    "要入库请重新入库 (工具会抽文字或要求先识别)", path.name,
                )
                mark_done(home, [path.name])
                continue
            limit = min(len(text), MAX_CHUNKS_PER_FILE * SOURCE_CHUNK_CHARS)
            parts = max(1, -(-limit // SOURCE_CHUNK_CHARS))
            start = min(progress.get(path.name, 0), limit)
            while start < limit:
                if time.monotonic() > deadline:
                    logger.info("catfish-memory bg session=%s: 资料读了 %d 段, 到时间了, 下次接着读", session_id, stats["chunks"])
                    return stats
                end = min(next_chunk(text, start), limit)
                part = start // SOURCE_CHUNK_CHARS + 1
                result = await run_pass(_analysis_input(path.name, text[start:end], part, parts), path)
                fail_key = f"{path.name}#fails"
                if result is None:
                    fails = progress.get(fail_key, 0) + 1
                    if fails < MAX_CHUNK_FAILS:
                        # 模型这次没给出结果 (超时 / 网关不通): 不前进, 下次从这段重读
                        progress[fail_key] = fails
                        _write_progress(home, progress)
                        logger.info("catfish-memory bg session=%s: 资料 %s 第 %d 段没读成 (%d 次), 下次重读", session_id, path.name, part, fails)
                        return stats
                    logger.warning("catfish-memory bg session=%s: 资料 %s 第 %d 段连续 %d 次没读成, 跳过这段", session_id, path.name, part, fails)
                    result = (0, 0)
                progress.pop(fail_key, None)
                stats["chunks"] += 1
                stats["entities"] += result[0]
                stats["concepts"] += result[1]
                start = end
                progress[path.name] = start
                _write_progress(home, progress)
            # 整份读完才算摄取
            mark_done(home, [path.name])
            progress.pop(path.name, None)
            progress.pop(f"{path.name}#fails", None)
            _write_progress(home, progress)
            stats["files_done"] += 1
            logger.info(
                "catfish-memory bg session=%s: ✓ 资料 %s 读完 (%d 段%s)", session_id, path.name, parts,
                f", 只读了前 {limit} 字 / 共 {len(text)} 字" if limit < len(text) else "",
            )
        return stats
    finally:
        _busy.release()


async def wiki_two_step(
    session_id: str,
    catfish_home: Path,
    analysis_input: str,
    provenance,
    resolve_model: Callable[[str], str],
    max_chars: int = 0,
    deps=None,
    source_title: str = "",
    source_ref: str = "",
) -> Optional[Tuple[int, int]]:
    """分析 → 生成 → 同名合并 → 写盘, 一遍。返回 (新增实体, 新增概念); 模型没给出结果返 None。

    从 _DistillMixin 里抽出来 (10/23): 聊天派生那一批和资料的每一段都走这一遍。
    resolve_model(stage) 每个阶段现取 picker 当前模型 (见 _resolve_background_model)。
    deps: 从哪个模块取 LLM / 写盘函数 —— 调用方传自己的模块, 测试打在那里的桩才生效。
    """
    if deps is None:
        try:
            from . import catfish_memory_helpers as deps  # noqa: PLC0415
        except ImportError:  # 独立脚本模式 (无父包)
            import catfish_memory_helpers as deps  # noqa: PLC0415
    _append_journal = deps._append_journal
    _call_analysis_llm = deps._call_analysis_llm
    _call_generation_llm = deps._call_generation_llm
    _parse_generation_output = deps._parse_generation_output
    _write_wiki_files = deps._write_wiki_files
    merge_files_with_llm = deps.merge_files_with_llm
    from datetime import datetime, timezone  # noqa: PLC0415

    model = resolve_model("wiki-analysis")
    if not model:
        return None
    analysis = await _call_analysis_llm(analysis_input, model, max_chars=max_chars)
    if not analysis:
        logger.info("catfish-memory bg session=%s: wiki Step 1 analysis 返空 (skip)", session_id)
        return None
    model = resolve_model("wiki-generation")
    if not model:
        return None
    generation = await _call_generation_llm(analysis, model, catfish_home)
    if not generation:
        logger.info("catfish-memory bg session=%s: wiki Step 2 generation 返空 (skip)", session_id)
        return None
    files = _parse_generation_output(generation)
    if not files:
        logger.info("catfish-memory bg session=%s: wiki parse 0 file (LLM 输出不符 sentinel)", session_id)
        return (0, 0)
    # 10/23: 每条事实对一遍原文 (分析那步实际看到的那段), 对不上的拿出正文进 unverified
    try:
        from . import catfish_memory_wiki_verify as verify  # noqa: PLC0415
    except ImportError:  # 独立脚本模式 (无父包)
        import catfish_memory_wiki_verify as verify  # noqa: PLC0415
    source = analysis_input[-max_chars:] if max_chars else analysis_input
    files, vstat = verify.verify_files(files, source)
    if vstat["dropped"]:
        logger.info(
            "catfish-memory bg session=%s: 核对原文: %d 条对上, %d 条对不上 (进 unverified 待人看)",
            session_id, vstat["kept"], vstat["dropped"],
        )
    # 10/23 评测: 分析抽出来的事实, 写卡那步常常没挂到任何条目上 (数字、指标最容易丢, 召回 4 成)。
    # 资料这一路额外按 Facts 段原样生成一张「资料要点」卡, 一条不丢, 同样逐条对原文。
    if source_title:
        digest = digest_card(analysis, source_title, source_ref)
        if digest:
            digest_path, digest_content = digest
            checked, dstat = verify.verify_files({digest_path: digest_content}, source)
            files.update(checked)
    pre_merge = dict(files)
    # P19 (6/5): 同名 entity/concept 让 LLM 合并叙述; 失败的落 P18 regex 安全网, 不丢数据
    llm_merged_paths: set = set()
    model = resolve_model("wiki-merge")
    if not model:
        return None
    try:
        files, llm_merged_paths, n_llm_fail = await merge_files_with_llm(catfish_home, files, model)
        if llm_merged_paths or n_llm_fail:
            logger.info(
                "catfish-memory bg session=%s: P19 LLM merge %d ok / %d fail (fail → P18 regex 安全网)",
                session_id, len(llm_merged_paths), n_llm_fail,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("catfish-memory P19 merge_files_with_llm 异常 (fallback P18): %s", e)
    # 10/23: 合并只许重新组织, 不许多出数字; evidence / unverified 也别在合并里丢了
    for path in llm_merged_paths:
        if path not in files:
            continue
        before = [pre_merge.get(path, "")]
        try:
            before.append((catfish_home / path).read_text(encoding="utf-8"))
        except OSError:
            pass
        files[path] = verify.carry_lists(verify.check_merged_numbers(files[path], before, source), before)
    n_e, n_c = _write_wiki_files(catfish_home, files, skip_merge_paths=llm_merged_paths, provenance=provenance)
    if n_e + n_c > 0:
        # journal 加 distill entry — Karpathy log.md 风格
        ts_short = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        _append_journal(
            catfish_home,
            f"\n## [{ts_short}] distill | {n_e} entities, {n_c} concepts\n\n"
            f"wiki/entities/ + wiki/concepts/ 已更新 ({len(files)} pages, ~{len(generation)//1024}KB).\n",
        )
        logger.info("catfish-memory bg session=%s: ✓ wiki ship %d entities + %d concepts", session_id, n_e, n_c)
    return (n_e, n_c)


def source_title_of(path: Path) -> str:
    """raw/sources 文件名 → 资料名: 去掉入库时加的时间戳前缀 (1792700000-福建十五五规划.md → 福建十五五规划)。"""
    import re  # noqa: PLC0415

    return re.sub(r"^\d{6,}-", "", path.stem) or path.stem


def digest_card(analysis: str, source_title: str, source_ref: str) -> Optional[Tuple[str, str]]:
    """从分析结果的 Facts 段生成「<资料名>（资料要点）」概念卡: 每条事实一行, 带原文摘录。

    不走模型 —— 分析那步已经把事实和摘录抽好了, 原样落下来, 写卡那步漏掉的也在这里。
    返回 (路径, 内容); Facts 段为空返回 None。
    """
    import re  # noqa: PLC0415
    from datetime import date  # noqa: PLC0415

    m = re.search(r"^##\s*Facts\s*$(.*?)(?=^##\s|\Z)", analysis, re.MULTILINE | re.DOTALL)
    if not m:
        return None
    lines = []
    for raw in m.group(1).splitlines():
        raw = raw.strip().lstrip("-*• ").strip()
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            continue
        fact, quote = parts[1], "|".join(parts[2:]).strip()
        quote = quote.strip("「」“”\"' ")
        if fact and quote:
            lines.append(f"- {fact} 〔原文:「{quote}」〕")
    if not lines:
        return None
    title = f"{source_title}（资料要点）"
    today = date.today().isoformat()
    content = (
        "---\n"
        "type: concept\n"
        f"title: {title}\n"
        "concept_type: 资料要点\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"sources: [\"{source_ref}\"]\n"
        "---\n\n"
        f"# {title}\n\n" + "\n".join(lines) + "\n"
    )
    safe = re.sub(r"[\\/:*?\"<>|]", "-", title)
    return f"wiki/concepts/{safe}.md", content
