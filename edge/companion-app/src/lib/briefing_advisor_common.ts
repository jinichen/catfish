/** 智能参谋 (早安) 的类型定义 + LLM 调用的公共常量。
 *
 * 8/15 从 briefing_advisor.ts 搬出来 (2281 行, 是仓里最大的 TS 文件,
 * 过了 CLAUDE.md §1 的 800 红线)。
 *
 * # 这一层是底, 它谁都不依赖
 *
 * prompts / parse / summaries 三个模块都往这儿依赖, 它只依赖 ./profile、
 * ./tauri、./advisor_cache 这些更外层的类型。保持单向, 不成环。
 *
 * # SERVICE_LLM_QUERY 为什么带这两个 query 参数 (5/21 CORS 修)
 *
 * 不带 X-Catfish-* header 而走 query, 是因为 hermes proxy 8642 的 CORS
 * allow-list 没配那些自定义 header, preflight 直接被拒。网关和 plugin 侧的
 * middleware 都是 header 优先、query 兜底, 所以 query 一样认。
 *
 * ⚠ `catfish_source=companion-advisor` 这个值是**跨仓约定**: 插件侧
 * plugin_memory_gate.py / plugin_service_lean.py 都按 source 分叉, 改这个字符串
 * 之前先去那两个文件确认白名单。
 */
import type { Profile } from "./profile";
import type {
  BriefingContext,
  CalendarEvent,
  EmailDigestItem,
  ReminderTodo,
} from "./tauri";
// P3.5.202 (C 方案): TaskChatStatus 从 advisor_cache colocated with TaskChatSummary.
import type { ManualTaskStatus, TaskChatStatus } from "./advisor_cache";
import { LLM_RACE_TIMEOUT_MS } from "./timeouts";

// 跟 briefing_workplan / briefing 同款 — 不带 X-Catfish-* header, 走 query param (5/21 CORS 修)

// 跟 briefing_workplan / briefing 同款 — 不带 X-Catfish-* header, 走 query param (5/21 CORS 修)
export const SERVICE_LLM_HEADERS = { "Content-Type": "application/json" };
export const SERVICE_LLM_QUERY = "?catfish_source=companion-advisor&catfish_skip_identity=1&catfish_internal=1";

// P3.4.E (6/15 鸿波): Call 2 transformToStructured 直走 catfish-gateway 8999 (LiteLLM passthrough),
//   bypass hermes 8642 agent loop. 真因: hermes _handle_chat_completions 不读 client tools / tool_choice
//   (api_server.py:1820 把 request 重 framing 成 agent run), 必须直 LiteLLM 才能用 strict function calling.
//   catfish_direct=1 query 让 me.ts isGatewayDirectPath 命中走 OAuth path (跟 profile.ts 同款).
export const ADVISOR_DIRECT_QUERY = "?catfish_source=companion-advisor-transform&catfish_skip_identity=1&catfish_internal=1&catfish_direct=1";

// ─── 输出 schema (UI ActionCard 渲染输入) ───────────────────────

/** BL-ADVISOR-PROMPT-CONFORMANCE (6/1 鸿波): tone 严格 enum, parseMainTask
 *  把非 enum 归一到 'balanced' + warn. decisions.jsonl 留档一致性靠这个保证. */
export const VALID_TONES = [
  "strict",
  "balanced",
  "friendly",
  "formal",
  "urgent",
  "hold",
] as const;
export type AdvisorTone = typeof VALID_TONES[number];

export interface AdvisorOption {
  /** "A" / "B" / "C" */
  label: string;
  /** enum 严格 (parseMainTask 校验 + 非 enum 归一到 balanced). */
  tone: AdvisorTone;
  /** 这个选项的一句话总结 (给员工选时用) */
  summary: string;
  /** LLM 倾向哪个 — 唯一一条 true. UI 角标"我倾向" */
  aiLean?: boolean;
  /** 已存草稿绝对路径 (调 draft_email_reply / draft_meeting_brief 后填) */
  draftPath?: string;
}

export interface ComplianceFlag {
  type: string;       // "iso_audit_relevant" 等
  severity: "high" | "medium" | "low";
  reason: string;
  matchedKeyword?: string;
  suggestion?: string;
}

export interface PoliticalFlag {
  type: string;
  severity: "high" | "medium" | "low";
  person?: string;
  reason: string;
  matchedKeyword?: string;
  suggestedPhrasings?: string[];
  advisoryOnly?: boolean;  // senior tier + high 时 true, UI 渲染"提醒"非"建议改"
}

