/** 把 LLM 返回的东西变成能渲染的 AdvisorResult —— JSON 抢救 / 校验 / 归一。
 *
 * 8/15 从 briefing_advisor.ts 搬出来。
 *
 * # 这一层存在的前提: **模型的输出不可信**
 *
 * 上游是 hermes 的 agent loop, 返回的 content 可能是 reasoning + 半截 JSON 混在
 * 一起, 也可能纯 reasoning 一个 JSON 都没有。所以这里是三层兜底:
 *
 *     robustJsonParse      从一团文本里把 JSON 抢救出来
 *     parseAdvisorResult   字段校验 + enum 归一 + 默认值 (strict / lenient 两档)
 *     parseMainTask        单条任务的归一, tone 非法归到 balanced, taskUid 缺了就生成
 *
 * 跟 ADVISOR_JSON_SCHEMA (在 _prompts.ts) 是双层校验: schema 在 API 层给硬约束,
 * 这里在客户端再兜一次。**两层都要留** —— schema 只约束走 tool_call 那条路
 * (Call 2 transformToStructured), Call 1 的 agent loop 输出没有任何 schema 约束。
 *
 * # filterResolvedTasks 也在这
 *
 * 它是 P3.5.202 C 方案的核心: 按 effective status 决定一条任务还上不上早安页。
 * 放这儿是因为它跟 parseMainTask 共享同一套 status 归一语义 (mergeTaskStatus)。
 * 它是**唯一有测试的一块** (briefing_advisor_resolved.test.ts 7 条), 走
 * briefing_advisor.ts 的 `__test__` 出口暴露 —— 那个出口保持不变。
 */
import type { TaskChatStatus, TaskStatus } from "./advisor_cache";
import { mergeTaskStatus } from "./advisor_cache";
import type {
  AdvisorInput,
  AdvisorOption,
  AdvisorResult,
  AdvisorTone,
  BlindSpotItem,
  ComplianceFlag,
  GraveyardItem,
  HandledSilentlyItem,
  MainTask,
  PoliticalFlag,
  SubconsciousItem,
} from "./briefing_advisor_common";
import { VALID_TONES } from "./briefing_advisor_common";


// ─── P3.5.202 BL-ADVISOR-RESOLVED-HARDFILTER (LLM status 语义驱动) ─────
//
// 客户端 deterministic 后处理 — LLM 主 briefing 漏 §4.2 (BL-ADVISOR-RESOLVED-
// DROP) 时兜底. 严格军规: 撤 P3.3.40 时代硬编码 regex keyword (5 条 summary +
// 3 条 title regex), 换 LLM summarizeTaskChat 输出的 chatStatus 语义判定.
//
// 触发条件:
//   chatStatus === "resolved" 或 "paused" → drop 挪去 handled_silently
//
// 老 cache 迁移: cacheValid 判定加了 status 存在检查 (见 _ensureTaskChatSummaries
// FreshImpl), 无 status 的老 hit 会强制重跑 LLM 生成新 status. 一次代价, 之后
// 全干净. 不留 regex 兜底 — LLM JSON 出错 fallback pending 时保守放行, 员工
// 顶多看一次已办完的事再说一遍, 下次 briefing 就有 status.

