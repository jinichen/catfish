/** BL-ADVISOR-UI (5/21 Phase 7 第 6 步): 智能参谋早安主菜列表 — 顶层容器.
 *
 * 替代 Phase 6 WorkplanView (已砍). 角色:
 *   1. 拉 profile (~/.catfish/profile.json) — 没有/占位 (confidence=0) 显引导
 *   2. profile 真识别后, 并发拉 7 数据源 + emails/events/todos
 *   3. 调 fetchBriefingAdvisor → AdvisorResult
 *   4. 渲染主菜 list (ActionCard) + 已默认处理 (HandledSilently)
 *
 * 启动顺序对 5/21 鸿波"不切信息"约束: 一次拉全数据, 一次喂 LLM, 不分块.
 */

import { useEffect, useRef, useState } from "react";

import {
  advisorCacheGet,
  advisorCacheSave,
  advisorConfigGet,
  advisorTaskStateGet,
  advisorTaskStatePruneOld,
  cacheAgeMinutes,
  didCrossRefreshTime,
  isCacheFresh,
  nextRefreshAfter,
  type AdvisorCache,
  type AdvisorConfig,
  type TaskStateFetch,
} from "../../lib/advisor_cache";
import {
  ADVISOR_TIMEOUT,
  ensureTaskChatSummariesFresh,
  fetchBriefingAdvisor,
  type AdvisorResult,
} from "../../lib/briefing_advisor";
import { ensureRecomputed, type Profile } from "../../lib/profile";
import {
  briefingContextFetch,
  calendarTodayFetch,
  emailDigestFetch,
  journalTodosFetch,
  type BriefingContext,
  type CalendarEvent,
  type EmailDigestItem,
  type JournalTodo,
} from "../../lib/tauri";
import { useChatStore } from "../../store/chat";
import { useEmailStore } from "../../store/email";

import BriefingTwoColumnView from "./components/BriefingTwoColumnView";  // P3.3.6 (6/10): 左右两栏 layout
import { DataDiagnosisCard } from "./components/DataDiagnosisCard";  // P3.4.4 (6/15): 三件套全空诊断卡, 替换老 "LLM 返空" 红字
import type { SourceStatus } from "./diagnosis_types";

interface AdvisorViewProps {
  /** 父组件 (BriefingCard) 触发 refresh 时调本 props 后会 reload */
  refreshKey?: number;
}

