"""LLM 合并 (P18) —— 从 catfish_memory_helpers.py 拆出 (8/15)。

依赖 fm (frontmatter) + wiki (_is_employee_authored) + helpers (gateway/logger)。

⚠ `_call_merge_llm` 和 `merge_files_with_llm` **必须留在同一个模块**。
tests/test_employee_authored.py 用 `monkeypatch.setattr(H, "_call_merge_llm", ...)`
然后调 `H.merge_files_with_llm` —— 两者分开的话, merge_files_with_llm 从自己的
globals 取 _call_merge_llm, 打在 helpers 上的 patch 就落空了
(`from X import name` 建的是新绑定, 不是别名)。
8/15 给那条测试补的阳性对照 test_p19_merges_non_employee_files 就是防这个。
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Dict, Optional, Tuple

# 本模块有两种加载方式, import 形式必须两种都活:
#   · hermes 进程内 —— plugins/memory/__init__.py 用 spec_from_file_location
#     + submodule_search_locations 加载, 是真包, **相对 import 才 work**
#     (它全程不碰 sys.path, 裸绝对 import 找不到兄弟模块)
#   · wiki_health.py 独立脚本 —— 自己 sys.path.insert, 此时没有父包,
#     相对 import 反过来会炸
# 见 tests/test_loader_fidelity.py, 那里每种方式各起一个干净子进程验。
try:
    from .catfish_memory_helpers import _LLM_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, _log_token_missing, logger  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import _LLM_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, _log_token_missing, logger  # noqa: F401

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
    from .catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body  # noqa: F401
try:
    from .catfish_memory_wiki import _is_employee_authored  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_wiki import _is_employee_authored  # noqa: F401


# ============================================================
# P19 (6/5 鸿波) — LLM merge mode: 同名 entity/concept 让 LLM 真合并叙述,
# 不是 P18 `body 替换 + 旧 body 注释留底` `(留底法)`.
#
# 触发时机: _write_wiki_files 检测重名 → 上层 sync_turn 3b 在 await
# _call_generation_llm 后, 调 _call_merge_llm 替换 file dict `重名 entry`.
# LLM 失败 → fallback 走 _merge_wiki_file (P18 regex merge 当安全网).
#
# Prompt 设计: 给 LLM 两版 (OLD + NEW) `整 markdown` 真, 让它生 merged
# 完整 markdown (frontmatter + body). frontmatter 规则 LLM 自己读 prompt,
# body `不直接拼接, 而是合一个连贯叙述, 矛盾的标 OLD/NEW 两段.
# ============================================================

_MERGE_PROMPT_TEMPLATE = (
    "你是企业知识体系维护员. 下面是同一个 wiki 页 (entity 或 concept) 真两个版本:\n"
    "OLD (现有 wiki, 已存) 和 NEW (基于新 source 生成).\n"
    "你的任务: 合并真一个最终版.\n\n"
    "**合并规则**:\n\n"
    "frontmatter:\n"
    "- title: 用 NEW (允许改名)\n"
    "- created: 用 OLD (保留身份历史, 不丢)\n"
    "- updated: {today}\n"
    "- entity_type / concept_type: 用 NEW (允许 reclassify)\n"
    "- tags / related / sources / aliases: 并集去重 (旧 ∪ 新)\n\n"
    "body:\n"
    "- **不直接拼接** 两版段落; 合一个连贯叙述\n"
    "- 重复信息只说一次\n"
    # P3.5.205 (7/9 鸿波 catch KB 中电福富 时间线误判): 老规则只有 OLD/NEW 二态,
    # LLM 把'并行事件'(07-08 高企申报进入盖章 + 07-09 同时启动 ITSS)误判成
    # '替换关系'. 改三态:
    #   1) 明确替换 → OLD/NEW (岗位变更, 事实纠错, 战略切换)
    #   2) 并行 (两版都对, 各说一件) → "另外 / 同时" 平铺
    #   3) 澄清 (新版更细化) → 直接换掉旧模糊表述, 不留 OLD 标注
    # 默认走并行/澄清 (不写 OLD/NEW), 只有肯定是替换关系才写 OLD/NEW.
    "- **三态判断 (P3.5.205)**:\n"
    "  1. 明确替换 (岗位变更, 事实纠错, 战略切换): 标 'OLD: 之前 X' / 'NEW: 现在 Y', 注日期\n"
    "  2. 并行 (两版都对, 各说一件): '另外' / '同时' 平铺, 不写 OLD/NEW\n"
    "  3. 澄清 (新版更细化): 直接换掉旧模糊表述, 不留 OLD 标注\n"
    "  - **默认走 2 或 3**, 只有肯定是替换关系才用 1\n"
    # P3.5.205: body 收紧同 generation prompt
    "- **不做因果推断 / 价值判断 / 生动化修饰**: 只用日志字面事实 rephrase\n"
    "- 允许衔接词 (同时/然后/目前/另外), 禁结论词 (决定/因此/意味着/影响/视为红线/直接影响)\n"
    "- 保留 NEW 所有新事实, OLD 只丢与 NEW 矛盾或过期部分\n"
    # P3.5.205: sources 升级 — 若 OLD 是常量 [employee_journal], 用 NEW 的日期列表升级
    "- **sources 升级**: 若 OLD `sources: [employee_journal]` (老格式), 用 NEW `[journal:YYYY-MM-DD, ...]` 替换. 若两版都新格式 → 并集去重升序.\n"
    "- 总字数: entity ≤500 / concept ≤700\n"
    "- 文末加 `## 变更历史` section, 1 行 bullet:\n"
    "  `- {today}: 基于 <source> 更新, 主要变化: <一句话>`\n\n"
    "**输出**: ONLY 最终 markdown (含 frontmatter + body), 无其他说明 / 解释 / 引号.\n"
    "frontmatter `related:` 字段必须 `[\"[[name]]\", ...]` 双引号 string list.\n\n"
    "=== OLD (已存 wiki) ===\n"
    "{old_text}\n"
    "=== END OLD ===\n\n"
    "=== NEW (基于新 source 生) ===\n"
    "{new_text}\n"
    "=== END NEW ==="
)


def _build_merge_prompt(old_text: str, new_text: str) -> str:
    return _MERGE_PROMPT_TEMPLATE.format(
        today=time.strftime("%Y-%m-%d"),
        old_text=old_text.strip(),
        new_text=new_text.strip(),
    )


async def _call_merge_llm(
    old_text: str, new_text: str, model: str,
) -> Optional[str]:
    """Step 3 Merge (P19, 6/5): 同名 wiki 真两版让 LLM 合并真一版.

    输入: 旧 markdown (含 frontmatter) + 新 markdown (LLM 真生成的 NEW).
    返: merged markdown 或 None (LLM 失败 → caller fallback regex merge).

    timeout 60s — 单 entity merge prompt 短, 比 generation 快.
    """
    if not old_text.strip() or not new_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("wiki merge (条目合并)")
        return None
    try:
        import httpx
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                with_source(_gateway_url(), "plugin:memory-wiki-merge"),
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
                         "content": _build_merge_prompt(old_text, new_text)},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2048,  # single entity merge, 4096 没必要
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory merge: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            text = text.strip()
            # 校验: 必须 `---\n` 开头 (frontmatter 存在), 否则 LLM 没遵守输出格式
            if not text.startswith("---\n"):
                logger.warning(
                    "catfish-memory merge: LLM output 不以 frontmatter 开头, skip"
                )
                return None
            return text
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "catfish-memory merge 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None


#: P19 LLM merge 的验收阈值 —— 只拦明显事故, 不做美学判断。
_MERGE_MIN_BODY_RATIO = 0.4     # 正文缩到旧+新较长者的 40% 以下 = LLM 偷懒


def _accept_llm_merge(
    rel_path: str,
    old_text: str,
    new_content: str,
    merged: str,
) -> Optional[str]:
    r"""验收 P19 LLM merge 的输出。不合格返 None → 自动落回 P18 regex 安全网。

    # 为什么要验收 (8/4)

    P19 把整个文件交给 LLM 重写, 拿到就写盘, **一行校验都没有**。而它只在
    **更新已有条目**时触发 —— 一个条目被更新 N 次就被 LLM 重写 N 次, 误差累积。

    8/4 在鸿波机器上实测到的后果: 255 条里 19 条 frontmatter 丢了开头的 `---`,
    读侧一个字段都读不到, title 退化成文件名 (UI 上显示成拼音), tags/related
    全部失效 —— 那些条目在知识图谱里是没有任何连线的孤岛。

    P18 反而更安全: frontmatter list 取并集、created 保留旧、旧 body 转
    `<!-- legacy body -->` 注释留在文末。**确定性, 且信息不丢。** 它唯一的
    缺点是叙述不如 LLM 融合的顺 —— 那是可以接受的代价。

    所以不砍 P19, 给它加验收: 过了就用 (读起来更好), 没过就退回 P18 (更保守)。
    回退机制本来就有 (merged 为假 → n_failed → 不进 ok_paths → 写盘走 P18),
    这里只是把"LLM 调用失败"扩展成"LLM 输出不合格"。

    # 三条检查, 都来自实际事故形态, 不是想象

    1. frontmatter 必须完整 (`---` 开头 + 有收尾)
       → 8/4 那 19 个坏文件就是这个形态

    2. 旧 frontmatter 的 list 字段不能丢
       related / tags / sources 在 P18 里是**并集**语义。LLM merge 丢掉 related
       → 图里连线消失、实体掉进"未分类"。丢了就是倒退, 不接受。

    3. 正文不能暴缩 (< 旧/新 较长者的 40%)
       LLM 偷懒把长文压成一句话。P18 至少有 legacy 留底, P19 是直接覆盖。

    阈值保守是故意的: 宁可放过一些不完美的合并, 也不要频繁退回 P18 让叙述碎掉。
    每次拒绝都打 WARNING —— 静默退回等于把 LLM 跑偏这件事藏起来。
    """
    if not merged or not merged.strip():
        return None

    # ① frontmatter 完整性
    fm_new, body_new = _split_frontmatter_body(merged)
    if not fm_new:
        logger.warning(
            "catfish-memory P19 拒收 %s: 输出没有完整 frontmatter "
            "(不以 --- 开头, 或缺收尾) —— 落回 P18 regex merge",
            rel_path,
        )
        return None

    # ② 旧 list 字段不能丢 (并集语义, 丢了就是倒退)
    fm_old, body_old = _split_frontmatter_body(old_text)
    if fm_old:
        old_lists = _parse_frontmatter_lists(fm_old)
        new_lists = _parse_frontmatter_lists(fm_new)
        for key, old_vals in old_lists.items():
            if not old_vals:
                continue
            lost = [v for v in old_vals if v not in new_lists.get(key, [])]
            if lost:
                logger.warning(
                    "catfish-memory P19 拒收 %s: 合并后 %s 丢了 %d 项 (%s) —— "
                    "这些字段是并集语义, 丢了会让条目从图谱/筛选里消失. 落回 P18",
                    rel_path, key, len(lost), ", ".join(lost[:3]),
                )
                return None

    # ③ 正文不能暴缩
    _, body_incoming = _split_frontmatter_body(new_content)
    baseline = max(len(body_old.strip()), len(body_incoming.strip()))
    if baseline > 0 and len(body_new.strip()) < baseline * _MERGE_MIN_BODY_RATIO:
        logger.warning(
            "catfish-memory P19 拒收 %s: 正文从 %d 缩到 %d 字符 "
            "(不足 %.0f%%) —— 疑似 LLM 偷懒压缩. 落回 P18 (有 legacy 留底)",
            rel_path, baseline, len(body_new.strip()), _MERGE_MIN_BODY_RATIO * 100,
        )
        return None

    return merged


async def merge_files_with_llm(
    catfish_home: Path,
    files: Dict[str, str],
    model: str,
) -> Tuple[Dict[str, str], set, int]:
    """P19: 遍历 LLM 生的 file dict, 重名走 LLM merge.

    返 (final_files, ok_paths_set, n_failed).
    - ok_paths_set: 真 LLM merge 成功的 rel_path 集合 — 传给 _write_wiki_files
      真 skip_merge_paths, 跳过 P18 regex merge (因 LLM 已合).
    - 失败的 entry `保持原 LLM 生成 content (新版)`, 落到 P18 regex 安全网.
    """
    out = dict(files)
    ok_paths: set = set()
    n_failed = 0
    for rel_path, new_content in files.items():
        target = catfish_home / rel_path
        if not target.exists():
            # 新建, 无需 merge
            continue
        try:
            old_text = target.read_text(encoding="utf-8")
        except OSError:
            continue
        # 8/4: 员工改过的条目连送都不送给 LLM。
        # 送了再拒是浪费 token, 而且只要送出去就有被改写的可能 —— 边界该划在这里,
        # 不是划在验收上。写盘那边会走 _append_as_appendix 把新内容放进附录。
        if _is_employee_authored(old_text):
            logger.info(
                "catfish-memory P19 跳过 %s: 员工改过的条目, 不交给 LLM 重写", rel_path
            )
            continue
        try:
            merged = await _call_merge_llm(old_text, new_content, model)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "catfish-memory merge_llm %s 异常 [%s]: %r",
                rel_path, type(e).__name__, e,
            )
            merged = None
        # 8/4: LLM 的输出要过验收才采用, 不合格当失败处理 → 自动落回 P18。
        merged = _accept_llm_merge(rel_path, old_text, new_content, merged)
        if merged:
            out[rel_path] = merged
            ok_paths.add(rel_path)
            logger.info(
                "catfish-memory wiki LLM merge ✓ %s (%d → %d chars)",
                rel_path, len(new_content), len(merged),
            )
        else:
            n_failed += 1
    return out, ok_paths, n_failed