export function filterResolvedTasks(
  result: AdvisorResult,
  previousTasks: NonNullable<AdvisorInput["previousTasks"]>,
): AdvisorResult {
  // P3.5.207 (7/9 鸿波): 合并 chatStatus + taskState 两路信号.
  // P3.5.208-B (7/10 鸿波 catch '关了几次今天又出来'): filter 之前**只按 uid
  // 匹配**, LLM 每次生成新 uid 时 previousTasks 里查不到 → 不 drop. 加 title
  // fallback: 员工遇到 uid 漂移也能被过滤 (title 精确匹配, trim 后小写化对齐).
  type Entry = { chatStatus?: TaskChatStatus; taskState?: TaskStatus };
  const bothByUid = new Map<string, Entry>();
  const bothByTitle = new Map<string, Entry>();
  for (const pt of previousTasks) {
    const entry: Entry = {
      chatStatus: pt.chatStatus,
      taskState: pt.taskState,
    };
    if (pt.taskUid) bothByUid.set(pt.taskUid, entry);
    if (pt.title) bothByTitle.set(pt.title.trim().toLowerCase(), entry);
  }

  const keptTasks: MainTask[] = [];
  const droppedTitles: string[] = [];

  for (const mt of result.mainTasks) {
    // uid 优先, title fallback (LLM 漂移救底 - P3.5.208-B)
    const bothU = bothByUid.get(mt.taskUid);
    const bothT = bothByTitle.get(mt.title.trim().toLowerCase());
    const both = bothU ?? bothT;
    const matchedBy = bothU ? "uid" : bothT ? "title" : "none";
    const effective = mergeTaskStatus(both?.taskState, both?.chatStatus);
    if (effective === "resolved" || effective === "paused") {
      console.log(
        `[advisor P3.5.208-B filter] drop ${mt.taskUid} "${mt.title}" ` +
          `(effective='${effective}', taskState='${both?.taskState ?? "-"}', ` +
          `chatStatus='${both?.chatStatus ?? "-"}', matchedBy=${matchedBy})`,
      );
      droppedTitles.push(mt.title);
      continue;
    }
    keptTasks.push(mt);
  }

  if (droppedTitles.length === 0) {
    return result;
  }

  // 重排 id (LLM 排的 1, 2, 3 留个洞不好看, 重排 1..N)
  const renumberedKept = keptTasks.map((t, i) => ({ ...t, id: i + 1 }));

  // 把 dropped 加进 handledSilently — 让员工在折叠区看得到
  const handledExtra: HandledSilentlyItem[] = droppedTitles.map((title) => ({
    type: "task_resolved",
    count: 1,
    category: `${title} 已结/误报/无风险 (BL-ADVISOR-RESOLVED-HARDFILTER 兜底)`,
  }));

  return {
    ...result,
    mainTasks: renumberedKept,
    handledSilently: [...result.handledSilently, ...handledExtra],
  };
}

// ─── 鲁棒 JSON 解析 (5/22 cold start 修) ──────────────────────────

/** LLM 输出常含前后解释文字, 剥 markdown 反引号 + 找 balanced { ... } JSON 块.
 *  失败返 null, 不抛.
 *
 *  P3.4.C (6/15 鸿波): 改用 brace counting 找第一个 balanced `{...}` 块.
 *  老 indexOf("{") + lastIndexOf("}") 在 LLM 输出 truncated 时挂 — 因为 LLM
 *  reasoning 里也含 `{tool_call}` / `{`uid`}` 等单独 `}`, lastIndexOf 命中那条,
 *  截出来 JSON 不闭合. brace counting 找真 balanced 块, 即使 LLM 后面截断也救
 *  开头那个完整 JSON.
 *
 *  实测鸿波 6/15: LLM 输出 "所有扫描完成。结果汇总: ... 现在输出最终 JSON。
 *  {tier: ..., main_tasks: [...]}" — 前面 reasoning 含 `{...}` (引用 task_uid 等),
 *  lastIndexOf 命中那种, 老算法 parse 挂. brace counting 找第一个真 balanced
 *  跳过 reasoning 引用.
 */