export default function AdvisorView({ refreshKey = 0 }: AdvisorViewProps) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [result, setResult] = useState<AdvisorResult | null>(null);
  const [cacheInfo, setCacheInfo] = useState<{ computedAt: string; ageMin: number } | null>(null);
  const [config, setConfig] = useState<AdvisorConfig | null>(null);
  const [phase, setPhase] = useState<"booting" | "profile_loading" | "data_loading" | "llm_running" | "done" | "no_profile" | "no_data" | "error" | "cache_hit" | "stale_fallback">("booting");
  const [errorMsg, setErrorMsg] = useState<string>("");
  // P3.4.4 (6/15 鸿波): 三件套各自拉取状态, no_data 阶段渲染诊断卡用.
  //   parseJsonList 老 helper 把失败原因吞成 [], 改 parseJsonListWithDiagnosis 保留 reason.
  const [sourceStatuses, setSourceStatuses] = useState<{
    emails: SourceStatus;
    events: SourceStatus;
    todos: SourceStatus;
  } | null>(null);
  /** 5/22 上游拥堵 fallback: TIMEOUT 时显的灰条提示 */
  const [staleNotice, setStaleNotice] = useState<string>("");
  /** 5/22 鸿波: 任务状态 (key = task.title → status). 启动时从后端拉. */
  const [taskState, setTaskState] = useState<TaskStateFetch>({
    today: {},
    yesterdaySnoozed: [],
  });

  // 5/22 鸿波: 启动时拉今日任务状态 + 清旧 (>7d)
  useEffect(() => {
    void advisorTaskStateGet().then(setTaskState).catch((e) => {
      console.warn("[AdvisorView] task state 拉取挂:", e);
    });
    void advisorTaskStatePruneOld().catch(() => {});  // 7d 前的旧记录清掉, 失败无所谓
  }, [refreshKey]);

  const urgencyMap = useEmailStore((s) => s.urgencyMap);
  const model = useChatStore((s) => s.model);

  // P3.3.12.1 (6/10): mount / refresh 时后台 ensure 每条 mainTask 的 task chat
  //   summary 是最新的 (jsonl size 没变跳, 变了重跑 LLM 写 cache). 跟 advisor
  //   主 LLM call 解耦 — cache 命中场景也能更新 summary. 异步不阻塞 UI.
  useEffect(() => {
    void ensureTaskChatSummariesFresh(model).catch((e) => {
      console.warn("[AdvisorView] ensure summary 异常 (不阻塞 UI):", e);
    });
  }, [refreshKey, model]);

  // 5/22 时段触发: 拉 yaml 配置 (refresh_times + cache_max_age_minutes)
  useEffect(() => {
    void advisorConfigGet().then(setConfig).catch(() => {
      // yaml 读挂了用默认值 (Rust 端 advisor_config_get 已 fallback)
    });
  }, []);

  useEffect(() => {
    let cancelled = false;

    // refreshKey > 0 = 员工点刷新, 强制重算 + 跳缓存
    const isManualRefresh = refreshKey > 0;

    void (async () => {
      try {
        // ─── 1. cache 优先 (非手动刷新时) ───
        if (!isManualRefresh && config) {
          const cached = await advisorCacheGet();
          if (cancelled) return;
          if (cached && isCacheFresh(cached, config.cacheMaxAgeMinutes)) {
            console.log("[advisor] cache 命中, 跳过 LLM 调用",
              { ageMin: cacheAgeMinutes(cached).toFixed(1) });
            setResult(cached.result);
            setCacheInfo({
              computedAt: cached.computedAt,
              ageMin: cacheAgeMinutes(cached),
            });
            setPhase("cache_hit");
            // profile 也读一下让 UI 显示 (ConfidenceHint)
            const { profileGet } = await import("../../lib/profile");
            const p = await profileGet();
            if (!cancelled && p) setProfile(p);
            return;
          }
        }

        // ─── 2. profile — 同步等 recompute (in-flight 锁防 StrictMode 双调) ───
        setPhase("profile_loading");
        const p = await ensureRecomputed(model, isManualRefresh);
        if (cancelled) return;

        if (!p || p.confidence < 0.3) {
          setProfile(p);
          setPhase("no_profile");
          return;
        }
        setProfile(p);

        // ─── 3. 并发拉数据 ───
        setPhase("data_loading");
        const [emailRes, eventsRes, todosRes, ctx] = await Promise.allSettled([
          emailDigestFetch(50),
          calendarTodayFetch(false),
          journalTodosFetch(),
          briefingContextFetch(),
        ]);
        if (cancelled) return;

        // P3.4.4 (6/15 鸿波): 用 parseJsonListWithDiagnosis 保留每个 source
        //   真实失败原因 (Tauri rejected reason / JSON parse err), 给诊断卡用.
        //   老 parseJsonList 把 rejected/无效都吞成 [], reason 全丢, calendar.rs
        //   :451 那段精彩"仅添加访问权限...必须 Cmd+Q 重启" 文案推不到 UI.
        const emailDiag = parseJsonListWithDiagnosis<EmailDigestItem>(emailRes);
        const eventsDiag = parseJsonListWithDiagnosis<CalendarEvent>(eventsRes);
        const todosDiag = parseJsonListWithDiagnosis<JournalTodo>(todosRes);
        const ctxValue =
          ctx.status === "fulfilled" ? ctx.value as BriefingContext : emptyCtx();

        const statuses = {
          emails: { ok: emailDiag.ok, count: emailDiag.items.length, reason: emailDiag.reason },
          events: { ok: eventsDiag.ok, count: eventsDiag.items.length, reason: eventsDiag.reason },
          todos:  { ok: todosDiag.ok,  count: todosDiag.items.length,  reason: todosDiag.reason  },
        };
        setSourceStatuses(statuses);

        // P3.4.4 (6/15 鸿波): 三件套全空 → 不调 advisor LLM (briefing_advisor.ts:533
        //   也有同款 short-circuit, 但走到那再返 null UI 只能渲老红字 lying 文案
        //   "LLM 返空或解析失败". 改: 这里直接 short-circuit 走 no_data, 渲诊断卡
        //   显每个 source 真状态.
        const totalCount = emailDiag.items.length + eventsDiag.items.length + todosDiag.items.length;
        if (totalCount === 0) {
          console.log("[advisor] 三件套全空, 跳 LLM, 走数据诊断卡", statuses);
          setPhase("no_data");
          return;
        }

        const emails = emailDiag.items;
        const events = eventsDiag.items;
        const todos = todosDiag.items;

        // ─── 4. 调 LLM (advisor) — 60s 客户端超时, 超时走 stale cache fallback ───
        setPhase("llm_running");
        const r = await fetchBriefingAdvisor({
          profile: p,
          emails,
          events,
          todos,
          ctx: ctxValue,
          urgencyMap,
          // sessionGoal 5/26 删 — hermes 0.14 原生 /goal 替代
          model,
        });
        if (cancelled) return;

        // 5/22 上游拥堵 fallback: 客户端 timeout → 拉 stale cache 撑场面 (即使过期).
        // P3.3.25 (6/11): 60s → 180s; P3.4.8 (6/15 鸿波): 180s → 300s (5min,
        //   因为 advisor 走 hermes agent loop 多轮, 实际跑完 ~2-3min, 见
        //   briefing_advisor.ts:CLIENT_TIMEOUT_MS 注释).
        if (r === ADVISOR_TIMEOUT) {
          console.warn("[advisor] 客户端 timeout, 走 stale cache fallback");
          const stale = await advisorCacheGet();
          if (cancelled) return;
          if (stale) {
            setResult(stale.result);
            setCacheInfo({
              computedAt: stale.computedAt,
              ageMin: cacheAgeMinutes(stale),
            });
            setStaleNotice(
              `⚠️ 公司内网模型响应慢 (>5min), 显示上次结果. 后台仍在算, 完成会自动更新.`,
            );
            setPhase("stale_fallback");
          } else {
            setStaleNotice("");
            setErrorMsg(
              "公司内网模型响应慢 (>5min), 也没有历史缓存可显示. 等几分钟点刷新重试.",
            );
            setPhase("error");
          }
          return;
        }

        setResult(r);

        // ─── 5. 写 cache (briefing_advisor 后台也会写一份, 这里走 happy path) ───
        if (r) {
          const newCache: AdvisorCache = {
            computedAt: new Date().toISOString(),
            result: r,
            model,
          };
          await advisorCacheSave(newCache).catch((e) =>
            console.warn("[advisor] cache 写入失败 (不影响显示):", e),
          );
          setCacheInfo({ computedAt: newCache.computedAt, ageMin: 0 });
        }
        setPhase("done");
      } catch (e) {
        if (cancelled) return;
        setErrorMsg(e instanceof Error ? e.message : String(e));
        setPhase("error");
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, config?.cacheMaxAgeMinutes]);

  // ─── 后台时段触发: setInterval 每分钟检查 now 跨时段就 setRefreshKey 触发刷新 ───
  const tickRef = useRef<Date>(new Date());
  useEffect(() => {
    if (!config || config.refreshTimes.length === 0) return;
    const timer = window.setInterval(() => {
      const prev = tickRef.current;
      const now = new Date();
      tickRef.current = now;
      if (didCrossRefreshTime(prev, now, config.refreshTimes)) {
        console.log("[advisor] 时段触发后台刷新", { time: now.toLocaleTimeString() });
        // 通过设置一个内部触发让 useEffect 重跑 — 但 refreshKey 是 props 不能直接改
        // 改用: 直接清缓存 + 再 mount 时会重新拉
        void (async () => {
          try {
            const { advisorCacheClear } = await import("../../lib/advisor_cache");
            await advisorCacheClear();
            // 再触发一次 useEffect: 用一个 state 切换让依赖变化
            setConfig((c) => (c ? { ...c } : c));  // shallow copy 触发 useEffect 重跑
          } catch (e) {
            console.warn("[advisor] 时段刷新失败:", e);
          }
        })();
      }
    }, 60_000);  // 每分钟检查一次
    return () => window.clearInterval(timer);
  }, [config]);

  // ─── 渲染 ────────────────────────────────────────────────────

  if (phase === "booting" || phase === "profile_loading") {
    return <Placeholder text="正在识别员工画像..." />;
  }

  if (phase === "no_profile") {
    return (
      <NoProfileNotice
        hasProfileButZero={profile !== null && profile.confidence === 0}
      />
    );
  }

  if (phase === "data_loading" || phase === "llm_running") {
    return (
      <Placeholder
        text={
          phase === "data_loading"
            ? "正在拉取邮件 / 日历 / TODO / 历史..."
            : "鲶鱼正在综合判断 (LLM 调用 + 起草草稿)..."
        }
      />
    );
  }

  if (phase === "error") {
    return <Placeholder text={`出错了: ${errorMsg}`} error />;
  }

  // P3.4.4 (6/15 鸿波): 三件套全空走诊断卡, 替换老 "LLM 返空" 红字 lying 文案
  //   (LLM 一次没调, 是数据全空 short-circuit). DataDiagnosisCard 显每个 source
  //   独立状态 + 修复指引 (calendar.rs:451 错误文案推到 UI).
  if (phase === "no_data" && sourceStatuses) {
    return (
      <DataDiagnosisCard
        statuses={sourceStatuses}
        onRetry={() => {
          // 重新触发 mount effect — 跟父组件 refresh 同款语义
          setPhase("booting");
          setSourceStatuses(null);
        }}
      />
    );
  }

  // phase === "done" / "cache_hit" / "stale_fallback"
  // 这里 !result 是 LLM 真返 null (LLM 调用失败 / 解析失败), 不是数据全空 —
  // 数据全空已经在 phase === "no_data" 分支接掉了 (P3.4.4).
  if (!result) {
    return (
      <Placeholder
        text="advisor LLM 调用失败 (网络挂 / 模型解析返非预期结构). 点刷新重试."
        error
      />
    );
  }

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      {/* 5/21 cold start 3: confidence 分层 UI 提示 */}
      {profile && <ConfidenceHint profile={profile} />}

      {/* 5/22 上游拥堵 fallback: 显示 stale cache 时顶上挂提示
       *  P3.4.E.9 (6/15 鸿波): 跟 ConfidenceHint 同源 hardcode hex 问题, 用 hint-amber var.
       *  老 #a16207 深琥珀在暗模式不可读. tokens.css --catfish-hint-amber-* 双模式定义. */}
      {phase === "stale_fallback" && staleNotice && (
        <div
          style={{
            fontSize: 12,
            background: "var(--catfish-hint-amber-bg)",
            border: "1px solid var(--catfish-hint-amber-border)",
            color: "var(--catfish-hint-amber-text)",
            padding: "8px 12px",
            borderRadius: 6,
            marginBottom: 10,
          }}
        >
          {staleNotice}
        </div>
      )}

      {/* P3.5.17.c.3 (6/17 鸿波): "鲶鱼参谋 · tier=..." inline 砍 — tier 是
          profile 内部分级 (frontline/mid/senior 影响 advisor 语气), UI 显是
          debug 痕迹无意义. "缓存中" / "显示历史结果" 也砍 — stale_fallback
          有独立 staleNotice warning channel (line 358-366), 不依赖 inline.
          只留 RefreshInfo (上次刷新时间 + 下次自动刷新), 真有用. */}
      {cacheInfo && config && (
        <div
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            marginBottom: 10,
            paddingLeft: 4,
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "baseline",
          }}
        >
          <RefreshInfo
            cacheInfo={cacheInfo}
            refreshTimes={config.refreshTimes}
          />
        </div>
      )}

      {(() => {
        // P3.3.6 (6/10): 平铺 ActionCard → 左右两栏 layout
        // 过滤 ignored 的 (今天不再出); done/snoozed 仍在 sidebar 显示 (变灰)
        const visibleTasks = result.mainTasks.filter((t) => {
          const s = taskState.today[t.title]?.status;
          return s !== "ignored";
        });
        return (
          <BriefingTwoColumnView
            tasks={visibleTasks}
            handledItems={result.handledSilently}
            taskState={taskState}
            wasSnoozedYesterday={(title) => taskState.yesterdaySnoozed.includes(title)}
            onStatusChange={(title, newStatus) => {
              // 切状态后本地立刻反映 (不等下次 refresh)
              setTaskState((prev) => {
                const today = { ...prev.today };
                if (newStatus === null) {
                  delete today[title];
                } else {
                  today[title] = {
                    status: newStatus,
                    ts: new Date().toISOString(),
                  };
                }
                return { ...prev, today };
              });
            }}
          />
        );
      })()}
    </div>
  );
}

