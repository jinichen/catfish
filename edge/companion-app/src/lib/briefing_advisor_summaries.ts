/** 任务会话摘要 —— 把每条任务底下的聊天记录压成一句话 + 一个状态。
 *
 * 8/15 从 briefing_advisor.ts 搬出来。
 *
 * # 它解决的是"早安页老让员工看已经办完的事"
 *
 * P3.5.202 (C 方案) 之前, `filterResolvedTasks` 用 5 条硬编码 regex 匹配
 * "已办完 / 已交付" 之类的关键字。员工换个说法 (今天"暂时关闭", 明天"这事推了")
 * regex 就漏, 而员工**每天都要撞一次**。
 *
 * 现在改成让 LLM 读那条任务下的聊天记录, 输出 {summary, status}:
 *     resolved  已结 / 已办完 / 已交付 / 确认误报 / 已撤销
 *     paused    暂时关闭 / 暂缓 / 先放放 / 等通知
 *     pending   球还在员工手里
 *
 * 失败时返 pending —— **保守放行**。宁可让员工多看一眼已办完的事, 也不能把
 * 真需要办的悄悄 drop 掉。这个方向不能反。
 *
 * # ⚠ _summaryEnsureInFlight 必须跟写它的函数同模块
 *
 * ES module 的 import 是活绑定, 读得到别的模块后来的赋值, 但**不能跨模块赋值**。
 * 所以 `ensureTaskChatSummariesFresh` (唯一写它的地方) 跟这个 let 绑死在一起。
 * 拆开的话 TS 会直接报错 —— 这条不会静默失败, 但值得写下来免得有人白试一次。
 *
 * # _mapWithLimit 也在这
 *
 * 8/7 加的并发闸: 原来 `Promise.all(items.map(...))` 每项一次 LLM call,
 * **并发数 = 任务数, 无上限**。上游有 "max 10 concurrent runs" 的限制,
 * advisor 一扇出就把额度吃光, 前台的邮件拟稿和早安卡片被 429 挡在门外。
 */
import {
  advisorCacheGet,
  advisorCacheSave,
  type ManualTaskStatus,
  type TaskChatStatus,
} from "./advisor_cache";
import { loadSessionMessagesAsChat } from "./sessionMessages";
import { getSession, sessionGetByTaskUid } from "./tauri";
import { taskChatGet, taskChatSize } from "./task_chat";
import { config } from "./env";
import { fetchWithAuth } from "./me";
import {
  SERVICE_LLM_HEADERS,
  SERVICE_LLM_QUERY,
} from "./briefing_advisor_common";


/** 限并发跑一批异步任务 —— 起 `limit` 个 worker 轮流领任务, 不做批次栅栏。
 *
 *  8/7 加。原来这里用 `Promise.all(items.map(...))`, 每项一次 LLM call,
 *  **并发数 = 任务数, 无上限**。上游有 `max 10 concurrent runs` 的闸,
 *  advisor 一扇出就把额度吃光, 前台的邮件拟稿 / 早安卡片被 429 挡在外面。
 *
 *  用 worker 池而不是 chunk 分批: 分批要等最慢的那个才进下一批 (栅栏),
 *  worker 池谁先空谁先领, 同样限并发但总耗时更短。
 *
 *  单项失败不影响其他 —— 调用方自己在 fn 里 try/catch (这里不吞异常,
 *  抛出来会中断整池, 所以 fn 必须自己兜住)。
 */
async function _mapWithLimit<T>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<void>,
): Promise<void> {
  let next = 0;
  const workers = Array.from(
    { length: Math.max(1, Math.min(limit, items.length)) },
    async () => {
      for (;;) {
        const i = next++;
        if (i >= items.length) return;
        await fn(items[i]);
      }
    },
  );
  await Promise.all(workers);
}