export function robustJsonParse(content: string): unknown | null {
  if (typeof content !== "string") return null;
  const s = content
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/```$/, "")
    .trim();
  // 1. 先全文 parse 试试 (LLM 听话只输出 JSON 时走这条)
  try {
    return JSON.parse(s);
  } catch {
    /* 落空走 brace counting */
  }

  // 2. brace counting — 从第一个 `{` 起, count 字符串字面量内的 brace 不算,
  //    找到 balanced `}` 立即返. 救 LLM truncated 输出.
  const first = s.indexOf("{");
  if (first < 0) return null;
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = first; i < s.length; i++) {
    const c = s[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (c === "\\" && inString) {
      escape = true;
      continue;
    }
    if (c === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) {
        // 找到 balanced — 截出 [first, i+1) parse
        try {
          return JSON.parse(s.slice(first, i + 1));
        } catch {
          // 这个 balanced 块本身不是合法 JSON (e.g. trailing comma) — 再往下找
          // 下一个 balanced 块. 但代价大, 简化: 直接返 null 让调用方降级.
          return null;
        }
      }
    }
  }
  // 跑完没遇到 depth=0 → LLM 输出真截断, JSON 未闭合. 返 null.
  return null;
}

// ─── 解析 LLM JSON 输出 ────────────────────────────────────────

/** P3.4.E.7 (6/15 鸿波): mode 参数 — strict frontline/mid options<2 拒, lenient 兜底接受.
 *
 *  设计:
 *   - Call 1 (hermes agent loop) parse: strict — 让 options 不足触发 Call 2 strict schema 重做
 *   - Call 2 (transformToStructured) parse: strict — Call 2 已用 minItems schema 强约束, 应该过
 *   - 第 3 层兜底: Call 1 + Call 2 都挂 (LLM 极端不听话), _fetchBriefingAdvisorImpl 用 lenient
 *     重新 parse Call 1 原 content — 至少给员工看 LLM 给的内容, 不让 UI 完全空数据诊断卡.
 */
export function parseAdvisorResult(raw: unknown, mode: "strict" | "lenient" = "strict"): AdvisorResult | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;

  const tier = obj.tier;
  if (tier !== "frontline" && tier !== "mid" && tier !== "senior") {
    console.warn("[advisor] tier 非法:", tier);
    return null;
  }

  const rawTasks = obj.main_tasks ?? obj.mainTasks;
  if (!Array.isArray(rawTasks)) {
    console.warn("[advisor] main_tasks 不是数组:", rawTasks);
    return null;
  }

  const mainTasks: MainTask[] = [];
  for (const t of rawTasks) {
    if (!t || typeof t !== "object") continue;
    const task = parseMainTask(t as Record<string, unknown>);
    if (task) mainTasks.push(task);
  }

  // handledSilently / handled_silently 兼容 snake/camel
  const rawHandled = obj.handled_silently ?? obj.handledSilently ?? [];
  const handledSilently: HandledSilentlyItem[] = [];
  if (Array.isArray(rawHandled)) {
    for (const h of rawHandled) {
      if (!h || typeof h !== "object") continue;
      const it = h as Record<string, unknown>;
      const type = typeof it.type === "string" ? it.type : null;
      const count = typeof it.count === "number" ? it.count : null;
      if (!type || count === null) continue;
      handledSilently.push({
        type,
        count,
        category: typeof it.category === "string" ? it.category : "",
      });
    }
  }

  // P3.4.E.7 (6/15 鸿波): tier-aware options 数量强校验 — 不达标 return null 强逼走 Call 2.
  //   真因: BL-ADVISOR-PROMPT-CONFORMANCE #6b (parseMainTask line 1512) 老只 warn 不修,
  //   鸿波 6/15 撞 'task 巡视巡察整改回头看确认 只 1 个 option'. SYSTEM_PROMPT 强约束
  //   "2-3 个 options" LLM 仍漏 — Call 1 走 hermes 没法用 strict schema.
  //   修法: parseAdvisorResult 加 tier-aware 校验 → return null → _fetchBriefingAdvisorImpl
  //   走 Call 2 fallback (transformToStructured + ADVISOR_JSON_SCHEMA options.minItems=2
  //   strict schema) → DeepSeek beta 拒不符合 schema 的 args, 100% 强制 ≥2 option.
  //   senior tier "异常例外型主菜" 允许 0 options (SYSTEM_PROMPT §3 已说), 不校验.
  if (mode === "strict" && (tier === "frontline" || tier === "mid")) {
    for (const task of mainTasks) {
      if (task.options.length < 2) {
        console.warn(
          `[advisor] P3.4.E.7 strict 校验失败: task '${task.title}' options=${task.options.length} < 2 ` +
            `(tier=${tier}), return null 触发 P3.4.E Call 2 strict schema 重做`,
        );
        return null;
      }
    }
  }
  // lenient mode: 接受 options<2 (Call 1+Call 2 都 strict 挂时兜底), 但仍 warn.
  if (mode === "lenient" && (tier === "frontline" || tier === "mid")) {
    for (const task of mainTasks) {
      if (task.options.length < 2) {
        console.warn(
          `[advisor] P3.4.E.7 lenient 模式接受 options<2: task '${task.title}' options=${task.options.length} ` +
            `(tier=${tier}, LLM 不听话兜底, UI 显示但只 1 个选项)`,
        );
      }
    }
  }

  // P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection parse.
  // optional — LLM 没返 / 返空 / parse 错 → 0 item, UI 自动不渲染 (length 0 早返).
  // snake_case + camelCase 双兼容 (LLM 易 drift, parseMainTask 同款).
  const subconscious: SubconsciousItem[] = [];
  const subRaw =
    (obj as Record<string, unknown>).subconscious
    ?? (obj as Record<string, unknown>).sub_conscious;
  if (Array.isArray(subRaw)) {
    for (const item of subRaw.slice(0, 3)) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const topic = typeof o.topic === "string" ? o.topic.trim() : "";
      const count =
        typeof o.count === "number" ? o.count : Number(o.count) || 0;
      const evidence = typeof o.evidence === "string" ? o.evidence.trim() : "";
      const reflectPrompt =
        typeof o.reflectPrompt === "string"
          ? o.reflectPrompt.trim()
          : typeof o.reflect_prompt === "string"
            ? o.reflect_prompt.trim()
            : "";
      if (!topic || count < 1 || !evidence) continue;
      subconscious.push({ topic, count, evidence, reflectPrompt });
    }
  }

  const graveyard: GraveyardItem[] = [];
  const graveRaw = (obj as Record<string, unknown>).graveyard;
  if (Array.isArray(graveRaw)) {
    for (const item of graveRaw.slice(0, 3)) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const name = typeof o.name === "string" ? o.name.trim() : "";
      const lastSeen =
        typeof o.lastSeen === "string"
          ? o.lastSeen.trim()
          : typeof o.last_seen === "string"
            ? o.last_seen.trim()
            : "";
      const evidence = typeof o.evidence === "string" ? o.evidence.trim() : "";
      if (!name || !lastSeen) continue;
      graveyard.push({ name, lastSeen, evidence });
    }
  }

  const blindSpots: BlindSpotItem[] = [];
  const blindRaw =
    (obj as Record<string, unknown>).blindSpots
    ?? (obj as Record<string, unknown>).blind_spots;
  if (Array.isArray(blindRaw)) {
    for (const item of blindRaw.slice(0, 3)) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const topic = typeof o.topic === "string" ? o.topic.trim() : "";
      const signal = typeof o.signal === "string" ? o.signal.trim() : "";
      const evidence = typeof o.evidence === "string" ? o.evidence.trim() : "";
      const reflectPrompt =
        typeof o.reflectPrompt === "string"
          ? o.reflectPrompt.trim()
          : typeof o.reflect_prompt === "string"
            ? o.reflect_prompt.trim()
            : "";
      if (!topic || !signal || !evidence) continue;
      blindSpots.push({ topic, signal, evidence, reflectPrompt });
    }
  }

  // P3.5.32.2 (6/18 鸿波 debug): 显式 log 3 维 parse 结果, 方便鸿波 devtools console
  // 看 LLM 到底返了什么. 鸿波本机 6/18 上午看不到 cards, 真因可能是:
  //   (a) cache 老数据 (computedAt 早于 P3.5.32 ship) → 点"刷新"触发 fresh LLM
  //   (b) LLM Call 1 没 emit 3 字段 (hermes 弱 schema, LLM 自由发挥, 可能漏)
  //   (c) 数据真不够 (7 天 sessions < 5) → LLM 返空 array, cards 0 渲染
  console.log("[advisor] P3.5.32 3 维 parse:", {
    subconscious: subconscious.length,
    graveyard: graveyard.length,
    blindSpots: blindSpots.length,
    rawHasSubconscious:
      "subconscious" in (obj as Record<string, unknown>)
      || "sub_conscious" in (obj as Record<string, unknown>),
    rawHasGraveyard: "graveyard" in (obj as Record<string, unknown>),
    rawHasBlindSpots:
      "blindSpots" in (obj as Record<string, unknown>)
      || "blind_spots" in (obj as Record<string, unknown>),
  });
  return {
    tier,
    mainTasks,
    handledSilently,
    ...(subconscious.length > 0 ? { subconscious } : {}),
    ...(graveyard.length > 0 ? { graveyard } : {}),
    ...(blindSpots.length > 0 ? { blindSpots } : {}),
  };
}

/** P3.3.9 (6/10): 生成 6 字符 [a-z0-9] uid. LLM 没返 task_uid 时 fallback 用. */
export function generateTaskUid(): string {
  return Math.random().toString(36).slice(2, 8).padEnd(6, "0");
}

export function parseMainTask(t: Record<string, unknown>): MainTask | null {
  const title = t.title;
  if (typeof title !== "string" || !title.trim()) return null;
  const id = typeof t.id === "number" ? t.id : 0;

  // P3.3.9 (6/10): task_uid 读, 兼容 task_uid / taskUid; 没返 fallback 生成新.
  const rawUid =
    typeof t.task_uid === "string" && t.task_uid.length >= 4
      ? t.task_uid
      : typeof t.taskUid === "string" && t.taskUid.length >= 4
      ? t.taskUid
      : null;
  const taskUid = rawUid
    ? rawUid.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 12) || generateTaskUid()
    : generateTaskUid();
  if (!rawUid) {
    console.warn(`[advisor] task '${title}' LLM 没返 task_uid, fallback 新生成 '${taskUid}' (跨 refresh chat 历史会丢)`);
  }

  const urgencyRaw = typeof t.urgency === "string" ? t.urgency.toLowerCase() : "medium";
  const urgency: "high" | "medium" | "low" =
    urgencyRaw === "high" ? "high" : urgencyRaw === "low" ? "low" : "medium";

  const reason = typeof t.reason === "string" ? t.reason : undefined;

  const options: AdvisorOption[] = [];
  if (Array.isArray(t.options)) {
    for (const o of t.options) {
      if (!o || typeof o !== "object") continue;
      const op = o as Record<string, unknown>;
      const label = typeof op.label === "string" ? op.label : null;
      const rawTone = typeof op.tone === "string" ? op.tone.toLowerCase() : null;
      const summary = typeof op.summary === "string" ? op.summary : "";
      if (!label || !rawTone) continue;
      // BL-ADVISOR-PROMPT-CONFORMANCE #6a (6/1): tone enum 归一化.
      // 5/22 实测 LLM 偶尔出 "prepare" / "consider" 等非 enum tone. UI 不依赖
      // tone 选颜色 (只当 string 传 decisionRecord), 但 decisions.jsonl 留档
      // 时统计困难 — 改归一到 'balanced' + warn. SYSTEM_PROMPT 已说 enum,
      // 这里是兜底 fallback (不影响主线).
      const tone: AdvisorTone = (VALID_TONES as readonly string[]).includes(rawTone)
        ? (rawTone as AdvisorTone)
        : "balanced";
      if (tone !== rawTone) {
        console.warn(
          `[advisor] LLM 返非 enum tone='${op.tone}' (task: ${title}), 归一到 'balanced'`,
        );
      }
      // 5/22 鸿波 BL-DRAFTPATH-WHITELIST: 防 LLM 幻觉路径.
      // LLM 偶尔不听 SYSTEM_PROMPT, 编一个 /Users/.../Documents/xxx.md 进来.
      // 客户端二次过滤: 只接 ~/.catfish/outputs/ 下的 path, 其它当 null.
      // 这样 UI 显"无草稿, 自己写" 而不是点了报"草稿打开失败".
      const rawDraft =
        typeof op.draftPath === "string"
          ? op.draftPath
          : typeof op.draft_path === "string"
          ? op.draft_path
          : undefined;
      const safeDraft =
        rawDraft && rawDraft.includes("/.catfish/outputs/") ? rawDraft : undefined;
      if (rawDraft && !safeDraft) {
        console.warn(
          "[advisor] LLM 幻觉 draftPath, 不在 ~/.catfish/outputs/ 下, 已清:",
          rawDraft,
        );
      }
      options.push({
        label,
        tone,
        summary,
        aiLean: op.aiLean === true || op.ai_lean === true,
        draftPath: safeDraft,
      });
    }
  }

  // BL-ADVISOR-PROMPT-CONFORMANCE #6b (6/1): options 数量校验 (warn only, 不
  // 拒). senior tier 允许 0 options ("异常例外型" 主菜只列风险, SYSTEM_PROMPT
  // 已说). 但 frontline/mid tier 出 1 options 是 LLM 没按 "2-3 个建议选项"
  // 出, 失"建议选项"价值. warn 不修, 真改靠 SYSTEM_PROMPT 强约束 (#6c).
  if (options.length === 1) {
    console.warn(
      `[advisor] task '${title}' 只 1 个 option, LLM 没按 SYSTEM_PROMPT 出 2-3 个`,
    );
  }

  const complianceFlags: ComplianceFlag[] = [];
  const rawCompFlags = t.complianceFlags ?? t.compliance_flags;
  if (Array.isArray(rawCompFlags)) {
    for (const f of rawCompFlags) {
      if (!f || typeof f !== "object") continue;
      const fl = f as Record<string, unknown>;
      const type = typeof fl.type === "string" ? fl.type : null;
      const severityRaw = typeof fl.severity === "string" ? fl.severity : "medium";
      const reason_ = typeof fl.reason === "string" ? fl.reason : "";
      if (!type) continue;
      complianceFlags.push({
        type,
        severity: severityRaw === "high" ? "high" : severityRaw === "low" ? "low" : "medium",
        reason: reason_,
        matchedKeyword:
          typeof fl.matchedKeyword === "string"
            ? fl.matchedKeyword
            : typeof fl.matched_keyword === "string"
            ? fl.matched_keyword
            : undefined,
        suggestion: typeof fl.suggestion === "string" ? fl.suggestion : undefined,
      });
    }
  }

  const politicalFlags: PoliticalFlag[] = [];
  const rawPoliFlags = t.politicalFlags ?? t.political_flags;
  if (Array.isArray(rawPoliFlags)) {
    for (const f of rawPoliFlags) {
      if (!f || typeof f !== "object") continue;
      const fl = f as Record<string, unknown>;
      const type = typeof fl.type === "string" ? fl.type : null;
      const severityRaw = typeof fl.severity === "string" ? fl.severity : "medium";
      const reason_ = typeof fl.reason === "string" ? fl.reason : "";
      if (!type) continue;
      const suggestedPhrasings = Array.isArray(
        fl.suggestedPhrasings ?? fl.suggested_phrasings,
      )
        ? ((fl.suggestedPhrasings ?? fl.suggested_phrasings) as unknown[]).filter(
            (s): s is string => typeof s === "string",
          )
        : undefined;
      politicalFlags.push({
        type,
        severity: severityRaw === "high" ? "high" : severityRaw === "low" ? "low" : "medium",
        person: typeof fl.person === "string" ? fl.person : undefined,
        reason: reason_,
        matchedKeyword:
          typeof fl.matchedKeyword === "string"
            ? fl.matchedKeyword
            : typeof fl.matched_keyword === "string"
            ? fl.matched_keyword
            : undefined,
        suggestedPhrasings,
        advisoryOnly: fl.advisoryOnly === true || fl.advisory_only === true,
      });
    }
  }

  const contextRefs: string[] = [];
  const rawRefs = t.contextRefs ?? t.context_refs;
  if (Array.isArray(rawRefs)) {
    for (const r of rawRefs) {
      if (typeof r === "string") contextRefs.push(r);
    }
  }

  return {
    id,
    taskUid,
    title,
    urgency,
    reason,
    options,
    complianceFlags,
    politicalFlags,
    contextRefs,
  };
}
