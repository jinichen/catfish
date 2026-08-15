/** BL-ADVISOR-BRIEFING (5/21 Phase 7 第 4+5 步): 智能参谋早安 LLM 调用层.
 *
 * 设计稿: docs/CATFISH-ADVISOR-DESIGN.md §5 (SYSTEM_PROMPT) + §7 (UI 渲染输入)
 *
 * 替代 Phase 6 的 briefing_workplan.ts (已砍). 核心差异:
 *   - prompt 让 LLM 当"参谋"不是"作家" — 不出总结, 出主菜 + 选项 + 草稿调用
 *   - 按 profile.tier 出不同粒度的主菜数 (一线 5-8 / 中层 3-4 / 高层 1-2 + 异常)
 *   - 央国企信号 strong 时主动调 check_compliance + political_sensitivity_scan
 *   - 严格 JSON 输出 (UI 渲染成 ActionCard list)
 *
 * 调用模式 (跟 5/21 学到的 Tauri webview suspend 经验):
 *   - **不挂 AbortSignal** — Tauri 失焦会 abort in-flight fetch
 *   - 走 backendUrl (8642 hermes proxy) + ?catfish_skip_identity=1 query (5/21 CORS 修)
 *   - 调 fetchMergedBriefing 同款 fetchWithAuth
 *
 * 注: 这里只管 LLM 调用 + 解析. 数据采集 (emails / events / todos / profile / ctx)
 * 由 caller (AdvisorView component) 拉好传进来. 单一职责.
 */

import { config } from "./env";
import { fetchWithAuth } from "./me";
import type { TaskStatus } from "./advisor_cache";
import { warnIfUpstreamError } from "./upstreamErrorGuard";

// 8/15: 本文件原来 2281 行 —— 仓里最大的 TS 文件, 过了 CLAUDE.md §1 的 800 红线。
// 拆成四块, 都是从这里搬出去的**同一批代码**, 不是新东西:
//
//   _common.ts     238  类型 + LLM 调用常量。最底层, 另外三块都往它依赖
//   _prompts.ts    589  SYSTEM_PROMPT / buildUserPrompt / 输出 schema
//                       —— 561 行是提示词文本, "内容即代码"
//   _parse.ts      535  JSON 抢救 / 字段校验 / status 归一 / filterResolvedTasks
//   _summaries.ts  361  任务会话摘要 (LLM 判 resolved/paused/pending)
//
// 依赖是单向的: main → prompts/parse/summaries → common, 没有回边。
//
// ⚠ 两个 in-flight 锁 (_advisorInFlight / _summaryEnsureInFlight) 各自跟**写它的
//   那个函数**绑死在同一个文件里。ES module 的 import 是活绑定, 读得到别的模块
//   后来的赋值, 但**不能跨模块赋值**。所以 _advisorInFlight 留在这里,
//   _summaryEnsureInFlight 跟着 ensureTaskChatSummariesFresh 去了 _summaries.ts。
//
// 老 caller 一个都不破 —— 下面把外部在用的 10 个名字原样 re-export 出去。
import {
  ADVISOR_DIRECT_QUERY,
  ADVISOR_TIMEOUT,
  CLIENT_TIMEOUT_MS,
  SERVICE_LLM_HEADERS,
  SERVICE_LLM_QUERY,
} from "./briefing_advisor_common";
import type {
  AdvisorFetchResult,
  AdvisorInput,
  AdvisorResult,
} from "./briefing_advisor_common";
import { ADVISOR_JSON_SCHEMA, SYSTEM_PROMPT, buildUserPrompt } from "./briefing_advisor_prompts";
import {
  filterResolvedTasks,
  parseAdvisorResult,
  robustJsonParse,
} from "./briefing_advisor_parse";
import { ensureTaskChatSummariesFresh } from "./briefing_advisor_summaries";