/** P3.3.12.1 (6/10): ensure 每个 mainTask 的 chat summary 是最新的 (jsonl size 没变就跳).
 *
 *  独立于 advisor 主 LLM call — 这样 cache 命中场景 (主 advisor 没跑) 仍能更新
 *  summary, 下次 advisor 真要跑时直接读 cache 即可.
 *
 *  AdvisorView mount 后异步调一次 (cache 命中也调), 不阻塞 UI.
 *  refreshKey 变 (员工点刷新) 也调.
 *
 *  实测烧 token 风险: 每个 jsonl size 变 task 跑一次 LLM. 4 task 全变 ≈ 4 次 LLM call,
 *  跟主 advisor 一次 call 比是几分之一. 失败不阻塞, summary 留旧的就旧的.
 *
 *  in-flight 锁: 跟主 advisor 同款 — React StrictMode dev useEffect 双调会让本函数
 *  瞬间双跑, 两次都读老 cache 各跑 4 次 LLM, 浪费 8 次 token. 锁复用 promise. */
export async function ensureTaskChatSummariesFresh(
  model: string,
  allowedTaskUids?: ReadonlySet<string>,
): Promise<void> {
  if (_summaryEnsureInFlight) {
    console.log("[advisor summary] ensure 已在跑, 复用 in-flight promise");
    return _summaryEnsureInFlight;
  }
  const p = _ensureTaskChatSummariesFreshImpl(model, allowedTaskUids);
  _summaryEnsureInFlight = p;
  try {
    await p;
  } finally {
    if (_summaryEnsureInFlight === p) _summaryEnsureInFlight = null;
  }
}

let _summaryEnsureInFlight: Promise<void> | null = null;