export interface MainTask {
  /** 当天主菜 id (LLM 自己排 1, 2, 3...) */
  id: number;
  /** P3.3.9 (6/10): 跨 refresh 稳定 key, 6 字符 [a-z0-9]. LLM 生成 + 上次 cache 注入
   *  让 LLM 同业务复用旧 uid. task_chat / decisions 等持久化用此 key, 不用 title
   *  (title 每次 refresh LLM 会重写). */
  taskUid: string;
  /** 一句话标题 */
  title: string;
  /** "high" / "medium" / "low" — UI 边框颜色 */
  urgency: "high" | "medium" | "low";
  /** 为什么这件事在主菜 (一句话) */
  reason?: string;
  /** 2-3 个口径选项 (高层异常型主菜可以 0 选项, 只列例外) */
  options: AdvisorOption[];
  /** check_compliance 跑的结果 */
  complianceFlags: ComplianceFlag[];
  /** political_sensitivity_scan 跑的结果 */
  politicalFlags: PoliticalFlag[];
  /** 上下文引用 — UI 用来显示 "🔗 历史: ..." */
  contextRefs: string[];
}

export interface HandledSilentlyItem {
  /** "email_archive" / "calendar_accept" / "todo_dedup" */
  type: string;
  /** 多少件 */
  count: number;
  /** 一句话理由 */
  category: string;
}

/** P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection items.
 *
 * reflectPrompt 真≤15 字 点 → useChat send(prompt) 真触发 deep-dive**.
 */
export interface SubconsciousItem {
  /** 无意识 topic, e.g. "OAuth token refresh" */
  topic: string;
  /** session 涉及次数 */
  count: number;
  /** 一句话证据 — LLM 指 哪些 sessions title 真关键词** */
  evidence: string;
  /** ≤15 字 reflectPrompt, 点击 → chat 触发 deep-dive */
  reflectPrompt: string;
}
export interface GraveyardItem {
  /** skill/工具/项目名 */
  name: string;
  /** 最近 reference 距今 (e.g. "14 天前") */
  lastSeen: string;
  /** 一句话证据 (distilled_facts 提到 哪段) */
  evidence: string;
}
export interface BlindSpotItem {
  /** topic */
  topic: string;
  /** 重要信号 (e.g. "邮件标星 / memory 重要 / project 汇报截止") */
  signal: string;
  /** 一句话证据 */
  evidence: string;
  /** ≤15 字 reflectPrompt */
  reflectPrompt: string;
}

export interface AdvisorResult {
  /** 跟 profile.tier 一致 — LLM 输出时回显, 让 UI 知道是按哪个 tier 渲染的 */
  tier: "frontline" | "mid" | "senior";
  /** 主菜 list, 按 tier 决定数量 */
  mainTasks: MainTask[];
  /** 已默认处理的事 (UI 折叠区) */
  handledSilently: HandledSilentlyItem[];
  /** P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 无意识高频 topic, max 3 */
  subconscious?: SubconsciousItem[];
  /** P3.5.32 Phase 10 — 装但 0 回顾 skill/工具/项目, max 3 */
  graveyard?: GraveyardItem[];
  /** P3.5.32 Phase 10 — 标重要但 0 action / 0 follow-up topic, max 3 */
  blindSpots?: BlindSpotItem[];
}

// ─── 入口 fetchBriefingAdvisor ──────────────────────────────────

