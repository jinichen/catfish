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
import {
  advisorCacheSourceMeta,
} from "./advisor_cache";
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
import { buildUserPrompt } from "./briefing_advisor_prompts";
import {
  buildAdvisorAgentRequest,
  buildAdvisorTransformRequest,
} from "./briefing_advisor_request";
import {
  filterResolvedTasks,
  parseAdvisorResult,
  robustJsonParse,
} from "./briefing_advisor_parse";
import {
  filterAdvisorResultByEvidence,
  isAdvisorTaskBackedByCurrentInput,
  isAdvisorTransformSourceUsable,
} from "./briefing_advisor_quality";
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
          const currentTaskUids = new Set(result.mainTasks.map((task) => task.taskUid));
          const taskChatSummaries = Object.fromEntries(
            Object.entries(oldCache?.taskChatSummaries ?? {})
              .filter(([taskUid]) => currentTaskUids.has(taskUid)),
          );
          await advisorCacheSave({
            computedAt: new Date().toISOString(),
            result,
            model: input.model,
            sourceMeta: advisorCacheSourceMeta(input),
            taskChatSummaries,
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


// 早安的 advisor 不再把长期画像、Hermes memory、历史会话或周报当作本轮输入。
// 这些内容仍可供 profile/其他功能使用，但不能重新生成已经结束的待办。
async function applyRelevanceFilter(input: AdvisorInput): Promise<AdvisorInput> {
  return {
    ...input,
    ctx: {
      ...input.ctx,
      distilledFacts: "",
      hermesMemoryRecent: "",
      recentSessionBriefs: [],
      weeklyReports: [],
    },
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
      const { advisorCacheGet, advisorTaskStateGet } = await import("./advisor_cache");
      let cached = await advisorCacheGet();
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
          .filter((t) =>
            typeof t.taskUid === "string"
            && t.taskUid.length > 0
            && isAdvisorTaskBackedByCurrentInput(t, input),
          )
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

        // 摘要刷新只能处理仍有当前来源依据的任务。旧秦树鹏这类只存在于
        // 历史 cache/事实库中的条目不会再触发并行 summary 写入。
        const allowedTaskUids = new Set(enriched.map((task) => task.taskUid));
        await ensureTaskChatSummariesFresh(input.model, allowedTaskUids).catch((e) => {
          console.warn("[advisor] ensureSummary 失败 (降级继续):", e);
        });
        cached = await advisorCacheGet();
        if (cached && inputWithPrev.previousTasks) {
          const summariesAfterRefresh = cached.taskChatSummaries ?? {};
          inputWithPrev = {
            ...input,
            previousTasks: inputWithPrev.previousTasks.map((task) => ({
              ...task,
              chatSummary: summariesAfterRefresh[task.taskUid]?.summary ?? task.chatSummary,
              chatStatus: summariesAfterRefresh[task.taskUid]?.status ?? task.chatStatus,
              taskState:
                summariesAfterRefresh[task.taskUid]?.manualStatus ?? task.taskState,
            })),
          };
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

  if (typeof localStorage !== "undefined" && localStorage.getItem("catfish:debug_advisor_prompt") === "true") {
    console.log("[advisor] 完整 prompt (P3.4.6 debug 模式, localStorage flag 打开):\n" + userPrompt);
  }

  // 不挂 AbortSignal — Tauri webview 失焦会 suspend, 让 fetch 自己生命周期
  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify(buildAdvisorAgentRequest(input.model, userPrompt)),
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
      if (!isAdvisorTransformSourceUsable(content, filteredInput)) {
        console.warn(
          "[advisor] Call 1 无结构且没有本轮业务依据，拒绝交给 Call 2 补造:",
          content.slice(0, 200),
        );
        return null;
      }
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
        if (!isAdvisorTransformSourceUsable(content, filteredInput)) {
          console.warn(
            "[advisor] Call 1 schema 不匹配且没有本轮业务依据，拒绝交给 Call 2 补造",
          );
          return null;
        }
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
    const evidenceFiltered = filterAdvisorResultByEvidence(result, filteredInput);
    if (evidenceFiltered === null) {
      console.warn(
        "[advisor] 最终结构化结果未通过业务依据校验，拒绝显示及写缓存:",
        result.mainTasks.map((task) => task.title),
      );
      return null;
    }
    if (evidenceFiltered.mainTasks.length !== result.mainTasks.length) {
      console.warn(
        "[advisor] 删除无本轮输入依据的任务:",
        result.mainTasks
          .filter((task) => !evidenceFiltered.mainTasks.some((kept) => kept.taskUid === task.taskUid))
          .map((task) => task.title),
      );
    }
    result = evidenceFiltered;
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
      body: JSON.stringify(buildAdvisorTransformRequest({ model, rawContent, tier })),
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