async function _ensureTaskChatSummariesFreshImpl(
  model: string,
  allowedTaskUids?: ReadonlySet<string>,
): Promise<void> {
  try {
    const cached = await advisorCacheGet();
    if (!cached || !Array.isArray(cached.result?.mainTasks)) {
      console.log("[advisor summary] ensure 跳过 — 没 cache 或没 mainTasks");
      return;
    }
    const mainTasks = cached.result.mainTasks.filter(
      (t: { taskUid?: string }) =>
        typeof t.taskUid === "string"
        && t.taskUid.length > 0
        && (!allowedTaskUids || allowedTaskUids.has(t.taskUid)),
    );
    if (mainTasks.length === 0) {
      console.log("[advisor summary] ensure 跳过 — mainTasks 全没 taskUid");
      return;
    }

    const cachedSummaries = (cached.taskChatSummaries ?? {}) as Record<
      string,
      {
        summary: string;
        /** P3.5.202 (C 方案): LLM 判定的 status, 老 cache 没这字段. */
        status?: TaskChatStatus;
        /** 员工手工状态，含 pending=重新打开；摘要刷新不能覆盖它。 */
        manualStatus?: ManualTaskStatus;
        manualStatusTs?: string;
        jsonlSize: number;
        messageCount?: number;
        computedAt: string;
      }
    >;
    const newSummaries = { ...cachedSummaries };
    let llmCalls = 0;
    let cacheHits = 0;
    let emptyTasks = 0;
    let fromStateDb = 0;
    let fromJsonl = 0;

    // ★ 8/7: 从 Promise.all 改成限并发 —— 这里每个任务一次 summarizeTaskChat
    // (LLM call), 原来是**无上限扇出**。
    //
    // 鸿波实盘: 点邮件拟稿报 429 `Too many concurrent runs (max 10)`。查 outbound_log
    // 最近 60 条 chat 请求, `companion-advisor` 占 28 条 (47%), 而 advisor_cache 里
    // taskChatSummaries 有 7 条 —— 首次跑 / 缓存失效时就是 7 个并发 LLM 一起打,
    // 再叠上同时在跑的 briefing-card / profile / wiki-suggest, 顶满 10 很容易。
    // 顶满之后**先倒霉的是别人**: 拟稿、早安卡片这些前台操作被 429 挡住,
    // 而 advisor 自己是后台任务, 慢一点没人知道。
    //
    // 限 3 并发: 7 个任务分 3 批, 后台多等两轮无感, 但给前台留出 7 个并发额度。
    await _mapWithLimit(
      mainTasks,
      3,
      async (t: { taskUid: string; title: string }) => {
        try {
          // P3.3.19 C Phase 5 (6/11): 优先拉 state.db. fallback 老 jsonl.
          //   1. sessionGetByTaskUid(taskUid) → 有 sessionId 就用 state.db
          //   2. 没有 sessionId (advisor 早过 task chat 但还没 DetailPane mount 触发 sessionCreate)
          //      → 退回 jsonl (Phase 4 migration 不一定已跑完)
          const sid = await sessionGetByTaskUid(t.taskUid).catch(() => null);

          let messages: Array<{ role: string; content: string; ts: string }> = [];
          let currentCount = 0;

          if (sid) {
            // state.db 路径
            try {
              const detail = await getSession(sid);
              currentCount = detail.meta?.messageCount ?? detail.messages.length;
              if (currentCount === 0) {
                emptyTasks++;
                return;
              }
              // 转 ChatMessage 后再降级成 summarizeTaskChat 接受的 shape
              const chatMessages = loadSessionMessagesAsChat(detail);
              messages = chatMessages.map((m) => ({
                role: m.role,
                content: m.content,
                ts: m.ts,
              }));
              fromStateDb++;
            } catch (e) {
              console.warn(`[advisor summary] state.db 拉 ${sid} 失败, fallback jsonl:`, e);
              sid && (await null); // noop, fall through
            }
          }

          if (!sid || messages.length === 0) {
            // jsonl fallback (Phase 4 migration 未跑完 / 没 session 时)
            const size = await taskChatSize(t.taskUid).catch(() => 0);
            if (size === 0) {
              emptyTasks++;
              return;
            }
            const jsonlMsgs = await taskChatGet(t.taskUid).catch(() => []);
            if (jsonlMsgs.length === 0) {
              emptyTasks++;
              return;
            }
            messages = jsonlMsgs.map((m) => ({
              role: m.role,
              content: m.content,
              ts: m.ts,
            }));
            currentCount = size; // 老 hash 用 size
            fromJsonl++;
          }

          // cache 失效判断: 优先 messageCount 对比 (state.db), 没 messageCount 则 jsonlSize
          // P3.5.202.b (7/9): 加 hit.status 存在检查 — 老 cache 无 status → 强制
          // 重跑一次 LLM 生成 status, 一次代价, 之后 filter 全走语义驱动路径.
          // 撤 regex 兜底后, 迁移完成前老 task 会被误保留在 mainTasks (无 status
          // 走 pending 分支 keep), 但只影响一次 briefing 的显示, 员工可选择再
          // 说一句 status 生成后不再推.
          const hit = cachedSummaries[t.taskUid];
          const cacheValid = hit && hit.summary && hit.status !== undefined && (
            (typeof hit.messageCount === "number" && hit.messageCount === currentCount) ||
            (typeof hit.messageCount !== "number" && hit.jsonlSize === currentCount)
          );
          if (cacheValid) {
            cacheHits++;
            return;
          }

          llmCalls++;
          const { summary, status } = await summarizeTaskChat(t.title, t.taskUid, messages, model);
          if (summary || status !== "pending") {
            // P3.5.202: 存 status 到 cache. 老 cache 结构不带 status, 新加不影响读老 cache.
            newSummaries[t.taskUid] = {
              summary,
              status,
              jsonlSize: currentCount,  // backward compat 留同字段, value 取 messageCount/size
              messageCount: currentCount,
              computedAt: new Date().toISOString(),
              // 摘要是聊天侧数据，不能覆盖员工在卡片上的显式状态。
              manualStatus: hit?.manualStatus,
              manualStatusTs: hit?.manualStatusTs,
            };
          }
        } catch (e) {
          console.warn(`[advisor summary] ensure ${t.taskUid} 失败:`, e);
        }
      },
    );

    console.log(
      `[advisor summary] ensure 完成 — ${mainTasks.length} task ` +
        `(${emptyTasks} 没聊过, ${cacheHits} cache 命中, ${llmCalls} 调 LLM; ` +
        `源: ${fromStateDb} state.db / ${fromJsonl} jsonl)`,
    );

    if (llmCalls > 0) {
      await advisorCacheSave({
        ...cached,
        taskChatSummaries: newSummaries,
      });
      console.log("[advisor summary] 已写 cache (含新 summary)");
    }
  } catch (e) {
    console.warn("[advisor summary] ensure 顶层异常:", e);
  }
}

