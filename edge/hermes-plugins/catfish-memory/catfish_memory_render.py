"""CatfishMemoryProvider 的**渲染层** —— 从 catfish_memory.py 拆出 (8/15)。

prefetch() 往 system prompt 里注入的那十几段, 每段一个 `_render_*`。

# 为什么是 mixin 而不是模块级函数

这 14 个方法虽然**一个都不碰 self 状态、也不回调留守方法** (拆之前专门扫过,
"回调留守方法 / 用 self 状态的: 无"), 但两种调用形式都得保住:

  · 类内 14 处 `self._render_xxx()` —— 都在 prefetch 里
  · 测试直接调 `provider._render_schema()` (tests/test_expense.py:271)

改成模块级函数就得动这两处; 做成 mixin 则一行调用点都不用改, 也不影响
`monkeypatch.setattr(CatfishMemoryProvider, "_render_x", ...)` 这种写法
(设在子类上, 按 MRO 照样盖住 mixin)。

# 边界怎么定的

按"纯净度"扫出来的: 不读 self 状态 + 不调兄弟方法 = 可以整组搬走。
`_render_employee_journal` 调 `self._tail_journal`, 所以两个一起搬 —— 组内闭合。

`_query_token_set` / `_jaccard_similarity` 也搬过来了: 全文件只有
`_render_skills_catalog` 和 `_render_strategic_docs` 用它们 (做 top-K 相似度),
留在原处会让本模块反向依赖 catfish_memory.py, 形成循环。

# 加载方式

跟同目录其它模块一样: 相对优先、绝对兜底, 见 tests/test_loader_fidelity.py。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

try:
    from .catfish_memory_helpers import (  # noqa: F401
        _BUDGETS,
        _read_jsonl_tail,
        _read_text_safe,
        logger,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import (  # noqa: F401
        _BUDGETS,
        _read_jsonl_tail,
        _read_text_safe,
        logger,
    )

# 8/17 拆分: 相关性打分搬到 catfish_memory_relevance.py。
#
# 这里 re-export 是有意的 —— 老 caller 和测试都 `from catfish_memory_render
# import _query_token_set`, 而且 _RenderMixin 自己也要用。不 re-export 就得
# 同时改一堆调用点, 收益为零。
#
# ⚠ 上次 (8/15) 从 catfish_memory.py 拆这个文件时踩过: 依赖计算只跑了那 14 个
#   render 方法, 后来临时决定把 _query_token_set 也搬过来却没重跑, 于是它引用的
#   _STOPCHARS 留在了原处 —— 3 处 NameError, check_undefined_names.sh 抓到的。
#   这次搬之前做了跨度自检 (116 + 675 = 791, 0 重叠 0 遗漏) 并确认搬走那段
#   引用的外部名字为空。
try:
    from .catfish_memory_relevance import (  # noqa: F401
        _RELEVANCE_MIN_HITS,
        _RELEVANCE_MIN_OVERLAP,
        _SKILLS_FLOOR_RATIO,
        _STRATEGIC_FLOOR_RATIO,
        _best_relevance,
        _jaccard_similarity,
        _overlap_coefficient,
        _query_token_set,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_relevance import (  # noqa: F401
        _RELEVANCE_MIN_HITS,
        _RELEVANCE_MIN_OVERLAP,
        _SKILLS_FLOOR_RATIO,
        _STRATEGIC_FLOOR_RATIO,
        _best_relevance,
        _jaccard_similarity,
        _overlap_coefficient,
        _query_token_set,
    )


#: wiki 清单每类最多列几个。
#:
#: query 空时沿用老值 50 (不给 advisor / 定时任务这类无 query 调用方引回归)。
#: 有 query 时 20 就够 —— 按相关性排的 20 个比按字母序排的 50 个有用得多,
#: 而且省 ~1,300 token。剩下的 LLM 用 catfish_wiki_search 自己查。
_WIKI_CAP_PLAIN = 50
_WIKI_CAP_QUERY = 20


class _RenderMixin:
    """prefetch 的各段渲染。不持有任何状态 —— 见模块 docstring。"""


    def _render_skills_catalog(
        self, catfish_home: Path, query: str = "",
    ) -> str:
        """BL-MEMORY-P2-4 (2026-06-03): dual-path fallback.
        BL-MEMORY-P2-2 (2026-06-03): query 相关性 top-K 真筛.

        真问题: 老版只扫 ~/.catfish/skills/, 真生产员工 mac 真无目录, 真返空.
        6/3 真生产 dump 真验 5 数据源里 skills_catalog 真缺位 (鸿波 BL-TOKEN-AUDIT
        '5 数据源痕迹没看见' 真因之一).

        真新逻辑 (按优先级扫, 真合并 budget):
        1. ~/.catfish/skills/ (员工录的 RecMode + propose_skill 真生成的, 真用户首选)
        2. ~/.hermes/skills/ (hermes 自带 29 skill + catfish 真 symlink 装的)

        真 dedupe by skill 真名 (优先 catfish 自家版本, 真覆盖 hermes 默认).

        真 query 筛 (P2-2):
        - query 真空 (initial / no message) → 全注入按字母序 (旧行为)
        - query 真有 → 算每 skill name+desc head 的 jieba/字符级 Jaccard, top-K 留
          (按 budget 截断, 真不漏高分 skill)
        """
        candidates: List[Path] = []
        catfish_skills = catfish_home / "skills"
        if catfish_skills.is_dir():
            candidates.append(catfish_skills)
        # 真 fallback: hermes skills (真常用真路径)
        hermes_skills = Path.home() / ".hermes" / "skills"
        if hermes_skills.is_dir():
            candidates.append(hermes_skills)

        if not candidates:
            return ""

        # 真先收集 (skill_name, skill_head) 真候选 list, 真不截 budget
        candidates_list: List[tuple] = []
        seen_names: set = set()
        try:
            for skills_root in candidates:
                # 真两层: skills_root 直接含 skill 目录 (catfish_home/skills),
                # 或 skills_root 含 category/skill 二层 (hermes 真 productivity/, devops/ 等)
                skill_dirs: List[Path] = []
                for child in sorted(skills_root.iterdir()):
                    if not child.is_dir():
                        continue
                    # 真 child 自身有 SKILL.md? → 它就是 skill 真目录
                    if (child / "SKILL.md").exists():
                        skill_dirs.append(child)
                    else:
                        # 真 category 层, 真扫一级子目录
                        try:
                            for grandchild in sorted(child.iterdir()):
                                if (grandchild.is_dir() and
                                        (grandchild / "SKILL.md").exists()):
                                    skill_dirs.append(grandchild)
                        except OSError:
                            continue

                for skill_dir in skill_dirs:
                    name = skill_dir.name
                    if name in seen_names:
                        continue  # 真 dedupe (优先 catfish 自家版本)
                    head = _read_text_safe(skill_dir / "SKILL.md", 800)
                    if not head:
                        continue
                    candidates_list.append((name, head.strip()[:600]))
                    seen_names.add(name)
        except OSError:
            pass

        if not candidates_list:
            return ""

        # 真 P2-2: query 真打分排序
        #
        # 8/17: 加闸门 + 相对下限, 跟 _render_strategic_docs 同一套判据。
        #
        # 改之前实测: 不管问什么, 这一段恒定 4,85x~4,91x 字符 (16 个 skill) ——
        # 排序是有效的 (问 cron 就把 cron-job-editing 排第一), 但排完之后
        # **预算没花完就继续塞**, 于是第 5~16 名照样进 prompt。
        #
        # 折叠掉是安全的, 因为**技能名单并没有消失**: hermes 自己的
        # <available_skills> 已经把全部 83 个 skill 连同一行描述放进 system
        # prompt 的 stable 层 (被缓存), 而 skills_list / skill_view /
        # search_skills 三个工具在聊天路径上都在手里。这一段的职责只是
        # "给当下最相关的那两三个补上细节", 不是"告诉模型有哪些技能"。
        #
        # 相对下限用 0.5 不用 strategic 那边的 0.25: 91 个 skill 的描述共用
        # 大量业务词汇, 分布压得很紧 —— 实测 "帮我改一下 cron 定时任务" 在
        # 0.25 下留全部 91 个 (27,533 字符), 比不筛还糟。
        query_clean = (query or "").strip()
        if query_clean:
            # 真用 char-level set 真简单 Jaccard (无 jieba 依赖, plugin 真 light)
            q_chars = _query_token_set(query_clean)
            scored: List[tuple] = []
            for name, head in candidates_list:
                n_tok, h_tok = _query_token_set(name), _query_token_set(head)
                # 真 name 真权重更高 (skill 真定位作用)
                total = (_jaccard_similarity(q_chars, n_tok) * 3.0
                         + _jaccard_similarity(q_chars, h_tok))
                d_tok = n_tok | h_tok
                scored.append((total, name, head,
                               _overlap_coefficient(q_chars, d_tok),
                               len(q_chars & d_tok)))
            scored.sort(key=lambda t: -t[0])

            top_overlap, top_hits = max(((t[3], t[4]) for t in scored), default=(0.0, 0))
            if top_overlap < _RELEVANCE_MIN_OVERLAP or top_hits < _RELEVANCE_MIN_HITS:
                return (
                    "## 🛠 可用技能 (catfish skills)\n\n"
                    f"_跟当前话题都不沾边, 细节已折叠 — 全部 {len(candidates_list)} 个技能的"
                    "名字和简介在 `<available_skills>` 里都列着, 要看某个的完整说明用 "
                    "`skill_view`._"
                )
            scored = [t for t in scored if t[3] >= top_overlap * _SKILLS_FLOOR_RATIO]
            ordered = [(name, head) for _s, name, head, _ov, _h in scored]
        else:
            # 真 query 空 → 按字母序 (老行为)
            ordered = candidates_list

        # 真按 budget 真截
        entries: List[str] = []
        budget = _BUDGETS["skills_catalog"]
        # 真 P2-2: query 真有 → 真 cap 砍到 5000 chars (top-K 真够, 减 system prompt)
        if query_clean:
            budget = min(budget, 5000)
        for name, head in ordered:
            entry = f"### {name}\n\n{head}\n"
            if len(entry) > budget:
                break
            entries.append(entry)
            budget -= len(entry)
            if budget <= 0:
                break

        if not entries:
            return ""
        title = "## 🛠 可用技能 (catfish skills)"
        if query_clean and len(entries) < len(candidates_list):
            title += f" — 按当前话题筛 top {len(entries)}/{len(candidates_list)}"
        return f"{title}\n\n" + "\n".join(entries)

    def _render_strategic_docs(
        self, catfish_home: Path, query: str = "",
    ) -> str:
        """BL-STRATEGIC-DOC-SYNC (6/7 鸿波 audit 后 ship): 战略 / 设计 doc 注入.

        # 用例

        把 14 份战略 / 战术 doc (manifesto / patent landscape / moat assessment
        / capability gaps / advisory spec / sandbox audit 等) 拷到
        ~/.catfish/strategic_docs/*.md, 这里自动 inject 关键段进 LLM prefetch.

        跟 _render_skills_catalog 同 pattern, 几个细节区别:

        - 文件结构: 平的 (无嵌套), 每个 .md 直接读
        - head 限: 1500 byte (跟 wiki concept 类似篇幅)
        - frontmatter 去掉 (markdown YAML 头部)
        - query 空 → 全注入字母序 cap 8KB (_BUDGETS["strategic_docs"])
        - query 触发 → top-K Jaccard 排序 + cap 5KB

        # 跟 wiki/concepts/ 区别

        wiki/concepts/ 是 catfish-memory plugin distill 出来的 entity/concept (短),
        会被 plugin distill 流程当源数据再处理 (污染 entity 抽取). strategic_docs
        是**原始战略 doc**, 不该进 distill 链 — 独立目录隔开.

        # 跟 manifesto 公理一致

        - 数据在 ~/.catfish/strategic_docs/ 员工本机, 不上传中央
        - LLM 主动看 / 员工 chat 提到关键词 → inject, pull-based
        """
        docs_root = catfish_home / "strategic_docs"
        if not docs_root.is_dir():
            return ""

        candidates_list: List[tuple] = []  # (name, head)
        try:
            for child in sorted(docs_root.iterdir()):
                if not child.is_file() or child.suffix.lower() != ".md":
                    continue
                name = child.stem  # foo.md → "foo"
                # 读前 1500 byte (含 frontmatter, 下面去掉)
                head = _read_text_safe(child, 1500)
                if not head:
                    continue
                # 去 frontmatter (--- ... ---)
                if head.startswith("---\n"):
                    end = head.find("\n---\n", 4)
                    if end > 0:
                        head = head[end + 5:]  # 跳过结束 ---\n
                candidates_list.append((name, head.strip()[:1200]))
        except OSError:
            pass

        if not candidates_list:
            return ""

        # query 打分排序 (跟 _render_skills_catalog 同套 helper)
        #
        # 两个分数各管一件事, 别混:
        #   score   (Jaccard, name 权重 ×3) → **排序**。它带着"标题比正文语义浓"
        #                                     这个启发, overlap 表达不了。
        #   overlap (|q∩d|/|q|)             → **要不要留**。它不受文档长度影响,
        #                                     换个员工的机器仍然成立。
        query_clean = (query or "").strip()
        if query_clean:
            q_chars = _query_token_set(query_clean)
            scored: List[tuple] = []
            for name, head in candidates_list:
                n_tok, h_tok = _query_token_set(name), _query_token_set(head)
                # name 权重更高 (doc 标题最语义浓)
                total = (_jaccard_similarity(q_chars, n_tok) * 3.0
                         + _jaccard_similarity(q_chars, h_tok))
                d_tok = n_tok | h_tok
                ov = _overlap_coefficient(q_chars, d_tok)
                scored.append((total, name, head, ov, len(q_chars & d_tok)))
            scored.sort(key=lambda t: -t[0])
            # 相关性闸门 + 全不沾边时折叠 (8/15 加, 8/17 换成 overlap)。
            #
            # 排序本身是好的 (实测 "沙箱部署"→SANDBOX-DEPLOY 第一, "护城河"→
            # MOAT-ASSESSMENT 第一)。问题出在排完之后**预算没花完就继续塞**。
            #
            # 闸门用 overlap 不用 Jaccard —— 见 _overlap_coefficient 的说明:
            # Jaccard 的绝对值随文档词汇量摆动 275 倍, 任何绝对阈值都只在调它
            # 那台机器上成立。员工机器上 8 篇的实测 overlap:
            #
            #     "护城河"           1.00 / 0.00 …          留 1
            #     "沙箱部署"         0.57 / 0.00 …          留 1
            #     "专利布局怎么样了"  0.77 / 0.23 / 0.08 …   留 2
            #     "catfish 的整体设计" 0.80 / 0.75 / 0.70 …  留多篇 (八篇都讲 catfish)
            #     ────────────────────────────────────────
            #     "高新技术企业认定"  0.20                   全折叠
            #     "福富的资质情况"    0.10                   全折叠
            #     "测试" / "在吗"     0.00                   全折叠
            #
            # 该留的落在 0.57~1.00, 该折的落在 0.00~0.20, 中间空一大段 ——
            # 阈值取 0.3 / 0.4 / 0.5 实测都全对, 取中间的 0.4。
            #
            # 顺带: "高新技术企业认定" 这个 case 之前用 Jaccard + 中位数倍数
            # 做区分度检验时跟 "catfish" 分不开 (两者都是分布平坦), 我因此撤掉
            # 过那条。overlap 把它俩一刀切开了 (0.20 vs 0.80)。
            top_overlap, top_hits = max(((t[3], t[4]) for t in scored), default=(0.0, 0))
            if top_overlap < _RELEVANCE_MIN_OVERLAP or top_hits < _RELEVANCE_MIN_HITS:
                # 一篇都没沾边。老行为是退回字母序, 结果把**全部 7 篇**倒进去
                # (~1,680 token) —— 恰好在最没用的时候花最多的钱。
                #
                # 改成只留一行计数提示。这不是我新发明的约定:
                # catfish_search_docs 的 description 里已经写着「当 system prompt
                # 折叠区显示 'catfish strategic docs 还有 N 份' …必用」, 而它是
                # P43 提升的核心工具, 每轮都在模型手里。折叠掉不等于够不着。
                return (
                    "## 📘 战略 / 设计 doc (catfish strategic docs)\n\n"
                    f"_跟当前话题都不沾边, 已折叠 — catfish strategic docs 还有 "
                    f"{len(candidates_list)} 份, 需要时用 `catfish_search_docs` 查._"
                )
            # 相对下限也走 overlap: 比值本身是无量纲的, 但拿 Jaccard 算比值时
            # 各篇除以各自不同的并集, 文档长短不一就不保序了。overlap 没这问题。
            scored = [t for t in scored if t[3] >= top_overlap * _STRATEGIC_FLOOR_RATIO]
            ordered = [(name, head) for _s, name, head, _ov, _h in scored]
        else:
            ordered = candidates_list

        # budget 截
        entries: List[str] = []
        budget = _BUDGETS.get("strategic_docs", 8000)
        # query 有 → 再收一档。注意 _BUDGETS["strategic_docs"] 6/16 已从 8K 砍到
        # 3K (P3.5.5), 所以这个 min 现在不起作用 —— 留着是防以后有人把 _BUDGETS
        # 调回去时这条路径失去上限。
        if query_clean:
            budget = min(budget, 5000)
        for name, head in ordered:
            entry = f"### {name}\n\n{head}\n"
            if len(entry) > budget:
                break
            entries.append(entry)
            budget -= len(entry)
            if budget <= 0:
                break

        if not entries:
            return ""
        title = "## 📘 战略 / 设计 doc (catfish strategic docs)"
        if query_clean and len(entries) < len(candidates_list):
            title += f" — 按当前话题筛 top {len(entries)}/{len(candidates_list)}"
        return f"{title}\n\n" + "\n".join(entries)

    def _render_task_status(self, catfish_home: Path) -> str:
        """P3.5.203 (β 7/9 鸿波): 从 ~/.catfish/advisor_cache.json 抽最近员工对
        task 的 chatStatus (P3.5.202 C 方案 LLM 判定的 resolved / paused), 拼成
        markdown 注入 system prompt.

        5 层记忆 audit 后发现: briefing filter (advisor_cache.taskChatSummaries
        + chatStatus) 已经 status 语义驱动, 但 chat / proactive starter 走 memory
        provider prefetch (读 distilled_facts / journal), 拿不到 chatStatus. 员工
        在早安不撞坑, 但平常聊天照样被 LLM 主动问已关闭的事. β 把 advisor_cache
        里 status 桥到 prefetch, 让 chat 主入口也拿到.

        跟 α (Dream 蒸馏加"任务状态"段) 双保险:
          - α: 长期 (每 24h Dream 跑) 存 distilled_facts.md 里
          - β: 短期 (员工聊完立即通过 advisor_cache refresh 时更新)
          - chat / proactive / briefing 三处都拿得到 status, 无死角

        输出格式对 LLM 友好, 明确 "尊重员工立场":

          ## 🎯 员工最近对具体事的表态 (尊重不主动推)

          - <title> (uid=xxx): resolved — 员工说事已办完/交付/确认误报
          - <title> (uid=xxx): paused — 员工说暂时关闭/暂缓/先放放/等通知

          请**不要**主动问这些事的进展, 员工已明确表态. pending 事不列 (默认可
          问). 无数据 (advisor_cache 不存在 / 无 taskChatSummaries) 返空段.

        性能: 每 turn read + parse JSON (~24 KB 大小, <2ms). 跟 employee_journal
        同款 async prefetch 时机, 无额外 IO 开销. fail-silent 挂了返空.
        """
        cache_path = catfish_home / "advisor_cache.json"
        if not cache_path.exists() or not cache_path.is_file():
            return ""
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.debug("catfish-memory: 读 advisor_cache.json 失败 (%s), skip task_status 段", e)
            return ""

        summaries = data.get("taskChatSummaries") or {}
        if not isinstance(summaries, dict) or not summaries:
            return ""

        # main tasks title map: uid → title (advisor_cache 里 result.mainTasks 存)
        result = data.get("result") or {}
        main_tasks = result.get("mainTasks") if isinstance(result, dict) else None
        title_by_uid: Dict[str, str] = {}
        if isinstance(main_tasks, list):
            for t in main_tasks:
                if isinstance(t, dict):
                    uid = t.get("taskUid")
                    title = t.get("title")
                    if isinstance(uid, str) and isinstance(title, str):
                        title_by_uid[uid] = title

        lines: List[str] = []
        for uid, s in summaries.items():
            if not isinstance(s, dict):
                continue
            status = s.get("status")
            if status not in ("resolved", "paused"):
                # pending / undefined 不列 — 默认可以问
                continue
            title = title_by_uid.get(uid) or s.get("title") or f"(uid={uid})"
            hint = "员工说已办完/交付/确认误报" if status == "resolved" else "员工说暂时关闭/暂缓/先放放/等通知"
            lines.append(f"- {title} (uid={uid}): {status} — {hint}")

        if not lines:
            return ""

        return (
            "## 🎯 员工最近对具体事的表态 (P3.5.203 尊重员工立场, 不主动推)\n\n"
            + "\n".join(lines)
            + "\n\n"
            + "请**不要**主动问这些事的进展或催员工. 员工已明确表态: resolved = "
            + "事已完结, paused = 员工主动搁置会自己回来找. pending 状态的事不在此段, "
            + "默认可以聊."
        )

    def _render_session_meta(self, catfish_home: Path) -> str:
        """时间感 — 今天日期锚点 (无条件) + 距上次聊天 (若有 session_meta).

        P3.5.219 (7/13 鸿波 catch): 老 raw last_chat_iso, LLM 无当天日期锚点 →
        小鲶把 7/13 周日算成"下周一". 治本: prefetch 时用 datetime.now() 拿今天
        date+weekday 无条件注入; last_chat_iso 若存在附一行"距上次 N 天前" humanize.

        # 不用 session_meta['today_date'] 的理由 (agent audit 铁证)
        hermes memory_manager.py:495 prefetch_all 是同步, line 557 sync_all 是
        background thread → tick 只在 turn 结尾更新. 跨天首轮 prefetch 看到的
        today_date **是昨天** (stale 一轮). datetime.now() 是唯一 fresh 手段.
        跟 _render_expense_summary line 869 同款 pattern.

        # 保留 _tick_session_meta 3 字段写入 (无 dead code)
        - today_count: Rust commands/relation.rs:161 Dashboard RelationCard 消费
        - today_date: _tick_session_meta 内部跨天判断 (reset today_count)
        - last_chat_iso: 本 render + Rust relation.rs:169 humanize_since

        # 军规避坑
        P3.5.213 negation blindness: 只给正例 (今天 fresh 事实), 无禁词, 无红线.
        P3.5.217 军规精简: 无军规文本, 只事实锚点. Diff +50 chars ≈ +30 tokens.
        """
        now = datetime.now().astimezone()
        weekday_cn = "一二三四五六日"[now.weekday()]
        lines = [
            "## 🕒 时间感",
            "",
            f"今天: {now.strftime('%Y-%m-%d')} 星期{weekday_cn}",
        ]

        path = catfish_home / "session_meta.json"
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    last = data.get("last_chat_iso")
                    if last:
                        try:
                            last_dt = datetime.fromisoformat(last)
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=now.tzinfo)
                            secs = int((now - last_dt).total_seconds())
                            # 对齐 Rust relation.rs humanize_seconds (line 277-299)
                            # 5 档: 刚刚 / N 分钟 / N 小时 [M 分] / N 天 [M 小时]
                            if secs < 60:
                                human = "刚刚"
                            elif secs < 3600:
                                human = f"{secs // 60} 分钟前"
                            elif secs < 86_400:
                                h = secs // 3600
                                m = (secs % 3600) // 60
                                human = f"{h} 小时前" if m == 0 else f"{h} 小时 {m} 分前"
                            else:
                                d = secs // 86_400
                                h = (secs % 86_400) // 3600
                                human = f"{d} 天前" if h == 0 else f"{d} 天 {h} 小时前"
                            lines.append(f"上次聊天: {last} ({human})")
                        except (ValueError, TypeError):
                            lines.append(f"上次聊天: {last}")
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("session_meta render 失败 %s", e)

        return "\n".join(lines)

    def _render_wiki_summary(self, catfish_home: Path, query: str = "") -> str:
        """BL-CATFISH-WIKI-MODE P3.3.11 (6/4): wiki summary 注入 prefetch.

        列 wiki/entities + wiki/concepts, 让 LLM 知道:
          - 员工 wiki 已有什么 entity / concept (避免 chat 重复抽)
          - reference 时用精确标题 (e.g. `[[ISO 27001]]`)
          - 人工新建的 file 自动进 prefetch (每轮实时读 file system)

        # 8/15 改了三件事 (查"一句话 4 万 token"时发现)

        这一段是 prefetch 里最大的 (2,222 token), 而且原来有三个毛病:

        1. **列的是文件名, 不是标题。** 员工机器上 261 个条目里 203 个是拼音
           slug (`AAA-xin-yong-deng-ji-zheng-shu`), 而它们的 frontmatter 里
           写着中文 `title: AAA信用等级证书`。
           而 `resolve_wiki_ref` 是 **title 第一优先、slug 第四**
           (wiki_resolve.py:90 vs :107) —— 也就是说旧写法一直在让 LLM 走
           最弱的那条解析路径。

        2. **不接 query, 按字母序砍到 50。** 168 个实体只露前 50, 于是
           118 个**永远**看不见, 而且永远是同一批 —— C 开头的
           `CCRC-tong-xin-...` 系列长期占位, `gaoxinjishu-qiye-rending`
           (高新技术企业认定) 这种永远进不来。员工问高新认定, wiki 里明明
           有, LLM 却看不到。这不只是费 token, 是功能缺陷。

        3. **废弃条目照列。** 30 个 `deprecated: true` 跟正主一起注进去
           (`gaoxinjishu-qiye-rending` 自己就是一个, 内容已并入
           `gaoxinjishuqiyerending`), 既费 token 又让 LLM 在两份之间犹豫。

        现在: 走 wiki_resolve.load_nodes 拿标题 (不自己再写一份 frontmatter
        解析 —— 那正是 wiki_resolve 契约测试当初要防的分叉), 跳过废弃,
        有 query 就按相关性排 top-K。

        query 空时保持老行为 (字母序 cap 50), 不给 advisor / 定时任务这些
        没有 query 的调用方引入回归。
        """
        wiki_dir = catfish_home / "wiki"
        if not wiki_dir.is_dir():
            return ""

        try:
            from wiki_resolve import load_nodes  # noqa: PLC0415  延迟 import, 避免环
            # 只读头部: 要的 title / aliases / deprecated 全在 frontmatter,
            # 正文用不上。读全文是 1.1 MB / 62 ms, 而 prefetch 每轮都跑。
            nodes = load_nodes(catfish_home, head_bytes=4096)
        except Exception:
            return ""

        live = [n for n in nodes if not n.deprecated]
        entities = [n for n in live if n.rel_path.startswith("wiki/entities/")]
        concepts = [n for n in live if n.rel_path.startswith("wiki/concepts/")]
        if not entities and not concepts:
            return ""

        query_clean = (query or "").strip()
        if query_clean:
            q = _query_token_set(query_clean)

            def _rank(pool):
                # 标题 + slug + 别名一起打分: 标题多为中文, slug 多为拼音,
                # 中文 query 只打得中前者, 英文/拼音 query 只打得中后者。
                # 两个都算再取大, 才不会因为命名风格把一半条目埋掉。
                scored = []
                for n in pool:
                    s = max(
                        [_jaccard_similarity(q, _query_token_set(n.title)),
                         _jaccard_similarity(q, _query_token_set(n.slug))]
                        + [_jaccard_similarity(q, _query_token_set(a)) for a in n.aliases]
                    )
                    scored.append((s, n))
                scored.sort(key=lambda t: (-t[0], t[1].title))
                return [n for s, n in scored if s > 0][:_WIKI_CAP_QUERY], True

            ents, ranked = _rank(entities)
            cons, _ = _rank(concepts)
            # 一个都没命中 (例: 打招呼 / 纯英文短句) → 退回字母序, 别给个空清单
            if not ents and not cons:
                ents = sorted(entities, key=lambda n: n.title)[:_WIKI_CAP_PLAIN]
                cons = sorted(concepts, key=lambda n: n.title)[:_WIKI_CAP_PLAIN]
                ranked = False
        else:
            ents = sorted(entities, key=lambda n: n.title)[:_WIKI_CAP_PLAIN]
            cons = sorted(concepts, key=lambda n: n.title)[:_WIKI_CAP_PLAIN]
            ranked = False

        suffix = " — 按当前话题排序" if ranked else ""
        lines = [f"## 🧠 员工 wiki 已有 (P3.3 知识体系 tab){suffix}"]
        for label, shown, pool in (("实体", ents, entities), ("概念", cons, concepts)):
            if not pool:
                continue
            lines.append(
                f"\n**{label} ({len(pool)})**: "
                + " · ".join(f"[[{n.title}]]" for n in shown)
            )
            if len(shown) < len(pool):
                lines.append(
                    f"_(只列了 {len(shown)}/{len(pool)} 个, 其余用 "
                    f"`catfish_wiki_search` 查, 或看 Companion 知识体系 tab)_"
                )
        lines.append("\n_chat 时引用员工 wiki 用 `[[标题]]` 精确链接._")
        return "\n".join(lines)

    def _render_employee_journal(self, catfish_home: Path) -> str:
        """长期记忆 (distilled) + 近期未蒸馏增量 (journal 尾部), 两段都注入。

        # 8/6 鸿波 catch「早安里说的项目进度, 工作台不知道」

        原来是**二选一**:

            distilled = read(distilled_facts.md)
            if distilled.strip():
                return distilled          # ← 读到就返回, journal 永远到不了
            raw = read(employee_journal.md)

        而 distilled_facts.md 是 **24h 蒸馏一次**的产物。于是最近一个蒸馏周期内
        写进 journal 的所有内容, 主聊天一个字都看不到。

        8/6 实测: 员工 15:11–15:15 在早安记了四条项目进度 (ISO 招投标 / 资质对标 /
        ISO9001+45001 / CIC), journal 里都在, 而 distilled_facts.md 停在前一晚
        22:02 —— 员工到工作台问同一件事, LLM 只能靠自己调 catfish_search_sessions
        和 execute_code 去翻文件才找得到。能找到是因为工具强, 不是因为记忆通。

        两者本来就是**互补的两层**, 不是备选关系:
          · distilled_facts.md —— 长期画像, 已蒸馏、已去重、密度高
          · employee_journal.md 尾部 —— 近期流水, 未蒸馏, 时效最新

        所以改成都注入, 预算三七开 (长期占七, 近期占三 —— 近期条目短、信息密度低,
        给太多会挤掉长期记忆)。蒸馏跑完之后, 近期那段自然并入 distilled, 不会重复
        堆积。

        journal 只取**尾部**: 它是 append-only 的, 8/6 实测已 533KB, 全量读进来
        既超预算也没意义 —— 早期内容早就蒸馏进 distilled 了。
        """
        budget = _BUDGETS["employee_journal"]
        long_budget = int(budget * 0.7)
        recent_budget = budget - long_budget

        parts: list[str] = []

        distilled = _read_text_safe(catfish_home / "distilled_facts.md", long_budget)
        if distilled.strip():
            parts.append(f"## 📝 员工长期记忆 (catfish distilled)\n\n{distilled}")

        # journal 尾部 = 尚未进入 distilled 的近期流水。_read_text_safe 从头截断,
        # 这里要的是**末尾**, 所以自己读。
        recent = self._tail_journal(
            catfish_home / "employee_journal.md", recent_budget
        )
        if recent.strip():
            if parts:
                parts.append(
                    "## 🕒 近期流水 (尚未蒸馏, 比上面的长期记忆更新)\n\n" + recent
                )
            else:
                # distilled 还不存在 (新装机器) —— journal 就是唯一的记忆, 给全额
                recent = self._tail_journal(
                    catfish_home / "employee_journal.md", budget
                )
                parts.append(f"## 📝 员工长期日记 (catfish)\n\n{recent}")

        return "\n\n".join(parts)

    @staticmethod
    def _tail_journal(path: Path, max_bytes: int) -> str:
        """读 journal 末尾 max_bytes, 并从第一个完整条目 (## 开头) 起返回。

        直接按字节截尾会把第一条切成半句, LLM 读到残句容易误读。journal 的条目
        以 `## ` 开头 (见 memory_router._route_to_journal / _route_to_reminder),
        所以往后找第一个 `\\n## ` 作为起点。找不到就整段返回 (说明尾部就是一条
        超长条目)。
        """
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        try:
            with open(path, "rb") as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                data = f.read()
        except OSError:
            return ""
        text = data.decode("utf-8", errors="ignore")
        if size > max_bytes:
            idx = text.find("\n## ")
            if idx >= 0:
                text = text[idx + 1 :]
        return text

    def _render_feedback(self, catfish_home: Path) -> str:
        records = _read_jsonl_tail(
            catfish_home / "feedback.jsonl",
            max_lines=10,
            max_bytes=_BUDGETS["feedback"],
        )
        if not records:
            return ""
        lines = []
        for r in records:
            verdict = r.get("verdict") or r.get("rating") or ""
            note = r.get("note") or r.get("comment") or ""
            icon = "👍" if verdict in ("up", "good", "👍") else "👎" if verdict in ("down", "bad", "👎") else "·"
            if note:
                lines.append(f"- {icon} {note}")
        if not lines:
            return ""
        return "## 💬 员工最近反馈 (catfish feedback)\n\n" + "\n".join(lines)