/** 5/22 cold start v3: 显示缓存时间 + 下次自动刷新时间 (yaml 配置). */
function RefreshInfo({
  cacheInfo,
  refreshTimes,
}: {
  cacheInfo: { computedAt: string; ageMin: number };
  refreshTimes: string[];
}) {
  const computedDate = new Date(cacheInfo.computedAt);
  const computedHHMM = computedDate.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const next = nextRefreshAfter(new Date(), refreshTimes);
  const nextHHMM = next
    ? next.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "明天 " + (refreshTimes[0] ?? "08:30");

  const ageMin = Math.round(cacheInfo.ageMin);
  const ageLabel =
    ageMin < 1
      ? "刚刚"
      : ageMin < 60
      ? `${ageMin} 分钟前`
      : `${(ageMin / 60).toFixed(1)} 小时前`;

  return (
    <span style={{ fontSize: 10, opacity: 0.75 }}>
      上次 {computedHHMM} ({ageLabel}) · 下次自动 {nextHHMM}
    </span>
  );
}

/** 5/21 cold start 3: confidence 分层 UI 提示.
 *
 *   - < 0.3: "🪴 刚认识你, 默认按通用版渲染. 多用几天会更准."
 *   - 0.3-0.7: "📊 画像中. 已识别: tier / style / N 关键人 / N 项目."
 *   - > 0.7: 无提示, 全功能 (顶上"鲶鱼参谋 · tier=..." 一行就够)
 */