/** P3.5.202 (C 方案 7/9 鸿波军规审判 — 去 RESOLVED regex 硬编码):
 *  LLM summarize 输出 JSON {summary, status} 而非纯文本.
 *
 *  status: "resolved" | "paused" | "pending"
 *    - resolved: 员工说的已结/已办完/已交付/已确认误报/已撤销
 *    - paused: 员工说的暂时关闭/暂缓/先放放/等通知再说 (临时搁置)
 *    - pending: 球还在员工手里, 需要继续跟进
 *
 *  以前是纯文本 summary, `filterResolvedTasks` 用 5 条硬编码 regex 匹配关键字
 *  兜底 — 员工用新说法 (今天"暂时关闭", 明天"这事推了") regex 就漏, 员工每
 *  天都要撞. 硬编码就是你点名的军规违反. LLM 语义判决替 regex.
 *
 *  截最后 30 条 + 每条限 300 字 → 控制 prompt 长度防 OOM.
 *  max_tokens 400 (~200 字, 留 JSON 结构 overhead), temperature 0.3 (准确为主).
 *  失败 (网络/LLM 非 2xx/parse 错) 返 {"", "pending"} — 不阻塞 advisor 主
 *  流程, pending 让 filter 保守放行 (不误 drop 员工真需要办的). */

// TaskChatStatus 定义在 advisor_cache.ts (colocated with TaskChatSummary cache
// schema). 这里从顶部 import 类型即可 — 不重复 export.

async function summarizeTaskChat(
  taskTitle: string,
  taskUid: string,
  messages: Array<{ role: string; content: string; ts: string }>,
  model: string,
): Promise<{ summary: string; status: TaskChatStatus }> {
  if (messages.length === 0) return { summary: "", status: "pending" };

  const lastN = messages.slice(-30);
  const dump = lastN
    .map((m) => {
      const role = m.role === "user" ? "员工" : m.role === "assistant" ? "AI" : m.role;
      const text = m.content.length > 300 ? m.content.slice(0, 300) + "…" : m.content;
      return `${role}: ${text}`;
    })
    .join("\n");

  const prompt = `以下是员工跟 catfish AI 在某条待办 "${taskTitle}" 上的最近对话.

请输出严格 JSON, 两个字段:

{
  "summary": "100-150 字客观描述员工**已经决定/已经做/已经说要做什么**. 不含 AI 建议或猜测, 只讲员工本人说过/做过/确认过的事.",
  "status": "resolved" | "paused" | "pending"
}

status 判定 (根据员工最近一次表态):
- "resolved" = 员工说事已办完/已结/已交付/已签字/已发出申请/已确认为误报/已作废/已撤销 等 — 事情已经完结, 不需要 catfish 再推
- "paused" = 员工说暂时关闭/暂缓/暂停/先放放/先不管/等通知再说/等回复 等 — 临时搁置, 不需要 catfish 主动推, 员工会主动来找
- "pending" = 球还在员工手里, 需要继续跟进 — 员工没说过完结/搁置的话

只输出 JSON, 不带 markdown 代码块 fence, 不带解释文本.

${dump}

JSON:`;

  try {
    const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content: prompt }],
        max_tokens: 400,
        temperature: 0.3,
        stream: false,
        response_format: { type: "json_object" },
      }),
    });
    if (!resp.ok) {
      console.warn(`[advisor summary] ${taskUid} 非 2xx:`, resp.status);
      return { summary: "", status: "pending" };
    }
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") return { summary: "", status: "pending" };

    // 兼容 LLM 偶尔用 markdown fence 包 JSON
    const raw = content.trim().replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```\s*$/i, "");
    let parsed: { summary?: unknown; status?: unknown };
    try {
      parsed = JSON.parse(raw);
    } catch (e) {
      // JSON parse fail → 尝试当作纯文本 summary, status pending
      console.warn(`[advisor summary] ${taskUid} JSON parse 失败 (LLM 输出可能非 JSON), fallback pending:`, e);
      return { summary: raw.slice(0, 400), status: "pending" };
    }

    const summary = typeof parsed.summary === "string" ? parsed.summary.trim().slice(0, 400) : "";
    const rawStatus = typeof parsed.status === "string" ? parsed.status.toLowerCase() : "";
    const status: TaskChatStatus =
      rawStatus === "resolved" || rawStatus === "paused" || rawStatus === "pending"
        ? (rawStatus as TaskChatStatus)
        : "pending"; // 兜底 pending — 不敢 drop 真需要办的事

    return { summary, status };
  } catch (e) {
    console.warn(`[advisor summary] ${taskUid} 异常:`, e);
    return { summary: "", status: "pending" };
  }
}
