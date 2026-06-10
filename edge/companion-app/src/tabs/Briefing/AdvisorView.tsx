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

interface AdvisorViewProps {
  /** 父组件 (BriefingCard) 触发 refresh 时调本 props 后会 reload */
  refreshKey?: number;
}

export default function AdvisorView({ refreshKey = 0 }: AdvisorViewProps) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [result, setResult] = useState<AdvisorResult | null>(null);
  const [cacheInfo, setCacheInfo] = useState<{ computedAt: string; ageMin: number } | null>(null);
  const [config, setConfig] = useState<AdvisorConfig | null>(null);
  const [phase, setPhase] = useState<"booting" | "profile_loading" | "data_loading" | "llm_running" | "done" | "no_profile" | "error" | "cache_hit" | "stale_fallback">("booting");
  const [errorMsg, setErrorMsg] = useState<string>("");
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

        const emails = parseJsonList<EmailDigestItem>(emailRes);
        const events = parseJsonList<CalendarEvent>(eventsRes);
        const todos = parseJsonList<JournalTodo>(todosRes);
        const ctxValue =
          ctx.status === "fulfilled" ? ctx.value as BriefingContext : emptyCtx();

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

        // 5/22 上游拥堵 fallback: 60s 超时 → 拉 stale cache 撑场面 (即使过期)
        if (r === ADVISOR_TIMEOUT) {
          console.warn("[advisor] 60s 超时, 走 stale cache fallback");
          const stale = await advisorCacheGet();
          if (cancelled) return;
          if (stale) {
            setResult(stale.result);
            setCacheInfo({
              computedAt: stale.computedAt,
              ageMin: cacheAgeMinutes(stale),
            });
            setStaleNotice(
              `⚠️ 公司内网模型响应慢 (>60s), 显示上次结果. 后台仍在算, 完成会自动更新.`,
            );
            setPhase("stale_fallback");
          } else {
            setStaleNotice("");
            setErrorMsg(
              "公司内网模型响应慢 (>60s), 也没有历史缓存可显示. 等几分钟点刷新重试.",
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

  // phase === "done" / "cache_hit" / "stale_fallback"
  if (!result) {
    return (
      <Placeholder
        text="LLM 返空或解析失败. 数据有可能不够 (邮件/日历/TODO 全空), 或网络挂. 点刷新重试."
        error
      />
    );
  }

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      {/* 5/21 cold start 3: confidence 分层 UI 提示 */}
      {profile && <ConfidenceHint profile={profile} />}

      {/* 5/22 上游拥堵 fallback: 显示 stale cache 时顶上挂提示 */}
      {phase === "stale_fallback" && staleNotice && (
        <div
          style={{
            fontSize: 12,
            background: "rgba(251, 191, 36, 0.12)",
            border: "1px solid rgba(251, 191, 36, 0.35)",
            color: "#a16207",
            padding: "8px 12px",
            borderRadius: 6,
            marginBottom: 10,
          }}
        >
          {staleNotice}
        </div>
      )}

      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: 10,
          paddingLeft: 4,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span>
          鲶鱼参谋 · tier={result.tier}
          {phase === "cache_hit" && " · 缓存中"}
          {phase === "stale_fallback" && " · 显示历史结果"}
        </span>
        {cacheInfo && config && (
          <RefreshInfo
            cacheInfo={cacheInfo}
            refreshTimes={config.refreshTimes}
          />
        )}
      </div>

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
  const bg = isLow ? "rgba(146,64,14,0.08)" : "rgba(37,99,235,0.06)";
  const border = isLow ? "#92400e40" : "#2563eb40";
  const color = isLow ? "#92400e" : "#1e3a8a";
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

function parseJsonList<T>(res: PromiseSettledResult<string>): T[] {
  if (res.status !== "fulfilled") return [];
  try {
    const arr = JSON.parse(res.value);
    return Array.isArray(arr) ? (arr as T[]) : [];
  } catch {
    return [];
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
  };
}