export interface AdvisorInput {
  profile: Profile;
  emails: EmailDigestItem[];
  events: CalendarEvent[];
  todos: ReminderTodo[];
  ctx: BriefingContext;
  urgencyMap: Record<string, string>;
  // sessionGoal 5/26 删 — hermes 0.14 原生 /goal 替代
  model: string;
  /** P3.3.9 (6/10): 上次 advisor 输出 (12h 内 cache), 让 LLM 复用 task_uid.
   *  P3.3.12 (6/10): 加 chatSummary — 员工跟这条 task 已聊过的 100 字 summary,
   *  让 advisor 看到员工已讨论过什么, 不再重推同样建议.
   *  fetchBriefingAdvisor 内部从 advisorCacheGet 拉, 不需要 caller 传. */
  previousTasks?: Array<{
    taskUid: string;
    title: string;
    urgency: string;
    /** P3.3.12: 跟 AI 已聊到哪 (100-150 字). 空字符串 = 没聊过 / summary 失败. */
    chatSummary?: string;
    /** P3.5.202 (C 方案 7/9): LLM 判 员工在这条 task 上的最新状态.
     *  用于 filterResolvedTasks 语义判 drop 而非 regex 关键字.
     *  老 cache 没这字段 → undefined → filter 兜底走 regex (backward compat). */
    chatStatus?: TaskChatStatus;
    /** P3.5.207 (7/9 鸿波 catch 早安卡片跟 chat 讨论修改不同步): 员工点
     *  "标记完成/推迟到明天/不做" 按钮存到 Rust backend 的 taskState. 之前
     *  filterResolvedTasks 只看 chatStatus, taskState 不参与 → 员工点了
     *  完成 briefing LLM 又推同一 task. 加这字段 + mergeTaskStatus 合并
     *  两路信号 (员工显式 action 优先, LLM chat 语义次之). */
    taskState?: ManualTaskStatus;
  }>;
  /** P3.5.40 (6/18 鸿波 audit huashu-design '不凭空创造, 查已有 spec'):
   *  跟今日邮件/任务相关的 wiki 条目 (entity/concept) head 拼接, 防 LLM 凭记忆造客户名/项目名/资质名.
   *  内部填充: advisor 跑前调 wikiSearchSemantic(query, top_k=3), 复用现有 BGE-M3 SQLite cache,
   *  不重 embed. 空字符串 = wiki 没数据 / 模型未装 / 没匹配, advisor 仍然能跑.
   *  Caller 不应该手动传, fetchBriefingAdvisor 内部填. optional 防 break 其他 caller 路径. */
  wikiRelevant?: string;
}

/** 5/22 鸿波实盘: 公司内网 Qwen 拥堵, TTFT 44s, 多次撞 gateway 300s timeout, UI 干等 5min.
 *  客户端 60s timeout sentinel — fetch 不 abort (避 5/21 Tauri webview suspend bug,
 *  fetch 完成后 cache 还会写, 下次时段触发能用), 但 fetchBriefingAdvisor 早返
 *  TIMEOUT, 调用方走 stale cache fallback. */
export const ADVISOR_TIMEOUT = Symbol("advisor-timeout");
export type AdvisorFetchResult = AdvisorResult | null | typeof ADVISOR_TIMEOUT;
// P3.3.25 (6/11): 60_000 → 180_000.
//   gateway log 看 catfish-private-main (40K prompt + 私有推理) latency 70-100s
//   常态. 60s race timeout 几乎每次都触发, 走 stale fallback, 但 stale 是 null →
//   红条 "LLM 返空或解析失败". 改 180s 给私有模型充分时间, 也保留 fail-safe.
//   仍然不挂 AbortSignal — Tauri webview suspend 时 fetch 自然 pending, race 让
//   UI 不无限等. 后台 fetch 完成会写 cache, 下次时段触发能用.
// P3.4.8 (6/15 鸿波): 180_000 → 300_000.
//   真因 (从 ~/.hermes/logs/agent.log 看): advisor 一次 POST /v1/chat/completions
//   不是单次 LLM call, 是 hermes 跑完整 agent session — 8 次 API call + 多次
//   tool 调用 (read_file / session_search 一次 76s / tool_search / recall_decision_history),
//   总耗时 ~2:40. 180s 客户端 timeout 擦边超出 60-90s, 几乎每次撞超时走 stale fallback.
//   改 300s 给 agent loop 跑完时间. stale fallback 仍保留 (真 5min 还没回就是上游真挂).
// P3.5.32.6 (6/18 鸿波 catch '完全卡死, 处理任务太多'): 300_000 → 600_000.
// 真因: 鸿波 picker = catfish-private-main (P3.5.29 Phase 6.3 catalog.default).
// gateway log 实测 profile 单 LLM call 116s, advisor hermes agent loop 多轮串行
// 5-10 min real. profile + advisor 累计 7-12 min, 300s timeout 全部走 stale cache.
// 600s 给内网模型留时间. 长期 fix (留 Phase 11): profile/advisor 脱钩 chat picker,
// 用专用 fast role (e.g. role rate_fast = qwen-flash, 5s 跑完).
// 8/8: 加 export —— AdvisorView 的超时文案原来硬编码 ">5min", 而这里早就是
// 600s 了 (P3.4.8 之后又调过一次)。两处各写各的, 结果界面上告诉员工"等 5 分钟",
// 实际要等 10 分钟。数字只该有一个来源。
// 8/10: 数值搬 timeouts.ts (单一来源)。上面那段演变史留着 —— 60→180→300→600
// 每一步都有实测依据, 是这个数为什么这么大的全部理由。
export const CLIENT_TIMEOUT_MS = LLM_RACE_TIMEOUT_MS;