// ── re-export: 外部文件 (AdvisorView / TaskPicker / BriefingDetailPane /
//    InsightReflectCards / HandledSilently / advisor_cache / taskSystemPrompt /
//    briefing_advisor_resolved.test) 一直写 `from "./briefing_advisor"`, 不破它们。
export type {
  AdvisorFetchResult,
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
export { ADVISOR_TIMEOUT, CLIENT_TIMEOUT_MS, VALID_TONES } from "./briefing_advisor_common";
export { ensureTaskChatSummariesFresh } from "./briefing_advisor_summaries";


/** 5/22 cold start 修锁: 同时只允许一个 advisor LLM call 跑.
 *  React StrictMode dev useEffect 双调 → fetchBriefingAdvisor 双跑 → LLM 调 2 次浪费 token + quota.
 *  跟 recomputeProfile 同款 in-flight 锁. */
let _advisorInFlight: Promise<AdvisorResult | null> | null = null;

/** 主入口. 不挂 AbortSignal (Tauri webview suspend 经验, 5/21 学到). */
export async function fetchBriefingAdvisor(input: AdvisorInput): Promise<AdvisorFetchResult> {
  // in-flight 锁: 已在跑就复用 promise
  if (_advisorInFlight) {
    console.log("[advisor] 已在跑, 复用 in-flight promise (StrictMode 双调防御)");
    // 复用 in-flight 也加同款 timeout race — 让连续两次调都同等享受超时保护
    return raceWithTimeout(_advisorInFlight);
  }

  // P3.4.E.6 (6/15 鸿波): 记开始时间, 后台 finally 算耗时判真 timeout / race 内完成.
  //   老 log "可能已 TIMEOUT 走 stale" 永远打 — 不论 race 是否真超时, 误导诊断
  //   方向 (P3.4.3 同款 pattern, 鸿波 6/15 撞到误以为 LLM 慢, 实际 race 内完成).
  const startMs = Date.now();
  const myPromise = _fetchBriefingAdvisorImpl(input);
  _advisorInFlight = myPromise;
  // 注: 不 await 整个 promise (它要 5min), 用 race 让本次调用早返;
  // myPromise 后台跑完后:
  //   - 清锁
  //   - 如果 result 非空, 写 cache (避免 5min 后真返回的成果被丢弃)
  try {
    return await raceWithTimeout(myPromise);
  } finally {
    void myPromise
      .then(async (result) => {
        if (!result) return;
        const elapsedMs = Date.now() - startMs;
        try {
          const { advisorCacheGet, advisorCacheSave } = await import("./advisor_cache");
          // P3.3.12 (6/10): save 前先读老 cache 拿到 taskChatSummaries — 老 cache
          // 里有 _fetchBriefingAdvisorImpl 阶段刚 pre-write 的 summaries, 直接 build
          // 新 cache 会丢. merge 进去.
          const oldCache = await advisorCacheGet().catch(() => null);
          await advisorCacheSave({
            computedAt: new Date().toISOString(),
            result,
            model: input.model,
            taskChatSummaries: oldCache?.taskChatSummaries,
          });
          // P3.4.E.6 (6/15 鸿波): 区分 race 内完成 / race 外完成. 实际多数 cache 写
          //   在 race 内 (主 caller 拿到结果 + cache 同步写), 老文案"可能已 TIMEOUT"
          //   不准, 误导. 真 timeout 时主 caller 已返 ADVISOR_TIMEOUT, 后台 promise
          //   仍在跑, 完成后才打这条 log.
          const elapsedSec = (elapsedMs / 1000).toFixed(1);
          if (elapsedMs < CLIENT_TIMEOUT_MS) {
            console.log(
              `[advisor] 后台完成, 已写 cache (race 内完成 ${elapsedSec}s < timeout ${CLIENT_TIMEOUT_MS / 1000}s, 主 caller 已用上结果)`,
            );
          } else {
            console.log(
              `[advisor] 后台完成, 已写 cache (race 外完成 ${elapsedSec}s >= timeout ${CLIENT_TIMEOUT_MS / 1000}s, 主 caller 已走 stale fallback, cache 下次时段触发用)`,
            );
          }
        } catch (e) {
          console.warn("[advisor] 后台写 cache 挂:", e);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (_advisorInFlight === myPromise) {
          _advisorInFlight = null;
        }
      });
  }
}

// ─── P3.5.40 (6/18 鸿波 audit huashu-design '不凭空创造, 查已有 spec') ────────
/** advisor 跑前拉跟今日邮件/任务相关的 wiki 条目 head, 注入 prompt 防 LLM 凭印象造.
 *
 *  query 构造: 今日 emails subject + todos text + events summary 同 applyRelevanceFilter
 *    (一样的相关性 query, 但 wiki 走 wikiSearchSemantic 不走 advisor_relevance.rankRelevance —
 *    wiki 已经有 P3.5.35 / P38 的 SQLite vector cache, 直接 top-K cosine, 不重 embed).
 *
 *  返字符串: top-K wiki entries 拼成 "## <title> (kind)\n<snippet>" 段, 总长上限 ~1500 字.
 *  空字符串 = wiki 没装 / BGE-M3 没装 / 没匹配, 不影响 advisor 跑.
 */
async function fetchWikiRelevant(input: AdvisorInput): Promise<string> {
  // 构造 query (跟 applyRelevanceFilter 同构, 但允许独立调整未来)
  const parts: string[] = [];
  if (input.todos.length > 0) {
    parts.push(input.todos.map((t) => t.text).join(" "));
  }
  if (input.emails.length > 0) {
    parts.push(input.emails.map((m) => `${m.sender}: ${m.subject}`).join(" "));
  }
  if (input.events.length > 0) {
    parts.push(input.events.map((e) => e.summary).join(" "));
  }
  const query = parts.join("\n").trim();
  if (!query) {
    return "";  // 今天啥也没, 无 query, 跳过 wiki search
  }

  try {
    const { wikiSearchSemantic } = await import("./tauri");
    const res = await wikiSearchSemantic(query, 3);
    if (!res.model_loaded || res.hits.length === 0) {
      return "";  // model 未装 / 没匹配 — 静默 fallback
    }
    // 拼 top-3 head 段, 总长上限 1500 字
    const segs: string[] = [];
    let totalChars = 0;
    const MAX_CHARS = 1500;
    for (const hit of res.hits) {
      const seg = `## ${hit.title} (${hit.kind})\n${hit.snippet}`;
      if (totalChars + seg.length > MAX_CHARS) {
        break;
      }
      segs.push(seg);
      totalChars += seg.length;
    }
    return segs.join("\n\n");
  } catch (e) {
    console.warn("[advisor] P3.5.40 fetchWikiRelevant 失败, fallback 空:", e);
    return "";
  }
}


// ─── P3.5.4 (6/16 鸿波): BGE-M3 相关性筛选 ────────────────────────────
//
// distilled_facts / hermes memory § / prev_tasks 都按今天输入 (todos+emails+events)
// 算语义相关性, top-K 注入. 砍 prompt 50%+, advisor Call 1 不再 truncated.
//
// 失败 fallback 返原 input (model 缺 / embed 异常都 silent).
// caller 拿到的"过滤后 input" 喂 buildUserPrompt, buildUserPrompt 不需要改 — 它直接读
// ctx.distilledFacts / ctx.hermesMemoryRecent / previousTasks, 我们只是替换这 3 个字段值.

const RELEVANCE_TOP_K_DISTILLED = 3;     // distilled_facts 段保留前 N
const RELEVANCE_TOP_K_MEMORY = 5;        // memory § 保留前 N
const RELEVANCE_TOP_K_PREV_TASKS = 5;    // prev_tasks 保留前 N (LLM 复用 task_uid)

async function applyRelevanceFilter(input: AdvisorInput): Promise<AdvisorInput> {
  // 构造 query: 今天员工真要处理的事 (todos + emails subject + events summary)
  const queryParts: string[] = [];
  if (input.todos.length > 0) {
    queryParts.push(input.todos.map((t) => t.text).join(" "));
  }
  if (input.emails.length > 0) {
    queryParts.push(
      input.emails.map((m) => `${m.sender}: ${m.subject}`).join(" "),
    );
  }
  if (input.events.length > 0) {
    queryParts.push(input.events.map((e) => e.summary).join(" "));
  }
  const query = queryParts.join("\n").trim();

  if (!query) {
    // 今天啥也没 (advisor 实际不会跑到这, 上游已 short-circuit), fallback
    return input;
  }

  // 切段
  const {
    splitDistilledFacts,
    splitMemoryRecent,
    rankRelevance,
    pickTopK,
  } = await import("./advisor_relevance");

  const distilledSegs = splitDistilledFacts(input.ctx.distilledFacts || "");
  const memorySegs = splitMemoryRecent(input.ctx.hermesMemoryRecent || "");
  const prevTaskTexts: string[] = (input.previousTasks ?? []).map((t) => {
    // prev_task embed 输入: title + chatSummary (chat summary 更精准反映"已聊过啥")
    return t.chatSummary ? `${t.title}\n${t.chatSummary}` : t.title;
  });

  // 没东西可筛 → fallback (不调 BGE-M3)
  if (
    distilledSegs.length <= RELEVANCE_TOP_K_DISTILLED &&
    memorySegs.length <= RELEVANCE_TOP_K_MEMORY &&
    prevTaskTexts.length <= RELEVANCE_TOP_K_PREV_TASKS
  ) {
    return input;
  }

  // 并发 3 个 rank
  const [distilledRes, memoryRes, prevRes] = await Promise.all([
    distilledSegs.length > RELEVANCE_TOP_K_DISTILLED
      ? rankRelevance(query, distilledSegs, "distilled")
      : Promise.resolve(null),
    memorySegs.length > RELEVANCE_TOP_K_MEMORY
      ? rankRelevance(query, memorySegs, "memory")
      : Promise.resolve(null),
    prevTaskTexts.length > RELEVANCE_TOP_K_PREV_TASKS
      ? rankRelevance(query, prevTaskTexts, "prev_task")
      : Promise.resolve(null),
  ]);

  // model 全挂 → fallback 全量
  if (
    distilledRes?.modelLoaded === false &&
    memoryRes?.modelLoaded === false &&
    prevRes?.modelLoaded === false
  ) {
    console.warn("[advisor relevance] BGE-M3 model 全挂, fallback 全量注入");
    return input;
  }

  // 拼新 ctx + previousTasks
  let newDistilled = input.ctx.distilledFacts;
  let newMemory = input.ctx.hermesMemoryRecent;
  let newPrevTasks = input.previousTasks;

  if (distilledRes && distilledRes.modelLoaded && distilledRes.ranked.length > 0) {
    const top = pickTopK(distilledSegs, distilledRes.ranked, RELEVANCE_TOP_K_DISTILLED);
    newDistilled = top.join("\n\n");
    const cacheHits = distilledRes.ranked.slice(0, RELEVANCE_TOP_K_DISTILLED).filter((r) => r.fromCache).length;
    console.log(
      `[advisor relevance] distilled: ${distilledSegs.length} → top-${top.length}, ` +
        `${cacheHits} cache 命中, scores: [${distilledRes.ranked.slice(0, 3).map((r) => r.score.toFixed(3)).join(",")}]`,
    );
  }
  if (memoryRes && memoryRes.modelLoaded && memoryRes.ranked.length > 0) {
    const top = pickTopK(memorySegs, memoryRes.ranked, RELEVANCE_TOP_K_MEMORY);
    newMemory = top.join("\n§\n");
    const cacheHits = memoryRes.ranked.slice(0, RELEVANCE_TOP_K_MEMORY).filter((r) => r.fromCache).length;
    console.log(
      `[advisor relevance] memory: ${memorySegs.length} → top-${top.length}, ` +
        `${cacheHits} cache 命中, scores: [${memoryRes.ranked.slice(0, 5).map((r) => r.score.toFixed(3)).join(",")}]`,
    );
  }
  if (
    prevRes && prevRes.modelLoaded && prevRes.ranked.length > 0 &&
    input.previousTasks && input.previousTasks.length > RELEVANCE_TOP_K_PREV_TASKS
  ) {
    const topIndices = prevRes.ranked.slice(0, RELEVANCE_TOP_K_PREV_TASKS).map((r) => r.idx);
    newPrevTasks = topIndices
      .map((i) => input.previousTasks?.[i])
      .filter((t): t is NonNullable<typeof t> => t !== undefined);
    const cacheHits = prevRes.ranked.slice(0, RELEVANCE_TOP_K_PREV_TASKS).filter((r) => r.fromCache).length;
    console.log(
      `[advisor relevance] prev_tasks: ${prevTaskTexts.length} → top-${newPrevTasks.length}, ` +
        `${cacheHits} cache 命中, scores: [${prevRes.ranked.slice(0, 5).map((r) => r.score.toFixed(3)).join(",")}]`,
    );
  }

  return {
    ...input,
    ctx: {
      ...input.ctx,
      distilledFacts: newDistilled,
      hermesMemoryRecent: newMemory,
    },
    previousTasks: newPrevTasks,
  };
}

async function raceWithTimeout(
  p: Promise<AdvisorResult | null>,
): Promise<AdvisorFetchResult> {
  // P3.4.3 (6/15 鸿波): timer id 拿出来 race resolve 后 clearTimeout.
  //   老 setTimeout 没 clear — race 已 resolve (LLM 早返 / short-circuit 返 null
  //   见 _fetchBriefingAdvisorImpl:533 "数据全空 不调 LLM") 后, 180s 那个
  //   setTimeout 仍跑回调 console.warn "客户端 180s 超时", 但实际 race 早结束
  //   了, 这条 warning 是误报. 6/15 鸿波早安卡 console 看到这条 warning 误把
  //   "数据全空"诊断方向带偏成"LLM 慢", 排了一通错路.
  //
  //   修法: setTimeout 回调里只 resolve, console.warn 移到 race 外, 看 winner
  //   真等于 ADVISOR_TIMEOUT 才 warn. try/finally clearTimeout 防 race 赢后
  //   timer 残留再 fire (即使 resolve 没用了, console.warn 也别再 spew).
  let timerId: ReturnType<typeof setTimeout> | undefined;
  const timeoutPromise = new Promise<typeof ADVISOR_TIMEOUT>((resolve) => {
    timerId = setTimeout(() => resolve(ADVISOR_TIMEOUT), CLIENT_TIMEOUT_MS);
  });
  try {
    const winner = await Promise.race<AdvisorFetchResult>([p, timeoutPromise]);
    if (winner === ADVISOR_TIMEOUT) {
      console.warn(
        `[advisor] 客户端 ${CLIENT_TIMEOUT_MS / 1000}s 超时, 返 TIMEOUT (fetch 仍在后台跑, ` +
          "完成会写 cache, 下次时段触发能用).",
      );
    }
    return winner;
  } finally {
    if (timerId !== undefined) clearTimeout(timerId);
  }
}

async function _fetchBriefingAdvisorImpl(input: AdvisorInput): Promise<AdvisorResult | null> {
  console.log("[advisor] 调用开始", {
    tier: input.profile.tier,
    centralState: input.profile.centralState,
    confidence: input.profile.confidence,
    emails: input.emails.length,
    events: input.events.length,
    todos: input.todos.length,
    model: input.model,
  });

  // profile 占位 (confidence=0) 跳过 — Phase 7 第 1 步占位时不调 LLM
  if (input.profile.confidence === 0) {
    console.log("[advisor] profile confidence=0 (占位), 不调 LLM");
    return null;
  }

  // 数据全空保护
  if (input.emails.length === 0 && input.events.length === 0 && input.todos.length === 0) {
    console.log("[advisor] 数据全空, 不调 LLM");
    return null;
  }

  // P3.3.9 (6/10): caller 没传 previousTasks 时, 内部从 advisorCacheGet 拉
  //   (含 stale, 让 LLM 复用 uid). 老 cache 没 taskUid 字段就跳过, 不出错.
  // P3.3.12.1 (6/10): summary 只从 cache 读 (不再现场跑 LLM). 拉 summary 是
  //   ensureTaskChatSummariesFresh 的活, 它由 AdvisorView mount 时后台调,
  //   跟 advisor 主 LLM call 解耦 — cache 命中场景也能更新.
  //
  // P3.3.46 BL-ADVISOR-MEMORY-RACE-HARDFIX (6/12 鸿波 "MEMORY 没更新混乱"):
  //   原 P3.3.12.1 设计有 race — AdvisorView effect1 跑 ensureSummary (慢, LLM
  //   5-30s), effect2 跑主 advisor (快). effect2 读 cache 时 effect1 还没写完,
  //   read stale summary → LLM 看到老 MEMORY 推理混乱.
  //   Fix: 这里 await ensureTaskChatSummariesFresh 先把 MEMORY 写新, 再读 cache.
  //   ensureTaskChatSummariesFresh 内部有 in-flight 锁, AdvisorView effect1 跟
  //   这里调的会复用同一 promise, 不双倍烧 token.
  let inputWithPrev = input;
  if (!input.previousTasks) {
    try {
      // P3.3.46: await summary fresh 先 (内部 in-flight 锁防双调)
      await ensureTaskChatSummariesFresh(input.model).catch((e) => {
        console.warn("[advisor] P3.3.46 await ensureSummary 失败 (降级用 stale):", e);
      });
      const { advisorCacheGet, advisorTaskStateGet } = await import("./advisor_cache");
      const cached = await advisorCacheGet();
      // P3.5.208-B (7/10 鸿波 catch '关了几次今天又出来'): P3.5.208-A migration
      // 时序 race — AdvisorView migration useEffect 依赖 [result], 必须先 setResult
      // 才 trigger, 但 fetchBriefingAdvisor 里 filter 在 setResult **之前**跑完,
      // 读的 summaries[uid].manualStatus 是 P3.5.208-A 上线前的**空值**. 结果:
      // 员工前几天点的按钮全被无视, task 又出来.
      // 修: 拉一次老 taskState.json 兜底. manualStatus 优先, 若空 fallback
      // taskState.today[title].status. 只在 filter 用, 不 save cache.
      let legacyTaskStateToday: Record<string, { status: TaskStatus }> = {};
      try {
        const ts = await advisorTaskStateGet();
        legacyTaskStateToday = ts?.today ?? {};
      } catch (e) {
        console.warn("[advisor P3.5.208-B] 老 taskState 兜底拉取失败 (降级):", e);
      }
      if (cached && cached.result?.mainTasks?.length > 0) {
        const summaries = cached.taskChatSummaries ?? {};
        const enriched = cached.result.mainTasks
          .filter((t) => typeof t.taskUid === "string" && t.taskUid.length > 0)
          .map((t) => ({
            taskUid: t.taskUid,
            title: t.title,
            urgency: t.urgency,
            chatSummary: summaries[t.taskUid]?.summary ?? "",
            // P3.5.202 (C 方案): LLM 判定的 chat 语义 status.
            chatStatus: summaries[t.taskUid]?.status,
            // P3.5.208-A: 员工按钮点的 manualStatus (SSOT 主源).
            // P3.5.208-B: 若 manualStatus 空 (migration race), fallback 老
            // taskState.today[title] — 保证 filter 判定不误放已关 task.
            taskState:
              summaries[t.taskUid]?.manualStatus
              ?? legacyTaskStateToday[t.title]?.status,
          }));
        if (enriched.length > 0) {
          inputWithPrev = { ...input, previousTasks: enriched };
          const withSummary = enriched.filter((t) => t.chatSummary).length;
          console.log(
            `[advisor] 注入 ${enriched.length} 条 prev task ` +
              `(${withSummary} 含 chat summary, 全部来自 cache)`,
          );
        }
      }
    } catch (e) {
      console.warn("[advisor] 拉 advisor_cache 失败 (P3.3.9 uid 复用降级):", e);
    }
  }

  // P3.5.4 (6/16 鸿波): BGE-M3 相关性筛选 — 砍 distilled / memory / prev_tasks
  //   按今天输入语义相关性, 不是粗暴 slice. 失败 silent fallback 返原 input.
  //   model 缺 (~/.catfish/models/bge-m3.onnx 没下载) 时也 fallback.
  const filteredInput = await applyRelevanceFilter(inputWithPrev);

  // P3.5.40 (6/18 鸿波 audit huashu-design '不凭空创造, 查已有 spec'):
  //   advisor 跑前调 wikiSearchSemantic 拿跟今日邮件/任务相关的 wiki 条目 head, 注入 prompt.
  //   防 LLM 凭印象造客户名/项目细节/资质规定 (员工 wiki 里有具体记录的话).
  //   复用 P3.5.35 P38 的 BGE-M3 SQLite cache, 不重 embed.
  //   失败 silent fallback (空字符串), 不阻塞 advisor.
  filteredInput.wikiRelevant = await fetchWikiRelevant(filteredInput);

  const userPrompt = buildUserPrompt(filteredInput);
  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
  console.log("[advisor] 发 fetch:", url, "prompt 长度:", userPrompt.length);

  // P3.4.6 (6/15 鸿波) sanity: hermes MEMORY 近期事项段是否真拼到 prompt 里.
  //   - "✓ 已注入" = Rust briefing_context_fetch 返了 hermes_memory_recent, 内容非空, prompt 拼了 "# 近期事项" 段
  //   - "✗ 未注入" = MEMORY.md 不存在 / 全是空 § 段 / Rust 端没读 / advisor.ts buildUserPrompt 漏拼
  // 完整 prompt dump: 在 DevTools Console 跑 localStorage.setItem("catfish:debug_advisor_prompt", "true") 再刷新.
  console.log(
    "[advisor] P3.4.6 hermes memory 近期事项注入:",
    userPrompt.includes("# 近期事项") ? "✓ 已注入" : "✗ 未注入 (ctx.hermesMemoryRecent 空 / MEMORY.md 不存在 / 全空 § 段)",
  );
  if (typeof localStorage !== "undefined" && localStorage.getItem("catfish:debug_advisor_prompt") === "true") {
    console.log("[advisor] 完整 prompt (P3.4.6 debug 模式, localStorage flag 打开):\n" + userPrompt);
  }

  // 不挂 AbortSignal — Tauri webview 失焦会 suspend, 让 fetch 自己生命周期
  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model: input.model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        // P3.4.C (6/15 鸿波): 3000 → 6000.
        //   真因 (鸿波 6/15 console raw content audit): DeepSeek Flash 仍
        //   "reasoning out loud" — 跑完 tool 后输出 "所有扫描完成。结果汇总: ...
        //   现在输出最终 JSON。{ "tier": "mid", "main_tasks": [{...]" — reasoning
        //   prose ~1500 tokens + JSON ~1500 tokens, max_tokens=3000 边界刚好,
        //   JSON 经常截断 (没闭合 ] }), robustJsonParse 救不了 truncated JSON.
        //   6000 给 reasoning + JSON 都装下. 跟 P3.4.D robustJsonParse 改 brace
        //   balanced match 一起救 truncated 场景.
        max_tokens: 6000,
        temperature: 0.4,
        stream: false,
        // P3.4.10 (6/15 鸿波): OpenAI 协议强制 JSON. P3.4.9 prompt 加强对
        //   DeepSeek Flash 无效 (LLM 仍 "Good — consistent with TODO list.
        //   Let me finalize..." 输出 markdown reasoning). 真因是 reasoning
        //   model 倾向 think out loud, prompt 压不住. response_format 是 API
        //   层面强制.
        //
        //   兼容性: catfish-gateway facts_pipeline.py:165 已有同款用法, 注释
        //   "部分模型支持, 不支持的会忽略" — 加上零风险, 不破坏现有调用.
        //   DeepSeek API / Anthropic Claude / OpenAI gpt-4o-mini 全支持.
        response_format: { type: "json_object" },
      }),
    });

    console.log("[advisor] HTTP status =", resp.status);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      console.warn("[advisor] LLM 非 2xx:", resp.status, text.slice(0, 200));
      return null;
    }

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") {
      console.warn("[advisor] LLM 返非字符串 content:", content);
      return null;
    }
    console.log("[advisor] raw content (前 400):", content.slice(0, 400));

    // BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT (6/1 鸿波): gateway 端已加 detect 转 502
    // (双层防御之一). 客户端兜底再判一次, 真 LiteLLM 私有 LLM (catfish-private-main)
    // streaming fail 时返 200 + content="API call failed after 3 retries..." 标准
    // pattern. 即使 gateway 端 detect 漏掉某变体, 这里仍能识别避免"JSON 解析失败"灰区.
    // 8/8: 正则和阈值挪去 upstreamErrorGuard.ts 共用 —— 6/1 加这道防御时只加在
    // 这一处, emailDraft 和 briefing 的三个调用点一直裸着, 8/8 拟稿框里就
    // 出现了 "API call failed after 3 retries" 当草稿。别再靠人记得复制那行正则。
    if (warnIfUpstreamError("advisor", content)) return null;

    // 5/22 cold start 修: 鲁棒 JSON 解析 — LLM 输出常含前后解释文字
    // (e.g. "现在我已经分析完..."), 不只 strip markdown 反引号.
    const parsed = robustJsonParse(content);

    // P3.4.E (6/15 鸿波): robustJsonParse 失败 → 调 Call 2 transformToStructured 100% 转结构化.
    //   真因: P3.4.9/.10/.C 累积修治标 80%, 仍 20% LLM 纯 reasoning 无 JSON / truncated 救不回.
    //   Call 2 single-shot + tool_choice strict 100% 拿到结构化结果 (代价 +1-2s latency).
    //   两层叠加 (Call 1 fast path / Call 2 strict fallback) — 多数 cache hit 走 fast,
    //   少数 reasoning out loud 才触发 Call 2, 平均 latency 影响小.
    let result: AdvisorResult | null;
    if (parsed === null) {
      console.warn(
        "[advisor] robustJsonParse 失败 (LLM 纯 reasoning / truncated), 触发 P3.4.E Call 2 转结构化. 原文前 200:",
        content.slice(0, 200),
      );
      result = await transformToStructured(content, input.model, input.profile.tier);
      if (result === null) {
        console.warn("[advisor] P3.4.E Call 2 也挂, 返 null (UI 显数据诊断卡)");
        return null;
      }
    } else {
      result = parseAdvisorResult(parsed, "strict");
      // P3.4.E: parsed 拿到了但 parseAdvisorResult strict 返 null (e.g. mainTasks 缺失 / tier 非法
      //   / P3.4.E.7 frontline/mid options<2). 走 Call 2 strict schema 救场.
      if (result === null) {
        console.warn(
          "[advisor] parseAdvisorResult strict 失败 (parsed 有但 schema 不匹配 / options<2), 触发 P3.4.E Call 2",
        );
        result = await transformToStructured(content, input.model, input.profile.tier);

        // P3.4.E.7 (6/15 鸿波): 第 3 层 lenient 兜底 — Call 2 也挂时, 用 lenient mode
        //   重新 parse Call 1 原 parsed (LLM 极端不听话场景, schema 都强不动).
        //   至少给员工看 LLM 给的内容, 不让 UI 完全空数据诊断卡.
        //   lenient 接受 options<2, 只 warn 不拒.
        if (result === null) {
          console.warn(
            "[advisor] P3.4.E Call 2 也挂, 尝试 lenient mode 兜底 (接受 options<2 不空 UI)",
          );
          result = parseAdvisorResult(parsed, "lenient");
          if (result === null) {
            console.warn("[advisor] P3.4.E.7 lenient 兜底也挂 (schema 真坏), 返 null UI 显数据诊断卡");
            return null;
          }
        }
      }
    }
    // P3.3.40 BL-ADVISOR-RESOLVED-HARDFILTER (6/12 鸿波): prompt 里加了 §4.2
    // (P3.3.39), 但 LLM 听话率 80-90%, 仍会漏. 这里加 deterministic 客户端
    // 后处理 — 拿 prev task chatSummary + 当前 title, 命中"已结案信号"关键字
    // 强制挪去 handledSilently. 不依赖 LLM, 100% 命中.
    if (result && inputWithPrev.previousTasks && inputWithPrev.previousTasks.length > 0) {
      return filterResolvedTasks(result, inputWithPrev.previousTasks);
    }
    return result;
  } catch (e) {
    console.warn("[advisor] LLM 调用挂:", e);
    return null;
  }
}