function ConfidenceHint({ profile }: { profile: Profile }) {
  const c = profile.confidence;
  if (c >= 0.7) return null;  // 高置信度无提示

  const isLow = c < 0.3;
  // P3.4.E.9 (6/15 鸿波): 用 tokens.css 双模式 hint CSS var, 替换老 hardcode hex.
  //   老 #1e3a8a 深蓝 / #92400e 深棕在暗模式 (--catfish-bg #131C1F) 上看不清.
  //   tokens.css :root + @media dark 各定义一套 hint-blue / hint-amber, 暗模式自动提亮.
  const bg = isLow ? "var(--catfish-hint-amber-bg)" : "var(--catfish-hint-blue-bg)";
  const border = isLow ? "var(--catfish-hint-amber-border)" : "var(--catfish-hint-blue-border)";
  const color = isLow ? "var(--catfish-hint-amber-text)" : "var(--catfish-hint-blue-text)";
  const icon = isLow ? "🪴" : "📊";
  const title = isLow ? "刚认识你" : "画像中";
  const body = isLow
    ? "默认按通用版 (中层) 渲染. 多用几天 catfish (写邮件 / 跟我聊几次) 会自动校准更准."
    : `已识别: ${profile.tier} / ${profile.style} / ${profile.keyPeople.length} 关键人 / ${profile.keyProjects.length} 项目. 置信度 ${(c * 100).toFixed(0)}%, 继续用会更准.`;

  return (
    <div
      style={{
        marginBottom: 12,
        padding: "8px 12px",
        background: bg,
        border: `1px solid ${border}`,
        borderRadius: "var(--radius-sm)",
        fontSize: 12,
        color,
        lineHeight: 1.55,
      }}
    >
      <strong style={{ marginRight: 6 }}>
        {icon} {title}
      </strong>
      {body}
    </div>
  );
}

