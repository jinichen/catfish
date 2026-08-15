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


# 8/15: 跟 _query_token_set 一起从 catfish_memory.py 搬过来。
# 第一版漏了它 —— 依赖计算只跑了那 14 个 render 方法, 后来临时决定把
# _query_token_set / _jaccard_similarity 也搬过来时没重跑, 于是它引用的
# 这个模块级常量留在了原处。check_undefined_names.sh 抓到的 (3 处 NameError)。
_STOPCHARS = set("的了是在我你他她我们你们和跟也都就这那有没不,.,。?!、 \n()—,—:;\"'")


def _query_token_set(text: str) -> set:
    """字符级 + bigram set (去停用字), 真返用作 Jaccard 输入.

    BL-CATFISH-WIKI-MODE P3.1 (6/4): char-only → char + bigram.
    Phrase 完整匹配 (e.g. "月度通报" 完整 hit "月度通报模板") Jaccard升,
    partial match (e.g. "月度发布" 只 hit "月度") 降. 跟 BACKLOG P3.1 BL 一致.
    不依赖 jieba (plugin light, jieba 启动 100ms+).
    """
    if not text:
        return set()
    chars = {c for c in text if c not in _STOPCHARS and c.strip()}
    bigrams = {
        text[i : i + 2]
        for i in range(len(text) - 1)
        if text[i] not in _STOPCHARS
        and text[i + 1] not in _STOPCHARS
        and text[i].strip()
        and text[i + 1].strip()
    }
    return chars | bigrams


def _jaccard_similarity(a: set, b: set) -> float:
    """Jaccard |a ∩ b| / |a ∪ b|. 真空返 0."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


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
        query_clean = (query or "").strip()
        if query_clean:
            # 真用 char-level set 真简单 Jaccard (无 jieba 依赖, plugin 真 light)
            q_chars = _query_token_set(query_clean)
            scored: List[tuple] = []
            for name, head in candidates_list:
                # 真 name 真权重更高 (skill 真定位作用)
                name_score = _jaccard_similarity(q_chars, _query_token_set(name)) * 3.0
                head_score = _jaccard_similarity(q_chars, _query_token_set(head))
                total = name_score + head_score
                scored.append((total, name, head))
            scored.sort(key=lambda t: -t[0])
            ordered = [(name, head) for _score, name, head in scored]
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
        query_clean = (query or "").strip()
        if query_clean:
            q_chars = _query_token_set(query_clean)
            scored: List[tuple] = []
            for name, head in candidates_list:
                # name 权重更高 (doc 标题最语义浓)
                name_score = _jaccard_similarity(q_chars, _query_token_set(name)) * 3.0
                head_score = _jaccard_similarity(q_chars, _query_token_set(head))
                total = name_score + head_score
                scored.append((total, name, head))
            scored.sort(key=lambda t: -t[0])
            ordered = [(name, head) for _score, name, head in scored]
        else:
            ordered = candidates_list

        # budget 截
        entries: List[str] = []
        budget = _BUDGETS.get("strategic_docs", 8000)
        # query 有 → cap 5KB (top-K 够用, 减 system prompt 噪音)
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

    def _render_wiki_summary(self, catfish_home: Path) -> str:
        """BL-CATFISH-WIKI-MODE P3.3.11 (6/4): wiki summary 注入 prefetch.

        列 wiki/entities + wiki/concepts 所有 file title, 让 LLM 知道:
          - 员工 wiki 真已有什么 entity / concept** (避免 chat 重复抽)
          - reference 时真精确 用 wiki 真 title** (e.g. `[[ISO 27001]]`)
          - 真真人工新建真 file 真自动真进**真 prefetch (file system → read 实时)

        cap 50 entries 避免 prompt 撑爆. P3.3.11 真re-ingest hook 简化 :
        plugin 不需"真watch + trigger ingest"** — read on prefetch 就够了,
        因 chat LLM 每轮 都看新 wiki.
        """
        wiki_dir = catfish_home / "wiki"
        if not wiki_dir.is_dir():
            return ""
        entities_dir = wiki_dir / "entities"
        concepts_dir = wiki_dir / "concepts"

        entities: list[str] = []
        if entities_dir.is_dir():
            try:
                for f in sorted(entities_dir.glob("*.md")):
                    entities.append(f.stem)
            except OSError:
                pass
        concepts: list[str] = []
        if concepts_dir.is_dir():
            try:
                for f in sorted(concepts_dir.glob("*.md")):
                    concepts.append(f.stem)
            except OSError:
                pass

        if not entities and not concepts:
            return ""

        lines = ["## 🧠 员工 wiki 已有 (P3.3 知识体系 tab)"]
        if entities:
            lines.append(f"\n**实体 ({len(entities)})**: " + " · ".join(f"[[{n}]]" for n in entities[:50]))
            if len(entities) > 50:
                lines.append(f"_(还有 {len(entities) - 50} 个未列, 全列在 Companion 真知识体系 tab)_")
        if concepts:
            lines.append(f"\n**概念 ({len(concepts)})**: " + " · ".join(f"[[{n}]]" for n in concepts[:50]))
            if len(concepts) > 50:
                lines.append(f"_(还有 {len(concepts) - 50} 个未列)_")
        lines.append("\n_chat 时引用员工 wiki 真用真 `[[标题]]` 精确链接真._")
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