// ─── P3.4.E (6/15 鸿波): Call 2 transformToStructured — strict tool_choice 100% JSON ──
//
// 设计:
//   Call 1 (现 _fetchBriefingAdvisorImpl 的 hermes agent loop): LLM 跑业务 tool
//     (catfish_check_compliance / political_sensitivity_scan 等), 拿 raw final content.
//     大多场景 content 已含合法 JSON, robustJsonParse 直接救场, 不进 Call 2.
//   Call 2 (本函数): 只在 robustJsonParse 失败 (LLM 纯 reasoning 无 JSON / truncated 救不回)
//     才触发. 单 shot 直走 catfish-gateway 8999 + tool_choice 强制 LLM 返结构化 tool_call.
//     prompt 给 LLM Call 1 的 raw final content, 让它 "把这个推理结论转成 advisor JSON".
//
// 为什么不 Call 1 就强 tool_choice:
//   Call 1 走 hermes 8642 agent loop, hermes 不读 client tools / tool_choice (api_server.py:1820).
//   就算 hermes 透传, advisor first turn 必调业务 tool (catfish_check_compliance 等), tool_choice
//   强制 submit_advisor_result 会跳过业务 tool 调用, 失去 agent loop 业务能力.
//   Two-call architecture 保留 agent loop 业务能力 + 加 100% 结构化保证. 代价 +1-2s latency.
//
// 兜底: Call 2 自己挂 → 返 null, caller 走老 robustJsonParse 失败路径 (返 null UI 显示数据诊断卡).
async function transformToStructured(
  rawContent: string,
  model: string,
  tier: "frontline" | "mid" | "senior",
): Promise<AdvisorResult | null> {
  const url = `${config.gatewayUrl}/v1/chat/completions${ADVISOR_DIRECT_QUERY}`;
  const trimmed = rawContent.length > 12000 ? rawContent.slice(0, 12000) + "\n\n[已截 ...]" : rawContent;

  // P3.4.E.7 (6/15 鸿波): senior tier 异常型主菜 0 options 合法, frontline/mid 必须 ≥2.
  //   ADVISOR_JSON_SCHEMA 默认 minItems=2 给 frontline/mid 强约束. senior 时动态 deep-clone
  //   去掉 minItems 让 senior 0 options 合法.
  let schemaForCall: typeof ADVISOR_JSON_SCHEMA | Record<string, unknown> = ADVISOR_JSON_SCHEMA;
  if (tier === "senior") {
    // 浅 deep-clone (JSON 不含函数 / 循环引用, 安全)
    const cloned = JSON.parse(JSON.stringify(ADVISOR_JSON_SCHEMA));
    // 路径: properties.mainTasks.items.properties.options.minItems
    const opt = cloned?.properties?.mainTasks?.items?.properties?.options;
    if (opt && typeof opt === "object") {
      delete opt.minItems;
      opt.description = "senior tier 异常型主菜可 0 个 options, 只列例外 + 风险";
    }
    schemaForCall = cloned;
  }

  const tierDirective =
    tier === "senior"
      ? "员工是 senior tier — 异常例外型主菜可 0 个 options, 只列例外 + 风险."
      : `员工是 ${tier} tier — 每个 mainTask 必须 2-3 个 options (口径/语气选项), 不达标 schema 会拒.`;

  const sys =
    "你是 catfish advisor 结构化转换器. 收到 advisor 的最终推理结论 (可能含 reasoning prose + 部分 JSON 混合), 必须调 submit_advisor_result tool 提交结构化 AdvisorResult. " +
    "不要返 free-text content, 不要解释, 直接调 tool. 字段缺失就用合理默认值 (mainTasks 至少含已识别的, handledSilently 缺就 []). taskUid 如原文有就复用, 没有就生成 6 字符 [a-z0-9]. " +
    tierDirective;
  const userPrompt = `# advisor 原始结论 (转结构化)\n\n${trimmed}`;

  console.log(
    "[advisor] P3.4.E Call 2 transformToStructured 触发, tier:",
    tier,
    "rawContent 长:",
    rawContent.length,
  );

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: sys },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 6000,  // 跟 Call 1 同, transform 不可能比 Call 1 输出大
        temperature: 0.1,  // 转换任务用低温, 不要 LLM 重新发挥
        stream: false,
        tools: [
          {
            type: "function",
            function: {
              name: "submit_advisor_result",
              description: "提交结构化 advisor 结果. 必须调这个 tool, 不允许 free-text content.",
              parameters: schemaForCall,
            },
          },
        ],
        tool_choice: { type: "function", function: { name: "submit_advisor_result" } },
      }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      console.warn("[advisor] P3.4.E Call 2 非 2xx:", resp.status, text.slice(0, 200));
      return null;
    }
    const data = await resp.json();
    const msg = data?.choices?.[0]?.message;

    const toolCalls = msg?.tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      const args = toolCalls[0]?.function?.arguments;
      if (typeof args === "string" && args.trim()) {
        try {
          const parsed = JSON.parse(args);
          console.log("[advisor] P3.4.E Call 2 tool_call 路径成功, args 长:", args.length);
          return parseAdvisorResult(parsed);
        } catch (e) {
          console.warn("[advisor] P3.4.E Call 2 args JSON.parse 挂:", e);
          const fallback = robustJsonParse(args);
          if (fallback) return parseAdvisorResult(fallback);
        }
      }
    }
    // LLM 仍没遵 tool_choice (DeepSeek 边缘 fallback) — 试 content
    const content = msg?.content;
    if (typeof content === "string") {
      const parsed = robustJsonParse(content);
      if (parsed) {
        console.log("[advisor] P3.4.E Call 2 tool_call 未命中, content fallback 成功");
        return parseAdvisorResult(parsed);
      }
    }
    console.warn("[advisor] P3.4.E Call 2 没拿到结构化结果, msg:", msg);
    return null;
  } catch (e) {
    console.warn("[advisor] P3.4.E Call 2 调用挂:", e);
    return null;
  }
}


// ─── 测试 export — P3.3.40 ────────────────────────────────────────
//
// 单测拿这套出来跑, 生产代码不用.
// P3.5.202.b (7/9): 撤 chatSummaryLooksResolved / titleLooksResolved /
// RESOLVED_SUMMARY_PATTERNS / RESOLVED_TITLE_PATTERNS 4 个 regex 符号 —
// C 方案 status 语义驱动后成 dead code. filterResolvedTasks 只用 status.
export const __test__ = {
  filterResolvedTasks,
};