// ─── 子组件 ──────────────────────────────────────────────────────

function Placeholder({ text, error = false }: { text: string; error?: boolean }) {
  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "20px 18px",
        color: error ? "#dc2626" : "var(--catfish-text-muted)",
        fontSize: 13,
        fontStyle: error ? "normal" : "italic",
        textAlign: "center",
        background: "var(--catfish-bg)",
        borderRadius: "var(--radius-sm)",
        border: `1px dashed ${error ? "#dc262640" : "var(--catfish-border)"}`,
      }}
    >
      {text}
    </div>
  );
}

function NoProfileNotice({ hasProfileButZero }: { hasProfileButZero: boolean }) {
  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "20px 18px",
        background: "var(--catfish-bg)",
        borderRadius: "var(--radius-sm)",
        border: "1px dashed var(--catfish-border)",
        fontSize: 13,
        lineHeight: 1.7,
        color: "var(--catfish-text-muted)",
      }}
    >
      <div style={{ fontSize: 14, color: "var(--catfish-text)", marginBottom: 8 }}>
        员工画像识别中
      </div>
      <div>
        {hasProfileButZero
          ? "已生成画像占位 (Phase 7 第 1 步), 真 LLM 推断待第 4 步集成后启用."
          : "首次启动, catfish 后台正在分析员工历史数据生成画像."}
      </div>
      <div style={{ marginTop: 8, fontSize: 12, opacity: 0.85 }}>
        识别完后才会出现主菜 + 选项 + 草稿. 请稍候或刷新.
      </div>
    </div>
  );
}

// ─── helpers ─────────────────────────────────────────────────────

/** P3.4.4 (6/15 鸿波): 解析 Promise.allSettled 单个 source 结果, 保留失败原因
 *  (Tauri reject / JSON parse err / 非数组). DataDiagnosisCard 拿这条 reason
 *  匹配指引文案 (e.g. calendar.rs:451 "仅添加访问权限...必须 Cmd+Q 重启" 直接
 *  推到 UI 让员工照做).
 *
 *  替换 P3.4.4 前的 parseJsonList<T>(res) — 那版把失败 / 非数组都吞成 [],
 *  reason 全丢. 6/15 鸿波早安卡 console 看不到真错因, 排错绕一通弯路.
 */
function parseJsonListWithDiagnosis<T>(
  res: PromiseSettledResult<string>,
): { items: T[]; ok: boolean; reason?: string } {
  if (res.status === "rejected") {
    return { items: [], ok: false, reason: String(res.reason) };
  }
  try {
    const arr = JSON.parse(res.value);
    if (!Array.isArray(arr)) {
      return { items: [], ok: false, reason: `返回不是 JSON 数组: ${res.value.slice(0, 200)}` };
    }
    return { items: arr as T[], ok: true };
  } catch (e) {
    return { items: [], ok: false, reason: `JSON 解析失败: ${e}` };
  }
}

function emptyCtx(): BriefingContext {
  return {
    distilledFacts: "",
    recentSessionBriefs: [],
    // sessionGoal 5/26 删
    workplan: "",
    projects: "",
    weeklyReports: [],
    hermesMemoryRecent: "",  // P3.4.6 (6/15 鸿波)
  };
}
